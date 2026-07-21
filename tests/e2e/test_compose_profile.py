from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[2]
E2E_COMPOSE_PATH = ROOT / "tests/e2e/compose.yml"


class ComposeLoader(yaml.SafeLoader):
    """Read Compose reset tags as their underlying YAML values for assertions."""


ComposeLoader.add_constructor(
    "!reset",
    lambda loader, node: loader.construct_sequence(node),
)


def load_e2e_compose() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        yaml.load(E2E_COMPOSE_PATH.read_text(encoding="utf-8"), Loader=ComposeLoader),
    )


def test_e2e_profile_uses_only_private_deterministic_upstreams() -> None:
    compose = load_e2e_compose()
    services = compose["services"]
    master_environment = services["master-agent"]["environment"]

    assert master_environment["LLM_BASE_URL"] == "http://e2e-support:8999/v1"
    assert master_environment["PUBLISHER_AGENT_BASE_URL"] == "http://e2e-support:8999"
    assert master_environment["AMAP_WEB_SERVICE_KEY"] == ""
    assert master_environment["DATA_MANIFEST_PATH"] == "/e2e/data/manifest.json"
    assert services["master-agent"]["command"][1] == "tests.e2e.master:app"
    assert services["web"]["ports"] == []
    assert services["master-agent"]["ports"] == []
    assert services["publisher-agent"]["ports"] == []
    assert services["e2e-support"]["networks"] == ["private"]
    assert services["e2e-support"]["depends_on"]["publisher-agent"] == {
        "condition": "service_healthy"
    }
    assert services["e2e"]["networks"] == ["private"]
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
