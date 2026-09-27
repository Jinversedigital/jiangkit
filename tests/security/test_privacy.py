"""EXIF stripping on image export, secret redaction, no telemetry defaults."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from PIL import Image

from jiangkit.security.images import save_clean, strip_metadata
from jiangkit.security.redact import RedactingFilter, redact

from ._util import jiang


def _jpeg_with_gps(path: Path):
    img = Image.new("RGB", (64, 48), (200, 50, 50))
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"            # Make
    exif[0x0131] = "Private Software"     # Software
    gps = {1: "N", 2: (25.0, 2.0, 0.0), 3: "E", 4: (121.0, 33.0, 0.0)}
    exif[0x8825] = gps
    img.save(path, exif=exif.tobytes())
    assert Image.open(path).getexif()


def _has_exif(p: Path) -> bool:
    im = Image.open(p)
    return bool(im.getexif()) or "exif" in im.info


def test_save_clean_strips(tmp_path):
    src = tmp_path / "a.jpg"
    _jpeg_with_gps(src)
    out = tmp_path / "b.jpg"
    save_clean(Image.open(src), out)
    assert not _has_exif(out)
    assert not strip_metadata(Image.open(src)).info.get("exif")


@pytest.mark.parametrize("cmd", [["strip-exif"], ["resize", "--max-side", "32"], ["fit", "-p", "100x100"],
                                 ["convert", "-f", "png"]])
def test_img_tools_exports_have_no_exif(tmp_path, cmd):
    src = tmp_path / "in.jpg"
    _jpeg_with_gps(src)
    out = tmp_path / "out"
    r = jiang("media", "img", cmd[0], src, *cmd[1:], "-o", out)
    assert r.returncode == 0, r.stderr
    files = [p for p in out.rglob("*") if p.is_file()]
    assert files
    for f in files:
        assert not _has_exif(f), f


@pytest.mark.parametrize("text,leak", [
    ("Authorization: Bearer abcdefghijklmnop123", "abcdefghijklmnop123"),
    ("cookie: auth_token=deadbeefcafebabe; ct0=0123456789", "deadbeefcafebabe"),
    ("api_key=sk-live-1234567890abcdef", "1234567890abcdef"),  # gitleaks:allow (fake test value / type hint, not a secret)
    ("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 leaked", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"),
    ("key JK-eyJ2IjoxLCJwIjoiamlhbmdraXQifQ.c2lnbmF0dXJlc2lnbmF0dXJlc2ln", "eyJ2IjoxLCJwIjoiamlhbmdraXQifQ"),
    ("password: hunter2hunter2", "hunter2hunter2"),
])
def test_redact(text, leak):
    assert leak not in redact(text)


def test_logging_filter(caplog):
    log = logging.getLogger("jk-test")
    log.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="jk-test"):
        log.info("sending %s", "Authorization: Bearer supersecrettoken123")
    assert "supersecrettoken123" not in caplog.text


def test_cli_error_messages_are_redacted(tmp_path):
    r = jiang("web", "web", "fetch", "http://user:ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345@127.0.0.1/")
    assert r.returncode == 5
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in r.stderr + r.stdout


def test_telemetry_disabled_by_default():
    import importlib

    import jiangkit
    importlib.reload(jiangkit)
    assert os.environ.get("GRADIO_ANALYTICS_ENABLED") == "False"
    assert os.environ.get("HF_HUB_DISABLE_TELEMETRY") == "1"
    assert os.environ.get("DO_NOT_TRACK") == "1"


def test_no_telemetry_endpoints_in_sources():
    src = Path(__file__).resolve().parents[2] / "src" / "jiangkit"
    blob = "\n".join(f.read_text(encoding="utf-8") for f in src.rglob("*.py"))
    for needle in ["google-analytics", "segment.io", "sentry_sdk", "mixpanel", "posthog", "amplitude"]:
        assert needle not in blob, needle
