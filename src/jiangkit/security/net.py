"""SSRF-safe HTTP helpers.

Protection layers (all on by default):
1. scheme allow-list (http/https only), no credentials in URL
2. hostname resolution: every resolved address must be globally routable
   (blocks loopback, RFC1918, CGNAT, link-local incl. 169.254.169.254 cloud metadata,
   multicast, reserved, unspecified, IPv6 ULA/site-local, IPv4-mapped variants)
3. redirects are followed manually (max N) and each hop is re-validated
4. the *actually connected* socket peer address is checked again (defeats DNS rebinding /
   TOCTOU between our resolution and urllib3's)
5. environment proxies are ignored (trust_env=False) so the peer check is meaningful
6. connect/read timeouts and a hard response size cap (streamed)

Local testing: pass allow_private=True or set JIANGKIT_ALLOW_PRIVATE_NET=1 (documented, off by default).
"""
from __future__ import annotations

import ipaddress
import os
import socket
from typing import Iterable
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

DEFAULT_TIMEOUT = (5.0, 20.0)
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 5
METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data", "instance-data.ec2.internal"}


class SSRFError(ValueError):
    """Raised when a URL targets a non-public address or violates fetch policy."""


class ResponseTooLarge(ValueError):
    pass


def private_allowed(flag: bool | None = None) -> bool:
    if flag is not None:
        return flag
    return os.environ.get("JIANGKIT_ALLOW_PRIVATE_NET") == "1"


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return is_public_ip(str(addr.ipv4_mapped))
        if addr.sixtofour:
            return is_public_ip(str(addr.sixtofour))
        if addr.teredo:
            return False
    if (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
            or addr.is_reserved or addr.is_unspecified or getattr(addr, "is_site_local", False)):
        return False
    if isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10"):
        return False  # carrier-grade NAT (shared address space)
    return addr.is_global


def resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SSRFError(f"DNS resolution failed for {host!r}: {e}") from e
    return sorted({i[4][0] for i in infos})


def validate_url(url: str, allow_private: bool | None = None) -> str:
    """Validate scheme/host/resolved IPs. Returns the URL unchanged or raises SSRFError."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SSRFError(f"only http/https URLs are allowed (got {parts.scheme or 'none'!r})")
    if parts.username or parts.password:
        raise SSRFError("credentials in URLs are not allowed")
    host = parts.hostname
    if not host:
        raise SSRFError("URL has no host")
    if private_allowed(allow_private):
        return url
    if host.lower().rstrip(".") in METADATA_HOSTS:
        raise SSRFError(f"blocked metadata host {host!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addrs = resolve(host, port)
    bad = [a for a in addrs if not is_public_ip(a)]
    if not addrs or bad:
        raise SSRFError(f"blocked non-public address for {host!r}: {', '.join(bad) or 'none'}")
    return url


# --------------------------------------------------------------- socket-level peer verification
def _check_peer(sock) -> None:
    try:
        peer = sock.getpeername()[0]
    except OSError:
        return
    if not is_public_ip(peer):
        sock.close()
        raise SSRFError(f"connection to non-public address {peer} blocked")


class _SafeHTTPConnection(HTTPConnection):
    def _new_conn(self):  # type: ignore[override]
        sock = super()._new_conn()
        _check_peer(sock)
        return sock


class _SafeHTTPSConnection(HTTPSConnection):
    def _new_conn(self):  # type: ignore[override]
        sock = super()._new_conn()
        _check_peer(sock)
        return sock


class _SafeHTTPPool(HTTPConnectionPool):
    ConnectionCls = _SafeHTTPConnection


class _SafeHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _SafeHTTPSConnection


class SafeAdapter(HTTPAdapter):
    def init_poolmanager(self, *a, **kw):
        super().init_poolmanager(*a, **kw)
        self.poolmanager.pool_classes_by_scheme = {"http": _SafeHTTPPool, "https": _SafeHTTPSPool}


def make_session(allow_private: bool | None = None, user_agent: str | None = None) -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # ignore HTTP(S)_PROXY so the peer check sees the real target
    s.max_redirects = DEFAULT_MAX_REDIRECTS
    if not private_allowed(allow_private):
        s.mount("http://", SafeAdapter())
        s.mount("https://", SafeAdapter())
    s._jiangkit_allow_private = private_allowed(allow_private)  # type: ignore[attr-defined]
    if user_agent:
        s.headers["User-Agent"] = user_agent
    return s


def safe_request(method: str, url: str, *, session: requests.Session | None = None,
                 allow_private: bool | None = None, timeout=DEFAULT_TIMEOUT,
                 max_bytes: int = DEFAULT_MAX_BYTES, max_redirects: int = DEFAULT_MAX_REDIRECTS,
                 **kw) -> requests.Response:
    """Perform a request with manual, re-validated redirects and a streamed size cap.

    The returned Response has its body already read (``resp.content`` works) and bounded.
    """
    if session is None:
        session = make_session(allow_private)
    allow = getattr(session, "_jiangkit_allow_private", private_allowed(allow_private)) if allow_private is None else allow_private
    kw.pop("allow_redirects", None)
    kw.pop("stream", None)
    current = url
    for _hop in range(max_redirects + 1):
        validate_url(current, allow)
        resp = session.request(method, current, timeout=timeout, allow_redirects=False, stream=True, **kw)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            resp.close()
            if not loc:
                raise SSRFError("redirect without Location")
            current = urljoin(current, loc)
            if resp.status_code == 303 or (resp.status_code in (301, 302) and method.upper() == "POST"):
                method = "GET"
                kw.pop("data", None)
                kw.pop("json", None)
            continue
        _read_capped(resp, max_bytes)
        return resp
    raise SSRFError(f"too many redirects (> {max_redirects})")


def _read_capped(resp: requests.Response, max_bytes: int) -> None:
    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        resp.close()
        raise ResponseTooLarge(f"response declares {declared} bytes (> {max_bytes})")
    buf = bytearray()
    for chunk in resp.iter_content(64 * 1024):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            resp.close()
            raise ResponseTooLarge(f"response exceeds {max_bytes} bytes")
    resp._content = bytes(buf)  # noqa: SLF001 - make .content/.text available after streaming
    resp._content_consumed = True  # noqa: SLF001


def safe_get(url: str, **kw) -> requests.Response:
    return safe_request("GET", url, **kw)


def download_to_file(url: str, dest, *, max_bytes: int = DEFAULT_MAX_BYTES, allow_private: bool | None = None,
                     timeout=DEFAULT_TIMEOUT, session: requests.Session | None = None) -> int:
    resp = safe_get(url, session=session, allow_private=allow_private, timeout=timeout, max_bytes=max_bytes)
    resp.raise_for_status()
    with open(dest, "wb") as fh:
        fh.write(resp.content)
    return len(resp.content)


def filter_public(urls: Iterable[str], allow_private: bool | None = None) -> list[str]:
    ok = []
    for u in urls:
        try:
            validate_url(u, allow_private)
            ok.append(u)
        except SSRFError:
            pass
    return ok
