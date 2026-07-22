import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[2]
E2E_COMPOSE_PATH = ROOT / "tests/e2e/compose.yml"
E2E_RUNNER_PATH = ROOT / "tests/e2e/run.sh"
E2E_HELPER_RESOURCE_LIMITS = {
    "e2e-seed": {"cpus": 1.0, "mem_limit": "1073741824", "pids_limit": 128},
    "e2e-migrate": {"cpus": 0.5, "mem_limit": "536870912", "pids_limit": 128},
    "e2e-support": {"cpus": 0.5, "mem_limit": "536870912", "pids_limit": 128},
}


class ComposeLoader(yaml.SafeLoader):
    """Read Compose sequence tags as their underlying values for assertions."""


ComposeLoader.add_constructor(
    "!reset",
    lambda loader, node: loader.construct_sequence(node),
)
ComposeLoader.add_constructor(
    "!override",
    lambda loader, node: loader.construct_sequence(node),
)


def load_e2e_compose() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        yaml.load(E2E_COMPOSE_PATH.read_text(encoding="utf-8"), Loader=ComposeLoader),
    )


def load_rendered_e2e_compose() -> dict[str, Any]:
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "docker-compose.yml"),
            "-f",
            str(E2E_COMPOSE_PATH),
            "--profile",
            "e2e",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(dict[str, Any], json.loads(completed.stdout))


def test_e2e_profile_uses_only_private_deterministic_upstreams() -> None:
    compose = load_e2e_compose()
    services = compose["services"]
    master_environment = services["master-agent"]["environment"]

    assert master_environment["LLM_BASE_URL"] == "http://e2e-support:8999/v1"
    assert master_environment["PUBLISHER_AGENT_BASE_URL"] == "http://e2e-support:8999"
    assert master_environment["AMAP_WEB_SERVICE_KEY"] == ""
    assert services["web"]["environment"]["VITE_AMAP_JS_API_KEY"] == ""
    assert services["web"]["environment"]["AMAP_JS_API_SECURITY_CODE"] == ""
    assert master_environment["DATA_MANIFEST_PATH"] == "/e2e/data/manifest.json"
    assert services["master-agent"]["command"][1] == "tests.e2e.master:app"
    assert services["web"]["ports"] == []
    assert services["master-agent"]["ports"] == []
    assert services["publisher-agent"]["ports"] == []
    assert services["e2e-support"]["networks"] == ["private"]
    assert services["e2e-support"]["depends_on"]["publisher-agent"] == {
        "condition": "service_healthy"
    }
    assert "ports" not in services["e2e-support"]
    assert services["e2e-seed"]["volumes"] == [
        {"type": "volume", "source": "e2e-data", "target": "/e2e"}
    ]
    assert set(compose["volumes"]) == {"e2e-data"}

    serialized = E2E_COMPOSE_PATH.read_text(encoding="utf-8")
    assert "restapi.amap.com" not in serialized
    assert "sk-" not in serialized


def test_all_data_consumers_wait_for_the_seeded_approved_manifest() -> None:
    services = load_e2e_compose()["services"]

    for service_name in (
        "master-agent",
        "data-agent",
        "analysis-agent",
        "quality-agent",
        "publisher-agent",
    ):
        service = services[service_name]
        assert service["environment"]["DATA_MANIFEST_PATH"] == "/e2e/data/manifest.json"
        assert service["depends_on"]["e2e-seed"] == {"condition": "service_completed_successfully"}


def test_e2e_helper_containers_have_bounded_cpu_memory_and_pids() -> None:
    services = load_rendered_e2e_compose()["services"]

    for service_name, expected in E2E_HELPER_RESOURCE_LIMITS.items():
        service = services[service_name]
        assert {key: service[key] for key in expected} == expected


def test_rendered_e2e_stack_uses_one_internal_network() -> None:
    compose = load_rendered_e2e_compose()
    services = compose["services"]

    assert compose["networks"]["private"]["internal"] is True
    for service in services.values():
        assert set(service["networks"]) == {"private"}


def test_e2e_runner_rebuilds_waits_runs_and_preserves_failure_logs() -> None:
    runner = E2E_RUNNER_PATH.read_text(encoding="utf-8")

    assert os.access(E2E_RUNNER_PATH, os.X_OK)
    assert "docker compose" in runner
    assert "build master-agent web postgis e2e" in runner
    assert "up -d --wait --remove-orphans" in runner
    assert "run --rm e2e" in runner
    assert "test-results/compose.log" in runner
