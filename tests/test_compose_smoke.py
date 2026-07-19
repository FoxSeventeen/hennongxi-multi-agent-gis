from scripts.compose_smoke import PortBindings, has_loopback_binding, has_no_host_bindings


def test_runtime_port_binding_helpers_distinguish_exposed_from_published_ports() -> None:
    exposed_only: PortBindings = {
        "8001/tcp": None,
        "8002/tcp": None,
    }
    published: PortBindings = {
        "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}],
        "8001/tcp": None,
    }

    assert has_no_host_bindings(exposed_only)
    assert not has_no_host_bindings(published)
    assert has_loopback_binding(published, 8000)
    assert not has_loopback_binding(published, 8001)
