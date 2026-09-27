import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jiangkit.system import sys_tools


@pytest.fixture
def local_webhook():
    """A throwaway HTTP server on 127.0.0.1 that records POST bodies (no real webhook)."""
    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(n)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/hook", received
    srv.shutdown()


def test_webhook_payload_flavours():
    _, d = sys_tools.build_webhook_request("https://discord.com/api/webhooks/1/abc", "T", "m")
    assert d == {"content": "**T**\nm"}
    _, s = sys_tools.build_webhook_request("https://hooks.slack.com/services/x", "T", "m")
    assert s == {"text": "*T*\nm"}
    _, g = sys_tools.build_webhook_request("https://example.com/h", "T", "m")
    assert g["title"] == "T" and g["message"] == "m"
    assert "abc" not in sys_tools.redact("https://discord.com/api/webhooks/1/abc")


def test_notify_webhook_via_env(cli, local_webhook):
    url, received = local_webhook
    r = cli("sys_tools", "notify", "渲染完成", "-t", "ComfyUI", "-c", "webhook", "-c", "terminal",
            "--webhook-env", "MY_HOOK", extra_env={"MY_HOOK": url})
    assert len(received) == 1 and received[0]["message"] == "渲染完成"
    assert "HTTP 204" in r.stderr and "ComfyUI" in r.stdout


def test_notify_missing_env_and_dry_run(cli):
    r = cli("sys_tools", "notify", "x", "-c", "webhook", "--webhook-env", "NOPE_UNSET", check=False)
    assert r.returncode == 1 and "not set" in r.stderr
    r = cli("sys_tools", "notify", "x", "-c", "webhook", "-n",
            extra_env={"TOOLKIT_WEBHOOK_URL": "https://discord.com/api/webhooks/123/SECRET"})
    assert "[dry-run]" in r.stdout and "SECRET" not in r.stdout
    r = cli("sys_tools", "notify", "x", "-c", "telegram", check=False,
            extra_env={"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""})
    assert r.returncode == 1 and "TELEGRAM_BOT_TOKEN" in r.stderr


def test_run_job_logging_and_history(cli, tmp_path, local_webhook):
    url, received = local_webhook
    log = tmp_path / "jobs.jsonl"
    r = cli("sys_tools", "run", "--name", "hello", "--log", log, "--out-dir", "out",
            "--times", "2", "--every", "0.1s", "--show-output", "--",
            sys.executable, "-c", "print('hi from job')")
    assert r.stdout.count("[hello] ok") == 2 and "hi from job" in r.stdout
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(recs) == 2 and all(x["status"] == "ok" for x in recs)

    r = cli("sys_tools", "run", "--name", "boom", "--log", log, "--out-dir", "out",
            "--retries", "1", "--notify", "webhook", "--",
            sys.executable, "-c", "import sys; print('bad'); sys.exit(3)",
            check=False, extra_env={"TOOLKIT_WEBHOOK_URL": url})
    assert r.returncode == 3
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    assert [x["attempt"] for x in recs if x["job"] == "boom"] == [1, 2]
    assert received and "boom" in received[0]["title"] and "bad" in received[0]["message"]

    r = cli("sys_tools", "run", "--name", "slow", "--log", log, "--out-dir", "out", "--timeout",
            "0.5", "--", sys.executable, "-c", "import time; time.sleep(5)", check=False)
    assert "timeout" in r.stdout and r.returncode == 1

    h = cli("sys_tools", "history", "--log", log, "--failed")
    assert "boom" in h.stdout and "slow" in h.stdout and "hello" not in h.stdout


def test_seconds_until():
    import datetime as dt
    now = dt.datetime(2026, 9, 27, 8, 0, 0)
    assert sys_tools.seconds_until("09:30", now) == 5400
    assert sys_tools.seconds_until("07:00", now) == 23 * 3600
    assert sys_tools.parse_interval("10m") == 600


def test_ports_and_wait_port(cli):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    try:
        r = cli("sys_tools", "ports", "--check", port)
        assert "OPEN" in r.stdout
        r = cli("sys_tools", "wait-port", port, "--timeout", "2")
        assert "is up" in r.stdout
        listing = cli("sys_tools", "ports", "--json")
        assert any(x["port"] == port for x in json.loads(listing.stdout))
    finally:
        srv.close()
    r = cli("sys_tools", "ports", "--check", port, check=False)
    assert r.returncode == 1 and "closed" in r.stdout
    r = cli("sys_tools", "wait-port", port, "--timeout", "0.6", check=False)
    assert r.returncode == 1


def test_proc_find_and_kill(cli):
    marker = "toolkit2_test_sleeper_4821"
    p = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(60)  # {marker}"])
    try:
        time.sleep(0.3)
        r = cli("sys_tools", "proc", marker, "--kill")
        assert str(p.pid) in r.stdout and "[dry-run]" in r.stdout and p.poll() is None
        cli("sys_tools", "proc", marker, "--kill", "--yes")
        assert p.wait(timeout=5) is not None
    finally:
        if p.poll() is None:
            p.kill()
