"""Web panel: loopback binding, upload validation, private work dir with cleanup."""
from __future__ import annotations

import os
import stat
import time

import pytest

gr = pytest.importorskip("gradio")
from jiangkit.panel import app as panel  # noqa: E402


@pytest.mark.parametrize("host,ok", [("127.0.0.1", True), ("localhost", True), ("::1", True), ("127.0.0.2", True),
                                     ("0.0.0.0", False), ("192.168.1.10", False), ("::", False), ("example.com", False)])
def test_is_loopback(host, ok):
    assert panel._is_loopback(host) is ok


def test_refuses_lan_bind_without_flag(monkeypatch, capsys):
    launched = []
    monkeypatch.setattr(panel, "build_app", lambda: launched.append(1))
    assert panel.main(["--host", "0.0.0.0", "--no-browser"]) == 2
    assert not launched
    assert "--i-know-lan" in capsys.readouterr().err


def test_default_host_is_loopback(monkeypatch):
    seen = {}

    class FakeApp:
        jk_theme = jk_css = None

        def queue(self, **kw):
            pass

        def launch(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(panel, "build_app", FakeApp)
    assert panel.main(["--no-browser"]) == 0
    assert seen["server_name"] == "127.0.0.1" and seen["share"] is False
    assert seen["max_file_size"].endswith("mb") and seen["mcp_server"] is False
    assert seen["allowed_paths"] == [str(panel.work_dir())]


def test_upload_extension_and_size_checks(tmp_path, monkeypatch):
    bad = tmp_path / "x.html"
    bad.write_text("<script>")
    with pytest.raises(gr.Error):
        panel._check_upload([str(bad)], [".png"])
    big = tmp_path / "big.png"
    big.write_bytes(b"\0" * 2048)
    monkeypatch.setattr(panel, "MAX_UPLOAD_MB", 0.001)
    with pytest.raises(gr.Error):
        panel._check_upload([str(big)], [".png"])


def test_work_dir_private_and_purged(tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "_WORK", None)
    monkeypatch.setenv("JIANGKIT_PANEL_WORKDIR", str(tmp_path / "wd"))
    wd = panel.work_dir()
    assert stat.S_IMODE(wd.stat().st_mode) == 0o700
    old = wd / "old_run"
    old.mkdir()
    past = time.time() - 48 * 3600
    os.utime(old, (past, past))
    fresh = panel.run_dir("t")
    assert fresh.exists() and not old.exists()
    monkeypatch.setattr(panel, "_WORK", None)


def test_pro_handlers_refuse_without_license(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("JIANGKIT_LICENSE", raising=False)
    with pytest.raises(gr.Error):
        panel._require_pro("web.palette_brand")
