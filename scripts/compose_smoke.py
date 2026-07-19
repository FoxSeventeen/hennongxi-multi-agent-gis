"""Verify the ARM64 Compose runtime, isolation, health, and volume persistence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ("docker", "compose")
SERVICES = (
    "web",
    "master-agent",
    "data-agent",
    "analysis-agent",
    "quality-agent",
    "publisher-agent",
    "postgis",
    "redis",
)
PUBLISHED_PORTS = {
    "web": 3000,
    "master-agent": 8000,
    "publisher-agent": 8004,
}
INTERNAL_PORTS = {
    "data-agent": 8001,
    "analysis-agent": 8002,
    "quality-agent": 8003,
    "postgis": 5432,
    "redis": 6379,
}
type PortBindings = dict[str, list[dict[str, str]] | None]


def run(
    command: Sequence[str],
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=check,
        capture_output=capture_output,
        text=True,
    )


def compose(
    *arguments: str,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return run(
        (*COMPOSE, *arguments),
        check=check,
        capture_output=capture_output,
    )


def compose_exec(service: str, *command: str) -> str:
    result = compose(
        "exec",
        "-T",
        service,
        *command,
        capture_output=True,
    )
    return result.stdout.strip()


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"unexpected status from {url}: {response.status}")
        return cast(dict[str, Any], json.load(response))


def assert_services_running() -> None:
    result = compose("ps", "--status", "running", "--services", capture_output=True)
    running = set(result.stdout.splitlines())
    if running != set(SERVICES):
        raise RuntimeError(f"unexpected running services: {sorted(running)}")


def container_port_bindings(service: str) -> PortBindings:
    container_id = compose("ps", "-q", service, capture_output=True).stdout.strip()
    result = run(
        (
            "docker",
            "inspect",
            "--format",
            "{{json .HostConfig.PortBindings}}",
            container_id,
        ),
        capture_output=True,
    )
    return cast(PortBindings, json.loads(result.stdout or "{}"))


def has_loopback_binding(bindings: PortBindings, port: int) -> bool:
    return bindings.get(f"{port}/tcp") == [{"HostIp": "127.0.0.1", "HostPort": str(port)}]


def has_no_host_bindings(bindings: PortBindings) -> bool:
    return not any(bindings.values())


def assert_port_exposure() -> None:
    for service, port in PUBLISHED_PORTS.items():
        bindings = container_port_bindings(service)
        if not has_loopback_binding(bindings, port):
            raise RuntimeError(f"unexpected host binding for {service}: {bindings}")

    for service, port in INTERNAL_PORTS.items():
        bindings = container_port_bindings(service)
        if not has_no_host_bindings(bindings):
            raise RuntimeError(
                f"internal service {service} unexpectedly publishes port {port}: {bindings}"
            )


def assert_arm64_images() -> None:
    for service in SERVICES:
        container_id = compose("ps", "-q", service, capture_output=True).stdout.strip()
        if not container_id:
            raise RuntimeError(f"missing container for {service}")
        image_id = run(
            ("docker", "inspect", "--format", "{{.Image}}", container_id),
            capture_output=True,
        ).stdout.strip()
        architecture = run(
            ("docker", "image", "inspect", "--format", "{{.Architecture}}", image_id),
            capture_output=True,
        ).stdout.strip()
        if architecture != "arm64":
            raise RuntimeError(f"{service} uses unexpected image architecture: {architecture}")


def assert_health() -> None:
    with urllib.request.urlopen("http://127.0.0.1:3000/", timeout=5) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"Web health returned {response.status}")

    local_master = fetch_json("http://127.0.0.1:8000/internal/v1/health")
    aggregate = fetch_json("http://127.0.0.1:8000/api/v1/health")
    readiness = fetch_json("http://127.0.0.1:8000/api/v1/config/readiness")
    publisher = fetch_json("http://127.0.0.1:8004/internal/v1/health")

    if local_master["state"] != "HEALTHY":
        raise RuntimeError("Master liveness is not healthy")
    if aggregate["state"] != "HEALTHY":
        raise RuntimeError(f"aggregate health is not healthy: {aggregate}")
    if publisher["state"] != "HEALTHY":
        raise RuntimeError("Publisher liveness is not healthy")
    if readiness["ready"]:
        raise RuntimeError("readiness must remain false before data and LLM approval gates")

    health_probe = (
        "import json, urllib.request; "
        "urls = ["
        "'http://data-agent:8001/internal/v1/health', "
        "'http://analysis-agent:8002/internal/v1/health', "
        "'http://quality-agent:8003/internal/v1/health', "
        "'http://publisher-agent:8004/internal/v1/health']; "
        "assert all(json.load(urllib.request.urlopen(url, timeout=2))['state'] == 'HEALTHY' "
        "for url in urls)"
    )
    compose_exec("master-agent", "python", "-c", health_probe)


def write_persistence_markers(marker: str) -> None:
    postgis_sql = (
        "CREATE TABLE IF NOT EXISTS compose_smoke_marker "
        "(marker text PRIMARY KEY); "
        f"INSERT INTO compose_smoke_marker(marker) VALUES ('{marker}') "
        "ON CONFLICT (marker) DO NOTHING;"
    )
    compose_exec(
        "postgis",
        "sh",
        "-c",
        'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
        "compose-smoke",
        postgis_sql,
    )
    compose_exec("redis", "redis-cli", "SET", "compose-smoke-marker", marker)
    compose_exec(
        "data-agent",
        "python",
        "-c",
        "from pathlib import Path; import sys; "
        "Path('/data/cache/.compose-smoke').write_text(sys.argv[1])",
        marker,
    )
    compose_exec(
        "analysis-agent",
        "python",
        "-c",
        "from pathlib import Path; import sys; "
        "Path('/data/outputs/.compose-smoke').write_text(sys.argv[1])",
        marker,
    )


def assert_persistence(marker: str) -> None:
    postgis_value = compose_exec(
        "postgis",
        "sh",
        "-c",
        'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
        f"-tAc \"SELECT marker FROM compose_smoke_marker WHERE marker = '{marker}'\"",
    )
    redis_value = compose_exec("redis", "redis-cli", "--raw", "GET", "compose-smoke-marker")
    cache_value = compose_exec(
        "analysis-agent",
        "python",
        "-c",
        "from pathlib import Path; print(Path('/data/cache/.compose-smoke').read_text())",
    )
    artifact_value = compose_exec(
        "publisher-agent",
        "python",
        "-c",
        "from pathlib import Path; print(Path('/data/outputs/.compose-smoke').read_text())",
    )
    if {postgis_value, redis_value, cache_value, artifact_value} != {marker}:
        raise RuntimeError("one or more named volumes did not survive docker compose down/up")


def cleanup_persistence_markers(marker: str) -> None:
    compose_exec(
        "postgis",
        "sh",
        "-c",
        'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
        f"-c \"DELETE FROM compose_smoke_marker WHERE marker = '{marker}'\"",
    )
    compose_exec("redis", "redis-cli", "DEL", "compose-smoke-marker")
    for service, path in (
        ("data-agent", "/data/cache/.compose-smoke"),
        ("analysis-agent", "/data/outputs/.compose-smoke"),
    ):
        compose_exec(
            service,
            "python",
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).unlink(missing_ok=True)",
            path,
        )


def start_stack(*, build: bool) -> None:
    arguments = ["up", "--detach", "--wait", "--wait-timeout", "240"]
    if build:
        arguments.insert(1, "--build")
    compose(*arguments)


def verify(*, build: bool) -> None:
    marker = str(uuid4())
    compose("config", "--quiet")
    start_stack(build=build)
    assert_services_running()
    assert_port_exposure()
    assert_arm64_images()
    assert_health()
    write_persistence_markers(marker)

    compose("down", "--remove-orphans")
    start_stack(build=False)

    assert_services_running()
    assert_health()
    assert_persistence(marker)
    cleanup_persistence_markers(marker)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="reuse existing images instead of rebuilding before the smoke run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(("docker", "info"), capture_output=True)
        verify(build=not args.skip_build)
    except Exception as error:
        compose("logs", "--no-color", "--tail", "200", check=False)
        print(f"Compose smoke failed: {error}", file=sys.stderr)
        return 1

    print("Compose smoke passed: ARM64, health, isolation, and persistence verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
