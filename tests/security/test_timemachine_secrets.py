"""shell_timemachine: secrets stored as keyed HMAC tags, redaction, raw env file cleanup, permissions."""
from __future__ import annotations

import hashlib
import stat
from types import SimpleNamespace

import pytest

from jiangkit.experimental.shell_timemachine import shell_timemachine as tm


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_HOME", str(tmp_path / "tm"))


def test_secret_env_values_are_hmac_not_plain_sha(tmp_path):
    env = tm.sanitize_env({"GITHUB_TOKEN": "ghp_abc123secret", "DB_URL": "postgres://u:hunter2@db/x",  # gitleaks:allow (fake test value / type hint, not a secret)
                           "HOME": "/home/u", "EDITOR": "vim"})
    assert env["GITHUB_TOKEN"].startswith("<hmac-sha256:") and "ghp_abc123secret" not in env["GITHUB_TOKEN"]
    assert env["DB_URL"].startswith("<hmac-sha256:")
    assert env["EDITOR"] == "vim"
    plain = hashlib.sha256(b"ghp_abc123secret").hexdigest()[:12]
    assert plain not in env["GITHUB_TOKEN"]  # not a brute-forceable unsalted hash
    kf = tmp_path / "tm" / ".hmac_key"
    assert stat.S_IMODE(kf.stat().st_mode) == 0o600 and len(kf.read_bytes()) >= 32
    assert tm.secret_tag("x") == tm.secret_tag("x")  # stable per install -> diffs still work


def test_command_line_redaction():
    out = tm.redact_text("curl -H 'Authorization: Bearer abcdefghijkl1234' https://u:pw123@host/ && API_KEY=zzz9 run")  # gitleaks:allow (fake test value / type hint, not a secret)
    assert "abcdefghijkl1234" not in out and "pw123" not in out and "zzz9" not in out


def test_raw_env_file_always_deleted(tmp_path):
    envf = tmp_path / "env0"
    envf.write_bytes(b"SECRET_TOKEN=abc\0HOME=/h\0")
    args = SimpleNamespace(env_file=str(envf), output_file=None, files_file=None, cmd="tm log", cwd=str(tmp_path),
                           exit=0, duration=0.1, ts=None, session="s", source="hook")
    assert tm.cmd_record(args) == 0
    assert not envf.exists()  # early-return path (the original leaked the file here)
    envf.write_bytes(b"SECRET_TOKEN=abc\0HOME=/h\0")
    args.cmd = "make build"
    assert tm.cmd_record(args) == 0
    assert not envf.exists()
    db = tmp_path / "tm" / "history.db"
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "tm").stat().st_mode) == 0o700
    assert b"SECRET_TOKEN\": \"abc\"" not in db.read_bytes()


def test_hooks_use_private_umask():
    from pathlib import Path
    d = Path(tm.__file__).parent
    for h in ("hook.bash", "hook.zsh"):
        assert "umask 077" in (d / h).read_text()
