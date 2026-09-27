#!/usr/bin/env python3
"""shell_timemachine.py - a flight recorder for your shell.

Every command you run (via the bash/zsh hook) is recorded with its cwd, exit
code, duration, a sanitized environment snapshot, a snapshot of the files in
the cwd, and which executable the command resolved to. Commands run through
`tm run -- CMD` (or the `tmr` shell function) additionally store their full
output.

The killer query is `diff`: "what changed since this command last worked?"
It finds the most recent failing run (or the run you name), finds the last
successful run of the *same* command, and diffs everything between them:
exit code, output, environment variables, cwd files, resolved binary.
With --rerun it executes the command again right now to get a fresh output
snapshot to compare against the last success.

Data lives in SQLite at $TM_HOME/history.db (default ~/.shell_timemachine).
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess  # nosec B404 - subprocess is required to drive external tools; every call uses an argv list
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Variables that change constantly and would drown out real differences.
NOISY_ENV = {"PWD", "OLDPWD", "SHLVL", "_", "COLUMNS", "LINES", "RANDOM", "SECONDS",
             "PROMPT_COMMAND", "PS1", "PS2", "PS4", "HISTCMD", "LINENO", "EPOCHSECONDS",
             "EPOCHREALTIME", "TERM_SESSION_ID", "WINDOWID", "SSH_AUTH_SOCK"}
# Values of variables whose names look sensitive are stored only as a short keyed hash
# (HMAC-SHA256 with a random per-install key in $TM_HOME/.hmac_key, mode 0600). A plain
# unsalted SHA-256 (the original design) lets anyone holding history.db brute-force short
# passwords/PINs offline; the keyed hash still lets `tm diff` show "value changed".
SECRET_RE = re.compile(r"(TOKEN|SECRET|PASS|PASSWD|PASSWORD|KEY|CREDENTIAL|AUTH|COOKIE|SESSION|PRIVATE|"
                       r"_PAT$|^PAT_|DSN|DATABASE_URL|CONN(ECTION)?_?STR|WEBHOOK|SIGNATURE|CERT)", re.I)
# Values that look like secrets regardless of the variable name.
SECRET_VALUE_RE = re.compile(r"(://[^/\s:@]+:[^/\s@]+@|\b(gh[pousr]_|github_pat_|sk-|sk_live_|xox[abprs]-|AKIA|AIza|"
                             r"eyJ[A-Za-z0-9_-]{10,}\.)|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
MAX_OUTPUT = 200_000          # bytes of output stored per run
MAX_FILES = 400               # cwd entries snapshotted per run


def tm_home() -> Path:
    p = Path(os.environ.get("TM_HOME", Path.home() / ".shell_timemachine"))
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(p, 0o700)  # history, env snapshots and outputs are private  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - 0o700/0o600 private permissions (this is the hardening, not a weakness)
    except OSError:
        pass
    return p


def _hmac_key() -> bytes:
    kf = tm_home() / ".hmac_key"
    try:
        data = kf.read_bytes()
        if len(data) >= 32:
            return data
    except OSError:
        pass
    import secrets

    data = secrets.token_bytes(32)
    fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return data


def secret_tag(value: str) -> str:
    import hmac

    return "<hmac-sha256:" + hmac.new(_hmac_key(), value.encode(), hashlib.sha256).hexdigest()[:12] + ">"


def redact_text(text: str | None) -> str | None:
    """Mask secrets in command lines / captured output before they are stored."""
    if not text:
        return text
    from jiangkit.security.redact import redact

    text = redact(text)
    text = re.sub(r"(://[^/\s:@]+:)[^/\s@]+@", r"\1[REDACTED]@", text)  # user:pass@host
    text = re.sub(r"(?i)(\s-p)(\S{3,})", r"\1[REDACTED]", text) if re.match(r"\s*(mysql|mysqldump|mariadb)\b", text) else text
    text = re.sub(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_KEY)[A-Z0-9_]*=)(\S+)", r"\1[REDACTED]", text)
    return text


def connect() -> sqlite3.Connection:
    dbp = tm_home() / "history.db"
    db = sqlite3.connect(dbp, timeout=10)
    try:
        os.chmod(dbp, 0o600)
    except OSError:
        pass
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL, session TEXT, cmd TEXT, cwd TEXT, exit_code INTEGER,
        duration REAL, env_json TEXT, files_json TEXT, binary_json TEXT,
        output TEXT, source TEXT)""")
    return db


# ---------------------------------------------------------------- snapshots
def sanitize_env(env: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in env.items():
        if k in NOISY_ENV or k.startswith(("TM_", "__tm", "BASH_FUNC")):
            continue
        if SECRET_RE.search(k) or SECRET_VALUE_RE.search(v or ""):
            v = secret_tag(v)
        out[k] = v
    return out


def read_env_file(path: str) -> dict[str, str]:
    """Parse the NUL-separated output of `env -0`."""
    data = Path(path).read_bytes().decode(errors="replace")
    env = {}
    for item in data.split("\0"):
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    return env


def snapshot_files(cwd: str) -> dict[str, list]:
    """Name -> [size, mtime] for the top-level entries of cwd (bounded)."""
    snap: dict[str, list] = {}
    try:
        with os.scandir(cwd) as it:
            for i, e in enumerate(sorted(it, key=lambda e: e.name)):
                if i >= MAX_FILES:
                    break
                try:
                    st = e.stat(follow_symlinks=False)
                    snap[e.name + ("/" if e.is_dir() else "")] = [st.st_size, round(st.st_mtime, 3)]
                except OSError:
                    pass
    except OSError:
        pass
    return snap


def resolve_binary(cmd: str, path_env: str | None) -> dict:
    """Which executable does the first word resolve to (path, size, mtime)?"""
    try:
        first = shlex.split(cmd)[0] if cmd.strip() else ""
    except ValueError:
        first = cmd.split()[0] if cmd.split() else ""
    if not first:
        return {}
    exe = shutil.which(first, path=path_env)
    if not exe:
        return {"name": first, "path": None}
    st = os.stat(exe)
    return {"name": first, "path": os.path.realpath(exe), "size": st.st_size,
            "mtime": round(st.st_mtime, 3)}


def read_files_file(path: str) -> dict[str, list]:
    """Parse `stat -c '%n\t%s\t%Y\t%F'` lines written synchronously by the hook."""
    snap: dict[str, list] = {}
    for line in Path(path).read_text(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[1].isdigit():
            name = parts[0] + ("/" if parts[3] == "directory" else "")
            snap[name] = [int(parts[1]), float(parts[2])]
    return snap


def insert_run(db, *, cmd, cwd, exit_code, duration=None, env=None, output=None,
               source="hook", session=None, ts=None, files=None) -> int:
    env = env if env is not None else dict(os.environ)
    clean = sanitize_env(env)
    if output is not None and len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + "\n...[truncated]\n"
    output = redact_text(output)
    cmd = redact_text(cmd) or ""
    cur = db.execute(
        "INSERT INTO runs (ts, session, cmd, cwd, exit_code, duration, env_json, files_json,"
        " binary_json, output, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ts or time.time(), session or str(os.getppid()), cmd.strip(), cwd, exit_code, duration,
         json.dumps(clean, sort_keys=True),
         json.dumps(files if files is not None else snapshot_files(cwd)),
         json.dumps(resolve_binary(cmd, env.get("PATH"))), output, source))
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------- commands
def cmd_record(args) -> int:
    try:
        env = read_env_file(args.env_file) if args.env_file else dict(os.environ)
        output = Path(args.output_file).read_text(errors="replace") if args.output_file else None
        if args.cmd.strip().split(" ")[0] in ("tm", "tmr") or not args.cmd.strip():
            return 0  # don't record the time machine itself / empty lines
        files = read_files_file(args.files_file) if args.files_file else None
        with connect() as db:
            insert_run(db, cmd=args.cmd, cwd=args.cwd, exit_code=args.exit, duration=args.duration,
                       env=env, output=output, source=args.source, session=args.session,
                       ts=args.ts, files=files)
        return 0
    finally:
        # Always remove the raw env snapshot (it contains un-hashed secrets). The original
        # code returned early for `tm` commands and left these files behind.
        for f in (args.env_file, args.files_file):
            if f:
                try:
                    os.unlink(f)
                except OSError:
                    pass


def execute(cmd: str, cwd: str, echo: bool = True) -> tuple[int, str, float]:
    """Run cmd through the user's shell, teeing combined output."""
    shell = os.environ.get("SHELL", "/bin/bash")
    if not Path(shell).exists():
        shell = "/bin/sh"
    t0 = time.time()
    proc = subprocess.Popen([shell, "-c", cmd], cwd=cwd, stdout=subprocess.PIPE,  # nosec B603 # nosemgrep - `tm run <cmd>` executes the operator's OWN command line in their shell by design (like `time`); never reachable from untrusted input
                            stderr=subprocess.STDOUT)
    chunks = []
    if proc.stdout is None:
        raise RuntimeError("stdout pipe missing")
    for line in iter(proc.stdout.readline, b""):
        chunks.append(line)
        if echo:
            sys.stdout.buffer.write(line)
            sys.stdout.flush()
    proc.wait()
    return proc.returncode, b"".join(chunks).decode(errors="replace"), time.time() - t0


def cmd_run(args) -> int:
    if not args.command:
        print("usage: tm run -- COMMAND ...", file=sys.stderr)
        return 2
    cmd = args.command[0] if len(args.command) == 1 else shlex.join(args.command)
    code, output, dur = execute(cmd, os.getcwd(), echo=not args.quiet)
    with connect() as db:
        insert_run(db, cmd=cmd, cwd=os.getcwd(), exit_code=code, duration=dur,
                   output=output, source="wrapper")
    return code


def fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def cmd_log(args) -> int:
    with connect() as db:
        q, params = "SELECT * FROM runs", []
        if args.grep:
            q += " WHERE cmd LIKE ?"
            params.append(f"%{args.grep}%")
        rows = db.execute(q + " ORDER BY ts DESC, id DESC LIMIT ?", (*params, args.n)).fetchall()
    for r in reversed(rows):
        mark = "ok " if r["exit_code"] == 0 else f"E{r['exit_code']:<2}"
        out = "*" if r["output"] is not None else " "
        print(f"#{r['id']:<5} {fmt_ts(r['ts'])} [{mark}]{out} {r['cwd']}$ {r['cmd']}")
    if not rows:
        print("(no history yet)")
    return 0


def find_pair(db, query: str | None, run_id: int | None):
    """Return (target_run, last_success_before_it) for the diff."""
    if run_id:
        target = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    elif query:
        target = db.execute("SELECT * FROM runs WHERE cmd LIKE ? ORDER BY ts DESC, id DESC LIMIT 1",
                            (f"%{query}%",)).fetchone()
    else:  # most recent failure
        target = db.execute("SELECT * FROM runs WHERE exit_code != 0 ORDER BY ts DESC, id DESC LIMIT 1"
                            ).fetchone()
    if target is None:
        return None, None
    earlier = "cmd=? AND exit_code=0 AND (ts<? OR (ts=? AND id<?))"
    key = (target["cmd"], target["ts"], target["ts"], target["id"])
    good = db.execute(f"SELECT * FROM runs WHERE {earlier} AND output IS NOT NULL "  # nosec B608 - only a constant SQL fragment is interpolated; all values are bound parameters
                      "ORDER BY ts DESC, id DESC LIMIT 1", key).fetchone()
    # prefer the *latest* success, only fall back to one with output if needed
    latest = db.execute(f"SELECT * FROM runs WHERE {earlier} ORDER BY ts DESC, id DESC LIMIT 1",  # nosec B608 - only a constant SQL fragment is interpolated; all values are bound parameters
                        key).fetchone()
    if latest is not None and (latest["output"] is not None or good is None
                               or good["output"] is None):
        good = latest
    return target, good


def diff_dicts(old: dict, new: dict) -> list[str]:
    lines = []
    for k in sorted(set(old) | set(new)):
        if k not in new:
            lines.append(f"  - {k} (removed; was {old[k]!r:.80})")
        elif k not in old:
            lines.append(f"  + {k}={new[k]!r:.80}")
        elif old[k] != new[k]:
            is_list = k.endswith("PATH") or all(":" in str(x) and "/" in str(x)
                                                 and not str(x).startswith(("<sha256", "<hmac"))
                                                 for x in (old[k], new[k]))
            if is_list:
                o, n = str(old[k]).split(":"), str(new[k]).split(":")
                added = [p for p in n if p not in o]
                removed = [p for p in o if p not in n]
                order = "" if added or removed else " (order changed)"
                lines.append(f"  ~ {k}: +{added} -{removed}{order}")
            else:
                lines.append(f"  ~ {k}: {old[k]!r:.60} -> {new[k]!r:.60}")
    return lines


def diff_files(old: dict, new: dict) -> list[str]:
    lines = []
    for k in sorted(set(old) | set(new)):
        if k not in new:
            lines.append(f"  - {k}")
        elif k not in old:
            lines.append(f"  + {k}")
        elif old[k] != new[k] and not k.endswith("/"):
            lines.append(f"  ~ {k} (size {old[k][0]} -> {new[k][0]})")
    return lines


def build_report(good, now: dict) -> str:
    """now: dict with keys like a runs row (cmd, cwd, exit_code, env_json, ...)."""
    out = []
    out.append(f"command : {now['cmd']}")
    out.append(f"worked  : run #{good['id']} at {fmt_ts(good['ts'])} (exit 0) in {good['cwd']}")
    label = f"run #{now['id']}" if now.get("id") else "re-run just now"
    out.append(f"now     : {label} at {fmt_ts(now['ts'])} (exit {now['exit_code']}) in {now['cwd']}")
    hrs = (now["ts"] - good["ts"]) / 3600
    out.append(f"elapsed : {hrs:.2f} h between the two runs")
    findings = 0
    if good["cwd"] != now["cwd"]:
        out.append(f"\n[cwd] directory changed: {good['cwd']} -> {now['cwd']}")
        findings += 1
    b_old, b_new = json.loads(good["binary_json"] or "{}"), json.loads(now["binary_json"] or "{}")
    if b_old != b_new:
        findings += 1
        out.append("\n[binary] the command now resolves differently:")
        out.append(f"  was: {b_old}")
        out.append(f"  now: {b_new}")
    env_lines = diff_dicts(json.loads(good["env_json"]), json.loads(now["env_json"]))
    if env_lines:
        findings += 1
        out.append(f"\n[env] {len(env_lines)} environment change(s):")
        out.extend(env_lines)
    f_lines = diff_files(json.loads(good["files_json"]), json.loads(now["files_json"]))
    if f_lines:
        findings += 1
        out.append(f"\n[files] {len(f_lines)} change(s) in the working directory:")
        out.extend(f_lines[:40])
        if len(f_lines) > 40:
            out.append(f"  ... {len(f_lines) - 40} more")
    if good["output"] is not None and now.get("output") is not None:
        d = list(difflib.unified_diff(good["output"].splitlines(), now["output"].splitlines(),
                                      "output@worked", "output@now", lineterm="", n=2))
        if d:
            findings += 1
            out.append("\n[output] diff of captured output:")
            out.extend("  " + x for x in d[:120])
    else:
        out.append("\n[output] (no captured output on both sides; use `tm run --` or --rerun)")
    if findings == 0:
        out.append("\nNo recorded differences - the change is probably outside what was "
                   "snapshotted (network, remote service, files outside cwd).")
    return "\n".join(out)


def cmd_diff(args) -> int:
    with connect() as db:
        target, good = find_pair(db, args.query, args.id)
        if target is None:
            print("no matching run found")
            return 1
        if good is None:
            print(f"no earlier successful run of: {target['cmd']}")
            return 1
        now = dict(target)
        if args.rerun and "[REDACTED" in (target["cmd"] or ""):
            print("refusing --rerun: the stored command had secrets masked; run it manually")
            return 1
        if args.rerun:
            code, output, dur = execute(target["cmd"], target["cwd"] if os.path.isdir(target["cwd"])
                                        else os.getcwd(), echo=False)
            env = dict(os.environ)
            now = {"id": None, "ts": time.time(), "cmd": target["cmd"], "cwd": target["cwd"],
                   "exit_code": code, "env_json": json.dumps(sanitize_env(env), sort_keys=True),
                   "files_json": json.dumps(snapshot_files(target["cwd"])),
                   "binary_json": json.dumps(resolve_binary(target["cmd"], env.get("PATH"))),
                   "output": output}
            insert_run(db, cmd=target["cmd"], cwd=target["cwd"], exit_code=code, duration=dur,
                       env=env, output=output, source="rerun")
    print(build_report(good, now))
    return 0


def cmd_hook(args) -> int:
    path = HERE / f"hook.{args.shell}"
    print(path.read_text().replace("@TM_PY@", shlex.quote(sys.executable))
          .replace("@TM_SCRIPT@", shlex.quote(str(Path(__file__).resolve()))))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="tm", description="Shell time machine: record commands and ask "
        "'what changed since this command last worked?'",
        epilog="Setup: eval \"$(python3 shell_timemachine.py hook bash)\"   then later: tm diff")
    sub = ap.add_subparsers(dest="sub", required=True)
    p = sub.add_parser("hook", help="print the shell integration snippet")
    p.add_argument("shell", choices=["bash", "zsh"])
    p.set_defaults(fn=cmd_hook)
    p = sub.add_parser("record", help="(used by the hook) record one finished command")
    p.add_argument("--cmd", required=True)
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--exit", type=int, required=True)
    p.add_argument("--duration", type=float)
    p.add_argument("--env-file", help="file with `env -0` output (deleted after reading)")
    p.add_argument("--output-file")
    p.add_argument("--files-file", help="file with stat lines of cwd (deleted after reading)")
    p.add_argument("--ts", type=float, help="epoch time the command started")
    p.add_argument("--session")
    p.add_argument("--source", default="hook")
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("run", help="run a command and record its full output")
    p.add_argument("-q", "--quiet", action="store_true", help="don't echo output")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("log", help="show recorded history")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--grep")
    p.set_defaults(fn=cmd_log)
    p = sub.add_parser("diff", help="what changed since this command last worked?")
    p.add_argument("query", nargs="?", help="substring of the command (default: last failure)")
    p.add_argument("--id", type=int, help="compare this specific run id")
    p.add_argument("--rerun", action="store_true", help="re-execute now for a fresh snapshot")
    p.set_defaults(fn=cmd_diff)
    args = ap.parse_args(argv)
    if getattr(args, "command", None) and args.command[:1] == ["--"]:
        args.command = args.command[1:]
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
