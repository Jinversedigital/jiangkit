"""SSRF protection in jiangkit.security.net (used by web_tools, x_scraper downloads, model downloads)."""
from __future__ import annotations

import http.server
import socket
import threading

import pytest

from jiangkit.security import net
from jiangkit.security.net import ResponseTooLarge, SSRFError, is_public_ip, safe_get, validate_url


@pytest.fixture(autouse=True)
def _no_env_optout(monkeypatch):
    monkeypatch.delenv("JIANGKIT_ALLOW_PRIVATE_NET", raising=False)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.8.9.10", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "::1", "fe80::1", "fc00::1", "fd12:3456::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "2002:7f00:1::1", "::", "not-an-ip",
])
def test_non_public_ips_rejected(ip):
    assert not is_public_ip(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_ips_accepted(ip):
    assert is_public_ip(ip)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:8080/admin", "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/", "http://[::1]/", "http://10.0.0.5/",
    "http://0x7f000001/", "http://2130706433/", "http://[::ffff:127.0.0.1]/", "http://0/",
])
def test_private_targets_blocked(url):
    with pytest.raises(SSRFError):
        validate_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://127.0.0.1:25/",
                                 "dict://127.0.0.1:11211/", "javascript:alert(1)", "http://user:pw@example.com/"])
def test_bad_schemes_and_credentials_blocked(url):
    with pytest.raises(SSRFError):
        validate_url(url)


def test_dns_resolving_to_private_is_blocked(monkeypatch):
    def fake_gai(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", port))]
    monkeypatch.setattr(net.socket, "getaddrinfo", fake_gai)
    with pytest.raises(SSRFError, match="non-public"):
        validate_url("http://innocent-looking.example/")


def test_dns_with_any_private_record_is_blocked(monkeypatch):
    def fake_gai(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    monkeypatch.setattr(net.socket, "getaddrinfo", fake_gai)
    with pytest.raises(SSRFError):
        validate_url("http://mixed.example/")


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):  # noqa: N802
        status, headers, body = self.routes.get(self.path, (404, {}, b"nope"))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Handler.routes
    srv.shutdown()
    _Handler.routes.clear()


def test_loopback_server_blocked_by_default(local_server):
    base, routes = local_server
    routes["/"] = (200, {}, b"secret admin page")
    with pytest.raises(SSRFError):
        safe_get(base + "/")


def test_redirect_to_metadata_is_revalidated(local_server, monkeypatch):
    base, routes = local_server
    routes["/r"] = (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b"")
    real = net.is_public_ip
    monkeypatch.setattr(net, "is_public_ip", lambda ip: ip == "127.0.0.1" or real(ip))  # first hop "public"
    with pytest.raises(SSRFError):
        safe_get(base + "/r")


def test_redirect_to_file_scheme_blocked(local_server, monkeypatch):
    base, routes = local_server
    routes["/r"] = (301, {"Location": "file:///etc/passwd"}, b"")
    real = net.is_public_ip
    monkeypatch.setattr(net, "is_public_ip", lambda ip: ip == "127.0.0.1" or real(ip))
    with pytest.raises(SSRFError, match="http/https"):
        safe_get(base + "/r")


def test_dns_rebinding_caught_at_socket_level(local_server, monkeypatch):
    """Resolution says 'public', but the socket actually connects to 127.0.0.1: the peer check must fire."""
    base, routes = local_server
    routes["/"] = (200, {}, b"internal")
    port = int(base.rsplit(":", 1)[1])
    real_gai = socket.getaddrinfo
    calls = {"n": 0}

    def rebinding_gai(host, p, *a, **kw):
        if host == "rebind.example":
            calls["n"] += 1
            if calls["n"] == 1:  # our validation
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", p))]
            return real_gai("127.0.0.1", p, *a, **kw)  # urllib3's own lookup -> loopback
        return real_gai(host, p, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_gai)
    with pytest.raises(SSRFError, match="non-public"):
        safe_get(f"http://rebind.example:{port}/")
    assert calls["n"] >= 2


def test_size_cap_streamed(local_server):
    base, routes = local_server
    routes["/big"] = (200, {}, b"x" * 50_000)
    with pytest.raises(ResponseTooLarge):
        safe_get(base + "/big", allow_private=True, max_bytes=10_000)
    r = safe_get(base + "/big", allow_private=True, max_bytes=100_000)
    assert len(r.content) == 50_000


def test_too_many_redirects(local_server):
    base, routes = local_server
    routes["/loop"] = (302, {"Location": "/loop"}, b"")
    with pytest.raises(SSRFError, match="too many redirects"):
        safe_get(base + "/loop", allow_private=True)


def test_session_ignores_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    assert net.make_session().trust_env is False


def test_web_tools_uses_safe_fetch(monkeypatch):
    from jiangkit.web import web_tools
    monkeypatch.setattr(web_tools, "ALLOW_PRIVATE", False, raising=False)
    with pytest.raises(SSRFError):
        web_tools.get("http://169.254.169.254/latest/meta-data/")


def test_web_cli_exit_code_for_blocked_url():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "jiangkit", "web", "web", "fetch", "http://127.0.0.1:1/"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env={**__import__("os").environ, "JIANGKIT_ALLOW_PRIVATE_NET": ""})
    assert r.returncode == 5, r.stderr


def test_x_scraper_media_host_allow_list():
    from jiangkit.social.x_scraper.scraper import _media_url_allowed
    assert _media_url_allowed("https://pbs.twimg.com/media/abc.jpg", False)
    assert not _media_url_allowed("http://pbs.twimg.com/media/abc.jpg", False)
    assert not _media_url_allowed("https://evil.example/x.jpg", False)
    assert not _media_url_allowed("https://pbs.twimg.com.evil.example/x.jpg", False)
    assert not _media_url_allowed("file:///etc/passwd", True)
