#!/usr/bin/env python3
"""sys_tools.py - notifications, job runner and port/process checks.

Subcommands:
  notify     Send a notification: terminal, desktop (notify-send/osascript/PowerShell) and/or
             webhook. Webhook URLs / bot tokens are read ONLY from environment variables.
  run        Run a command as a logged job (timeout, retries, repeat --every / daily --at,
             optional notify on failure). Logs go to a JSONL file + per-run output files.
  history    Show recent job runs from the JSONL log
  ports      List listening ports (with PID / process) or check specific ports
  wait-port  Block until host:port accepts connections (e.g. wait for ComfyUI to start)
  proc       Find processes by name/cmdline, show CPU/RAM; optional --kill (needs --yes)

Environment variables:
  TOOLKIT_WEBHOOK_URL   generic / Discord / Slack webhook URL (name overridable: --webhook-env)
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   for --channel telegram
  TOOLKIT2_HOME         where logs go (default ~/.toolkit2)
"""
import argparse
import datetime as dt
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess  # nosec B404 - subprocess is required to drive external tools; every call uses an argv list
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("JIANGKIT_HOME") or os.environ.get("TOOLKIT2_HOME") or (Path.home() / ".jiangkit"))


def _ps_quote(s: str) -> str:
    """PowerShell single-quoted literal (no $() / backtick expansion inside '...')."""
    return "'" + str(s).replace("'", "''") + "'"


# ---------------------------------------------------------------- notify
def desktop_notify(title, message):
    """Best-effort native desktop notification. Returns True if a notifier ran."""
    system = platform.system()
    try:
        if system == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], check=True, timeout=10)  # nosec B603 B607 - argv list without shell; executable and arguments are fixed or validated; external tool is looked up on PATH by design; argv list, no shell
            return True
        if system == "Darwin":
            script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
            subprocess.run(["osascript", "-e", script], check=True, timeout=10)  # nosec B603 B607 - argv list without shell; executable and arguments are fixed or validated; external tool is looked up on PATH by design; argv list, no shell
            return True
        if system == "Windows":
            ps = ("[Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')|Out-Null;"
                  "$n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                  f"$n.ShowBalloonTip(5000,{_ps_quote(title)},{_ps_quote(message)},'Info');"
                  "Start-Sleep 6;$n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])  # nosec B603 B607 - argv list without shell; executable and arguments are fixed or validated; external tool is looked up on PATH by design; argv list, no shell
            return True
    except (subprocess.SubprocessError, OSError):
        return False
    return False


def build_webhook_request(url, title, message, kind="auto"):
    """Return (url, payload_dict) for the webhook flavour."""
    if kind == "auto":
        if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
            kind = "discord"
        elif "hooks.slack.com" in url:
            kind = "slack"
        else:
            kind = "generic"
    text = f"**{title}**\n{message}" if title else message
    if kind == "discord":
        return url, {"content": text[:2000]}
    if kind == "slack":
        return url, {"text": text.replace("**", "*")}
    return url, {"title": title, "message": message, "text": text,
                 "host": socket.gethostname(),
                 "time": dt.datetime.now().isoformat(timespec="seconds")}


def build_telegram_request(title, message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars are required")
    return (f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat, "text": f"{title}\n{message}" if title else message})


def post_json(url, payload, timeout=15):
    # Webhook URLs come from the operator's own env vars (not untrusted input); still, only
    # http(s) is accepted so file:// or other urllib schemes can never be used.
    if not str(url).lower().startswith(("https://", "http://")):
        raise RuntimeError("webhook URL must start with https:// (or http://)")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "toolkit2-sys_tools/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - scheme checked above  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected - webhook URL comes from the operator's env var and is restricted to http(s) above
        return r.status


def redact(url):
    """Hide secrets in URLs when printing (tokens usually live in the path)."""
    if "/bot" in url:
        return url.split("/bot")[0] + "/bot***/" + url.rsplit("/", 1)[-1]
    parts = url.split("/")
    return "/".join(parts[:5] + ["***"]) if len(parts) > 5 else url


def notify(title, message, channels, webhook_env="TOOLKIT_WEBHOOK_URL", webhook_kind="auto",
           dry_run=False):
    results = {}
    for ch in channels:
        try:
            if ch == "terminal":
                print(f"\a🔔 {title}: {message}" if title else f"\a🔔 {message}")
                results[ch] = "ok"
            elif ch == "desktop":
                results[ch] = "ok" if (not dry_run and desktop_notify(title, message)) else \
                    ("dry-run" if dry_run else "unavailable")
            elif ch in ("webhook", "telegram"):
                if ch == "telegram":
                    url, payload = build_telegram_request(title, message)
                else:
                    url = os.environ.get(webhook_env)
                    if not url:
                        raise RuntimeError(f"env var {webhook_env} is not set")
                    url, payload = build_webhook_request(url, title, message, webhook_kind)
                if dry_run:
                    print(f"[dry-run] POST {redact(url)} {json.dumps(payload, ensure_ascii=False)}")
                    results[ch] = "dry-run"
                else:
                    results[ch] = f"HTTP {post_json(url, payload)}"
        except (RuntimeError, urllib.error.URLError, OSError) as e:
            results[ch] = f"error: {e}"
    return results


def cmd_notify(a):
    msg = a.message if a.message is not None else sys.stdin.read().strip()
    res = notify(a.title, msg, a.channel or ["terminal"], a.webhook_env, a.webhook_kind,
                 a.dry_run)
    for ch, r in res.items():
        print(f"  {ch}: {r}", file=sys.stderr)
    sys.exit(0 if all(not r.startswith("error") for r in res.values()) else 1)


# ---------------------------------------------------------------- job runner
def parse_interval(s):
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    s = s.strip().lower()
    if s[-1] in units:
        return float(s[:-1]) * units[s[-1]]
    return float(s)


def seconds_until(hhmm, now=None):
    now = now or dt.datetime.now()
    h, m = map(int, hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


def run_job(name, cmd, log_file, out_dir, timeout=None, retries=0, shell=False, cwd=None):
    """Run one job (with retries); append a JSON record to log_file; return the record."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        attempt += 1
        start = dt.datetime.now()
        stamp = start.strftime("%Y%m%d_%H%M%S_%f")[:-3]
        out_path = out_dir / f"{name}_{stamp}.log"
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                # --shell is an explicit opt-in for the operator's *own* command line (same trust as
                # typing it in a terminal, e.g. pipes in a cron job). It is never fed from files,
                # the network or the web panel. Default path uses an argument list, no shell.
                if shell:
                    proc = subprocess.run(" ".join(cmd), shell=True, cwd=cwd,  # nosec B602 # nosemgrep
                                          stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
                else:
                    proc = subprocess.run(cmd, cwd=cwd, stdout=fh,  # nosec B603 - argv list, no shell
                                          stderr=subprocess.STDOUT, timeout=timeout)
            code, status = proc.returncode, ("ok" if proc.returncode == 0 else "failed")
        except subprocess.TimeoutExpired:
            code, status = None, "timeout"
        except OSError as e:
            out_path.write_text(str(e), encoding="utf-8")
            code, status = None, "error"
        end = dt.datetime.now()
        rec = {"job": name, "cmd": shlex.join(cmd), "start": start.isoformat(timespec="seconds"),
               "end": end.isoformat(timespec="seconds"),
               "seconds": round((end - start).total_seconds(), 3), "status": status,
               "exit_code": code, "attempt": attempt, "output": str(out_path)}
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if status == "ok" or attempt > retries:
            return rec
        time.sleep(min(60, 2 ** attempt))


def tail(path, n=10):
    try:
        return "".join(Path(path).read_text(encoding="utf-8", errors="replace")
                       .splitlines(True)[-n:])
    except OSError:
        return ""


def cmd_run(a):
    cmd = a.command[1:] if a.command and a.command[0] == "--" else a.command
    if not cmd:
        sys.exit("No command given. Usage: sys_tools.py run --name job -- python script.py")
    name = a.name or Path(cmd[0]).stem
    log_file = Path(a.log) if a.log else HOME / "jobs.jsonl"
    out_dir = Path(a.out_dir) if a.out_dir else HOME / "job_logs"
    runs = 0
    worst = 0
    while True:
        if a.at:
            wait = seconds_until(a.at)
            print(f"[{name}] next run at {a.at} (in {wait / 60:.1f} min)", flush=True)
            time.sleep(wait)
        rec = run_job(name, cmd, log_file, out_dir, a.timeout, a.retries, a.shell, a.cwd)
        runs += 1
        print(f"[{name}] {rec['status']} exit={rec['exit_code']} {rec['seconds']}s "
              f"(attempt {rec['attempt']}) log={rec['output']}", flush=True)
        if a.show_output:
            print(tail(rec["output"], 20), end="")
        failed = rec["status"] != "ok"
        worst = worst or (rec["exit_code"] or 1 if failed else 0)
        if a.notify and (failed or a.notify_always):
            notify(f"Job {name}: {rec['status']}",
                   f"exit={rec['exit_code']} {rec['seconds']}s\n{tail(rec['output'], 5)}",
                   a.notify, dry_run=a.notify_dry_run)
        if a.times and runs >= a.times:
            break
        if a.every:
            time.sleep(parse_interval(a.every))
        elif not a.at:
            break
    sys.exit(worst)


def cmd_history(a):
    log_file = Path(a.log) if a.log else HOME / "jobs.jsonl"
    if not log_file.exists():
        print(f"No log at {log_file}")
        return
    recs = [json.loads(l) for l in log_file.read_text("utf-8").splitlines() if l.strip()]
    if a.job:
        recs = [r for r in recs if r["job"] == a.job]
    if a.failed:
        recs = [r for r in recs if r["status"] != "ok"]
    for r in recs[-a.last:]:
        print(f"{r['start']}  {r['job']:<16} {r['status']:<8} exit={str(r['exit_code']):<4} "
              f"{r['seconds']:>8}s  {r['cmd']}")


# ---------------------------------------------------------------- ports / processes
def port_open(host, port, timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def listening_ports():
    import psutil
    rows = []
    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        conns = []
    for c in conns:
        if c.status == psutil.CONN_LISTEN or (c.type == socket.SOCK_DGRAM and c.laddr):
            name = ""
            if c.pid:
                try:
                    name = psutil.Process(c.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    name = "?"
            rows.append({"proto": "tcp" if c.type == socket.SOCK_STREAM else "udp",
                         "addr": c.laddr.ip, "port": c.laddr.port, "pid": c.pid, "process": name})
    return sorted({(r["proto"], r["addr"], r["port"]): r for r in rows}.values(),
                  key=lambda r: (r["port"], r["proto"]))


def cmd_ports(a):
    if a.check:
        any_closed = False
        listen = {r["port"]: r for r in listening_ports()}
        for p in a.check:
            is_open = port_open(a.host, p)
            any_closed |= not is_open
            who = listen.get(p)
            extra = f" pid={who['pid']} ({who['process']})" if who and who["pid"] else ""
            print(f"{a.host}:{p}  {'OPEN' if is_open else 'closed'}{extra}")
        sys.exit(1 if any_closed else 0)
    rows = listening_ports()
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"{'proto':<6}{'address':<18}{'port':>6}  {'pid':>7}  process")
    for r in rows:
        if a.udp or r["proto"] == "tcp":
            print(f"{r['proto']:<6}{r['addr']:<18}{r['port']:>6}  {str(r['pid'] or '-'):>7}  "
                  f"{r['process']}")


def cmd_wait_port(a):
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        if port_open(a.host, a.port):
            print(f"{a.host}:{a.port} is up")
            return
        time.sleep(a.interval)
    print(f"Timed out after {a.timeout}s waiting for {a.host}:{a.port}", file=sys.stderr)
    sys.exit(1)


def find_procs(pattern, name_only=False):
    import psutil
    pat = pattern.lower()
    found = []
    # Never match ourselves or the shells/terminals that launched us
    skip = {os.getpid()}
    try:
        skip.update(p.pid for p in psutil.Process().parents())
    except psutil.Error:
        pass
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "create_time"]):
        try:
            cmdline = " ".join(p.info["cmdline"] or [])
            if p.pid in skip:
                continue
            if pat in (p.info["name"] or "").lower() or (not name_only and pat in cmdline.lower()):
                found.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def cmd_proc(a):
    import psutil
    procs = find_procs(a.pattern, a.name_only)
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(0.3)
    print(f"{'pid':>7} {'cpu%':>6} {'mem MB':>8}  {'started':<19} command")
    for p in procs:
        try:
            cpu = p.cpu_percent(None)
            mem = p.info["memory_info"].rss / 1e6 if p.info["memory_info"] else 0
            started = dt.datetime.fromtimestamp(p.info["create_time"]).strftime("%Y-%m-%d %H:%M:%S")
            cmd = " ".join(p.info["cmdline"] or [p.info["name"] or ""])[:100]
            print(f"{p.pid:>7} {cpu:>6.1f} {mem:>8.1f}  {started:<19} {cmd}")
        except psutil.Error:
            continue
    print(f"{len(procs)} process(es) match '{a.pattern}'")
    if a.kill:
        if not a.yes:
            print("[dry-run] add --yes to actually terminate these processes")
            return
        for p in procs:
            try:
                p.terminate()
            except psutil.Error as e:
                print(f"  cannot terminate {p.pid}: {e}")
        deadline = time.time() + 5
        alive = list(procs)
        while alive and time.time() < deadline:
            alive = [p for p in alive if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
            time.sleep(0.1)
        for p in alive:
            try:
                p.kill()
            except psutil.Error:
                pass
        print(f"Terminated {len(procs) - len(alive)}, force-killed {len(alive)}")


def build_parser():
    p = argparse.ArgumentParser(description="System toolkit: notify / job runner / ports",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Environment variables:" +
                                __doc__.split("Environment variables:", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("notify", help="Send notification")
    s.add_argument("message", nargs="?", help="Message (default: stdin)")
    s.add_argument("-t", "--title", default="")
    s.add_argument("-c", "--channel", action="append",
                   choices=["terminal", "desktop", "webhook", "telegram"],
                   help="Repeatable; default terminal")
    s.add_argument("--webhook-env", default="TOOLKIT_WEBHOOK_URL",
                   help="Env var holding the webhook URL")
    s.add_argument("--webhook-kind", choices=["auto", "discord", "slack", "generic"],
                   default="auto")
    s.add_argument("-n", "--dry-run", action="store_true", help="Print payload, don't send")
    s.set_defaults(func=cmd_notify)

    s = sub.add_parser("run", help="Run a command as a logged job",
                       description="Example: sys_tools.py run --name backup --every 1h -- "
                                   "python file_tools.py backup src dst")
    s.add_argument("--name")
    s.add_argument("--timeout", type=float)
    s.add_argument("--retries", type=int, default=0)
    s.add_argument("--every", help="Repeat interval, e.g. 30s, 10m, 2h")
    s.add_argument("--at", help="Run daily at HH:MM (loops)")
    s.add_argument("--times", type=int, help="Stop after N runs")
    s.add_argument("--shell", action="store_true", help="Run through the shell")
    s.add_argument("--cwd")
    s.add_argument("--log", help="JSONL log path (default ~/.toolkit2/jobs.jsonl)")
    s.add_argument("--out-dir", help="Per-run output folder")
    s.add_argument("--show-output", action="store_true")
    s.add_argument("--notify", action="append",
                   choices=["terminal", "desktop", "webhook", "telegram"],
                   help="Notify on failure through these channels")
    s.add_argument("--notify-always", action="store_true", help="Notify on success too")
    s.add_argument("--notify-dry-run", action="store_true")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("history", help="Show job history")
    s.add_argument("--log")
    s.add_argument("--job")
    s.add_argument("--failed", action="store_true")
    s.add_argument("--last", type=int, default=20)
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("ports", help="List listening ports or --check ports")
    s.add_argument("--check", type=int, nargs="+", help="Ports to test (exit 1 if any closed)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--udp", action="store_true", help="Include UDP")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_ports)

    s = sub.add_parser("wait-port", help="Wait for a port to open")
    s.add_argument("port", type=int)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--timeout", type=float, default=60)
    s.add_argument("--interval", type=float, default=0.5)
    s.set_defaults(func=cmd_wait_port)

    s = sub.add_parser("proc", help="Find processes")
    s.add_argument("pattern")
    s.add_argument("--name-only", action="store_true", help="Match process name only, not cmdline")
    s.add_argument("--kill", action="store_true", help="Terminate matches (dry-run without --yes)")
    s.add_argument("--yes", action="store_true", help="Confirm --kill")
    s.set_defaults(func=cmd_proc)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
