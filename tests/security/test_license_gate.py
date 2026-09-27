"""Ed25519 license keys and the Pro gate."""
from __future__ import annotations

import base64
import json
import os
import stat
from datetime import date, timedelta
from pathlib import Path

import pytest

import jiangkit.license as L

from ._util import jiang

ROOT = Path(__file__).resolve().parents[2]


def test_valid_key(test_keys):
    st = L.verify_key(test_keys.issue(), public_keys=[test_keys.pub_hex])
    assert st.valid and st.is_pro and st.payload["b"] == "TEST-1"


def test_tampered_payload_rejected(test_keys):
    key = test_keys.issue(tier="standard")
    prefix, rest = key.split("-", 1)
    body, sig = rest.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["t"] = "pro"
    forged_body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    assert L.verify_key(f"{prefix}-{forged_body}.{sig}", public_keys=[test_keys.pub_hex]).reason == "signature"


def test_tampered_signature_rejected(test_keys):
    key = test_keys.issue()
    flipped = key[:-2] + ("A" if key[-2] != "A" else "B") + key[-1]
    assert not L.verify_key(flipped, public_keys=[test_keys.pub_hex]).valid


def test_other_signer_rejected(test_keys):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    forged = test_keys.issue(key=Ed25519PrivateKey.generate())
    assert L.verify_key(forged, public_keys=[test_keys.pub_hex]).reason == "signature"


def test_expired_rejected(test_keys):
    old = (date.today() - timedelta(days=2)).isoformat()
    assert L.verify_key(test_keys.issue(expires=old), public_keys=[test_keys.pub_hex]).reason == "expired"
    future = (date.today() + timedelta(days=30)).isoformat()
    assert L.verify_key(test_keys.issue(expires=future), public_keys=[test_keys.pub_hex]).valid


def test_wrong_product_and_prefix(test_keys):
    assert L.verify_key(test_keys.issue(product="invoice-pro"), public_keys=[test_keys.pub_hex]).reason == "product"
    assert L.verify_key(test_keys.issue(prefix="INV"), public_keys=[test_keys.pub_hex]).reason == "product"


@pytest.mark.parametrize("junk", ["", "JK-", "JK-abc", "hello world", "JK-" + "A" * 5000 + ".x", "JK-e30.!!!!"])
def test_garbage_rejected(junk):
    assert not L.verify_key(junk).valid


def test_embedded_key_rejects_test_keys(test_keys):
    """The shipped public key must not accept keys signed by anyone else (e.g. the test key)."""
    assert L.verify_key(test_keys.issue()).reason == "signature"
    assert all(len(k) == 64 and bytes.fromhex(k) for k in L.PUBLIC_KEYS)


def test_no_env_override_for_public_key(test_keys, monkeypatch):
    monkeypatch.setenv("JIANGKIT_TEST_PUBKEY", test_keys.pub_hex)
    monkeypatch.setenv("JIANGKIT_PUBLIC_KEY", test_keys.pub_hex)
    monkeypatch.setenv("JIANGKIT_LICENSE", test_keys.issue())
    assert not L.status().is_pro


def test_activate_writes_private_file(pro_license, tmp_path, monkeypatch):
    monkeypatch.delenv("JIANGKIT_LICENSE")
    st, path = L.activate(pro_license)
    assert st.valid and path and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert L.status().is_pro
    assert L.deactivate() and not L.status().is_pro


def test_pro_tool_requires_license_exit_3(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path), "JIANGKIT_LICENSE": "", "JIANGKIT_LICENSE_FILE": ""}
    for cat, tool in [("comic", "comic"), ("web", "palette_brand"), ("ai", "auto_tagger"), ("social", "trend_radar"),
                      ("media", "beat_reels"), ("media", "best_frame"), ("ai", "face_consistency")]:
        r = jiang(cat, tool, "--help", env=env)
        assert r.returncode == 3, (cat, tool, r.stdout, r.stderr)


def test_free_tool_works_without_license(tmp_path):
    r = jiang("social", "social", "--help", env={"XDG_CONFIG_HOME": str(tmp_path), "JIANGKIT_LICENSE": ""})
    assert r.returncode == 0


def test_forged_key_via_cli_is_refused(tmp_path, test_keys):
    r = jiang("license", "activate", test_keys.issue(), env={"XDG_CONFIG_HOME": str(tmp_path), "JIANGKIT_LICENSE": ""})
    assert r.returncode != 0 and not (tmp_path / "jiangkit" / "license.key").exists()


def test_keygen_not_in_package_and_private_keys_not_in_repo_tree():
    assert not (ROOT / "src" / "jiangkit" / "license_keygen.py").exists()
    import re
    pem = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=]{20,}")
    for p in (ROOT / "src").rglob("*"):
        if p.is_file() and p.suffix in (".py", ".pem", ".key", ".txt", ".json"):
            assert not pem.search(p.read_text(errors="ignore")), p


def test_free_build_without_pro_module_exits_3(monkeypatch, capsys):
    """Free wheel strips Pro modules: the CLI must say so (exit 3), not crash or claim a missing extra."""
    from jiangkit import cli, registry

    monkeypatch.setattr(registry, "module_available", lambda tool: tool.tier != "pro")
    rc = cli.run_tool("media", "beat_reels", ["--help"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "免費版" in err and "free build" in err
