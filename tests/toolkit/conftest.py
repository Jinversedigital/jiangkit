import pytest


@pytest.fixture(autouse=True)
def _allow_local_test_servers(monkeypatch):
    """The original web_tools tests talk to a local HTTP server on 127.0.0.1.
    SSRF protection blocks loopback by default, so these tests opt in explicitly."""
    monkeypatch.setenv("JIANGKIT_ALLOW_PRIVATE_NET", "1")
