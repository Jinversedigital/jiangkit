"""Offline Pro license verification (Ed25519 public-key signatures).

Only the PUBLIC key is embedded here. Keys are issued by the seller with
tools/license_keygen.py (never shipped). Format:

    JK-<base64url(JSON payload)>.<base64url(64-byte Ed25519 signature over "JK-<body>")>
    payload = {"v":1, "p":"jiangkit", "t":"pro", "b":"<buyer id>", "i":"YYYY-MM-DD", "x":"YYYY-MM-DD"?}

Where the key is looked up (first hit wins):
    1. $JIANGKIT_LICENSE        (the key string itself)
    2. $JIANGKIT_LICENSE_FILE   (path to a file containing the key)
    3. ~/.config/jiangkit/license.key   (written by `jiang license activate`)

HONESTY NOTE: this is a client-side check. Anyone with the Python source can patch it out.
It stops casual key sharing/forgery (keys cannot be minted without the private key), not a
determined cracker. Stronger protection needs online activation / server-side features.
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ._brand import ENV_PREFIX, LICENSE_PREFIX, PRODUCT_ID

# Ed25519 public key(s) for PRODUCT_ID (hex, raw 32 bytes). A list allows key rotation.
PUBLIC_KEYS: list[str] = [
    "0fd4491bc40c5c375f3fe69f66ca755ebecc9c5cb822009b2366d364cece9e44",
]
KEY_RE = re.compile(r"^([A-Z0-9]{2,8})-([A-Za-z0-9_-]{8,2048})\.([A-Za-z0-9_-]{86})$")
PRO_TIERS = {"pro", "business", "lifetime"}


@dataclass
class LicenseStatus:
    valid: bool
    reason: str = ""
    payload: dict = field(default_factory=dict)
    source: str = ""

    @property
    def tier(self) -> str:
        return self.payload.get("t", "free") if self.valid else "free"

    @property
    def is_pro(self) -> bool:
        return self.valid and self.tier in PRO_TIERS


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_key(key: str, *, product: str = PRODUCT_ID, prefix: str = LICENSE_PREFIX,
               public_keys: list[str] | None = None, now: datetime | None = None) -> LicenseStatus:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    m = KEY_RE.match((key or "").strip())
    if not m:
        return LicenseStatus(False, "format")
    kprefix, body, sig_s = m.groups()
    if kprefix != prefix:
        return LicenseStatus(False, "product")
    try:
        sig = _b64d(sig_s)
    except (ValueError, TypeError):
        return LicenseStatus(False, "format")
    msg = f"{kprefix}-{body}".encode()
    ok = False
    for hx in (public_keys if public_keys is not None else PUBLIC_KEYS):
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(hx)).verify(sig, msg)
            ok = True
            break
        except (InvalidSignature, ValueError):
            continue
    if not ok:
        return LicenseStatus(False, "signature")
    try:
        payload = json.loads(_b64d(body))
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, TypeError):
        return LicenseStatus(False, "format")
    if payload.get("p") != product:
        return LicenseStatus(False, "product", payload)
    if payload.get("x"):
        try:
            exp = datetime.fromisoformat(str(payload["x"]) + "T23:59:59+00:00")
        except ValueError:
            return LicenseStatus(False, "format", payload)
        if exp < (now or datetime.now(timezone.utc)):
            return LicenseStatus(False, "expired", payload)
    return LicenseStatus(True, "", payload)


def default_license_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / PRODUCT_ID / "license.key"


def load_key() -> tuple[str, str]:
    env_key = os.environ.get(f"{ENV_PREFIX}_LICENSE")
    if env_key:
        return env_key.strip(), "env"
    f = os.environ.get(f"{ENV_PREFIX}_LICENSE_FILE")
    path = Path(f) if f else default_license_file()
    try:
        if path.is_file() and path.stat().st_size < 8192:
            return path.read_text(encoding="utf-8").strip(), str(path)
    except OSError:
        pass
    return "", ""


def status() -> LicenseStatus:
    key, src = load_key()
    if not key:
        return LicenseStatus(False, "missing")
    st = verify_key(key)
    st.source = src
    return st


def activate(key: str) -> tuple[LicenseStatus, Path | None]:
    st = verify_key(key)
    if not st.valid:
        return st, None
    path = default_license_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key.strip() + "\n")
    return st, path


def deactivate() -> bool:
    p = default_license_file()
    if p.exists():
        p.unlink()
        return True
    return False


def require_pro(tool: str) -> LicenseStatus:
    from .errors import ProRequired

    st = status()
    if not st.is_pro:
        raise ProRequired(tool)
    return st
