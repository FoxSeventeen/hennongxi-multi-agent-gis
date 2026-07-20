from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker-compose.yml"

AGENT_SERVICES = {
    "master-agent",
    "data-agent",
    "analysis-agent",
    "quality-agent",
    "publisher-agent",
}
ALL_SERVICES = AGENT_SERVICES | {"web", "postgis", "redis"}


def load_compose() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8")),
    )


def volume_mounts(service: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    mounts: dict[tuple[str, str], dict[str, Any]] = {}
    for mount in service.get("volumes", []):
        assert isinstance(mount, dict), "Use long volume syntax so access modes are explicit"
        mounts[(mount["source"], mount["target"])] = mount
    return mounts


def test_compose_declares_the_complete_runtime_topology() -> None:
    compose = load_compose()

    assert set(compose["services"]) == ALL_SERVICES
    assert set(compose["volumes"]) == {
        "postgres-data",
        "redis-data",
        "data-cache",
        "artifacts",
        "quality-reports",
    }
    assert compose["networks"]["private"]["internal"] is True


def test_only_user_facing_services_publish_loopback_ports() -> None:
    services = load_compose()["services"]

    assert services["web"]["ports"] == ["127.0.0.1:3000:3000"]
    assert services["master-agent"]["ports"] == ["127.0.0.1:8000:8000"]
    assert services["publisher-agent"]["ports"] == ["127.0.0.1:8004:8004"]

    for name in {"data-agent", "analysis-agent", "quality-agent", "postgis", "redis"}:
        assert "ports" not in services[name]


def test_private_services_are_isolated_from_the_public_network() -> None:
    services = load_compose()["services"]

    assert set(services["web"]["networks"]) == {"public"}
    assert set(services["master-agent"]["networks"]) == {"public", "private"}
    assert set(services["publisher-agent"]["networks"]) == {"public", "private"}

    for name in {"data-agent", "analysis-agent", "quality-agent", "postgis", "redis"}:
        assert set(services[name]["networks"]) == {"private"}


def test_shared_storage_has_least_privilege_access_modes() -> None:
    services = load_compose()["services"]

    data_mounts = volume_mounts(services["data-agent"])
    analysis_mounts = volume_mounts(services["analysis-agent"])
    quality_mounts = volume_mounts(services["quality-agent"])
    publisher_mounts = volume_mounts(services["publisher-agent"])

    assert data_mounts[("data-cache", "/data/cache")].get("read_only", False) is False
    assert analysis_mounts[("data-cache", "/data/cache")]["read_only"] is True
    assert analysis_mounts[("artifacts", "/data/outputs")].get("read_only", False) is False
    assert quality_mounts[("artifacts", "/data/outputs")]["read_only"] is True
    assert (
        quality_mounts[("quality-reports", "/data/quality-reports")].get("read_only", False)
        is False
    )
    assert publisher_mounts[("artifacts", "/data/outputs")].get("read_only", False) is False
    assert publisher_mounts[("quality-reports", "/data/quality-reports")]["read_only"] is True

    assert (
        volume_mounts(services["postgis"])[("postgres-data", "/var/lib/postgresql/data")].get(
            "read_only", False
        )
        is False
    )
    assert (
        volume_mounts(services["redis"])[("redis-data", "/data")].get("read_only", False) is False
    )

    assert "volumes" not in services["master-agent"]


def test_publisher_uses_the_same_approved_data_manifest_as_upstream_agents() -> None:
    services = load_compose()["services"]
    expected = "${DATA_MANIFEST_PATH:-/app/data/manifest.json}"

    for service_name in ("data-agent", "analysis-agent", "quality-agent", "publisher-agent"):
        assert services[service_name]["environment"]["DATA_MANIFEST_PATH"] == expected


def test_dependencies_wait_for_healthy_services_without_cycles() -> None:
    services = load_compose()["services"]

    expected_master_dependencies = {
        "data-agent",
        "analysis-agent",
        "quality-agent",
        "publisher-agent",
        "postgis",
        "redis",
    }
    assert set(services["master-agent"]["depends_on"]) == expected_master_dependencies
    assert set(services["web"]["depends_on"]) == {"master-agent", "publisher-agent"}

    for service_name in {"master-agent", "web"}:
        dependencies = services[service_name]["depends_on"]
        assert all(
            dependency["condition"] == "service_healthy" for dependency in dependencies.values()
        )

    assert all("healthcheck" in service for service in services.values())


def test_application_containers_are_non_privileged_and_read_only() -> None:
    services = load_compose()["services"]

    for name in AGENT_SERVICES:
        service = services[name]
        assert service["read_only"] is True
        assert service.get("privileged", False) is False
        assert service["tmpfs"] == ["/tmp"]

    serialized = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "docker.sock" not in serialized


def test_images_are_pinned_and_default_to_arm64() -> None:
    compose = load_compose()

    for service in compose["services"].values():
        assert "linux/arm64" in service["platform"]
        assert "linux/amd64" not in service["platform"]
        assert not str(service.get("image", "")).endswith(":latest")

    dockerfiles = {
        "backend": ROOT / "infra/docker/backend.Dockerfile",
        "web": ROOT / "apps/web/Dockerfile",
        "postgis": ROOT / "infra/db/postgis/Dockerfile",
    }
    contents = {name: path.read_text(encoding="utf-8") for name, path in dockerfiles.items()}

    assert "python:3.12.13-slim-bookworm" in contents["backend"]
    assert "ghcr.io/astral-sh/uv:0.11.29" in contents["backend"]
    assert "node:24.18.0-bookworm-slim" in contents["web"]
    assert "postgres:17.10-bookworm" in contents["postgis"]
    assert all(":latest" not in content for content in contents.values())
    assert all("docker/dockerfile" not in content for content in contents.values())
