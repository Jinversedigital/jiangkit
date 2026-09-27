import pytest


@pytest.fixture(autouse=True)
def _allow_local_mock_server(monkeypatch):
    """Mock X server runs on 127.0.0.1; SSRF protection blocks loopback unless opted in."""
    monkeypatch.setenv("JIANGKIT_ALLOW_PRIVATE_NET", "1")
