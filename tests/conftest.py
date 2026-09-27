"""Suite-wide pytest fixtures."""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.pop("JIANGKIT_LICENSE", None)
os.environ.pop("JIANGKIT_LICENSE_FILE", None)


def _seed_models() -> None:
    """Point model lookups at a per-user cache; seed it from a local copy if available (offline tests)."""
    cache = Path(os.environ.get("JIANGKIT_MODELS_DIR") or Path.home() / ".cache" / "jiangkit" / "models")
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["JIANGKIT_MODELS_DIR"] = str(cache)


_seed_models()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


class TestKeys:
    """Ephemeral Ed25519 keypair generated per test session (the real private key is never used)."""

    def __init__(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self.priv = Ed25519PrivateKey.generate()
        self.pub_hex = self.priv.public_key().public_bytes(serialization.Encoding.Raw,
                                                           serialization.PublicFormat.Raw).hex()

    def issue(self, product="jiangkit", prefix="JK", tier="pro", buyer="TEST-1", expires=None, key=None):
        payload = {"v": 1, "p": product, "t": tier, "b": buyer, "i": date.today().isoformat()}
        if expires:
            payload["x"] = expires
        body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
        sig = (key or self.priv).sign(f"{prefix}-{body}".encode())
        return f"{prefix}-{body}.{_b64e(sig)}"


@pytest.fixture(scope="session")
def test_keys() -> TestKeys:
    return TestKeys()


@pytest.fixture
def pro_license(test_keys, monkeypatch, tmp_path):
    """Trust the ephemeral key in-process and activate a valid Pro license via env."""
    import jiangkit.license as L

    monkeypatch.setattr(L, "PUBLIC_KEYS", [test_keys.pub_hex])
    key = test_keys.issue()
    monkeypatch.setenv("JIANGKIT_LICENSE", key)
    monkeypatch.setenv("JIANGKIT_TEST_PUBKEY", test_keys.pub_hex)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return key
