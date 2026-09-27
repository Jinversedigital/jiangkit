#!/usr/bin/env python3
"""code_weather.py - a weather forecast for a codebase.

Instead of a dashboard of numbers, the codebase gets a *forecast*:
  temperature  <- churn (lines changed recently, relative to size)
  pressure     <- cyclomatic complexity (radon for Python, keyword heuristic otherwise)
  humidity     <- TODO/FIXME/HACK/XXX density
  shelter      <- whether tests exist for the code
  visibility   <- file age (old, untouched, untested code = fog)

These combine into a storm-risk score per module, rendered as conditions
(Thunderstorms, Rain, Overcast, Fog, Partly cloudy, Sunny), an ASCII weather
map (a treemap whose areas scale with code size and whose fill pattern is the
weather), a plain-text forecast and a self-contained HTML report.
"""
from __future__ import annotations

import argparse
import html
import math
import os
import re
import subprocess  # nosec B404 - subprocess is required to drive external tools; every call uses an argv list
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from radon.complexity import cc_visit
except ImportError:  # pragma: no cover
    cc_visit = None

SOURCE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".c", ".h",
              ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".lua"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
             ".tox", ".mypy_cache", ".pytest_cache", "target", "vendor", ".idea"}
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
BRANCH_RE = re.compile(r"\b(if|elif|else if|for|while|case|catch|except|&&|\|\||\?)\b")

# name, unicode icon, ascii fill pattern, advice
CONDITIONS = {
    "Thunderstorms": ("⛈", "/\\", "heavy churn on complex or untested code: add tests before the next change"),
    "Rain":          ("🌧", "''", "changes are landing on fragile ground: review carefully"),
    "Overcast":      ("☁", "==", "some complexity or debt building up: schedule a cleanup"),
    "Fog":           ("🌫", "..", "old, untouched, untested: low visibility, nobody remembers how it works"),
    "Partly cloudy": ("⛅", "o-", "mostly fine, a few clouds"),
    "Sunny":         ("☀", "  ", "calm, simple and covered"),
}


@dataclass
class FileStats:
    path: str
    module: str
    lines: int = 0
    churn: int = 0
    commits: int = 0
    max_cc: float = 1.0
    avg_cc: float = 1.0
    worst_fn: str = ""
    todos: int = 0
    tested: bool = False
    is_test: bool = False
    age_days: float = 0.0
    risk: float = 0.0


@dataclass
class ModuleWeather:
    name: str
    files: list[FileStats] = field(default_factory=list)
    lines: int = 0
    churn: int = 0
    max_cc: float = 0
    todos: int = 0
    untested_ratio: float = 0.0
    median_age: float = 0.0
    risk: float = 0.0
    temp_c: float = 0.0
    condition: str = "Sunny"


# ------------------------------------------------------------------ scanning
def git(root: Path, *args) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,  # nosec B603 B607 - argv list without shell; executable and arguments are fixed or validated; external tool is looked up on PATH by design; argv list, no shell
                           errors="replace")
        return r.stdout if r.returncode == 0 else None
    except FileNotFoundError:
        return None


def list_sources(root: Path) -> list[Path]:
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            p = Path(dp) / fn
            if p.suffix in SOURCE_EXT:
                out.append(p)
    return sorted(out)


def is_test_file(rel: str) -> bool:
    name = Path(rel).name
    parts = Path(rel).parts
    return (name.startswith("test_") or re.search(r"(_test|\.test|\.spec|Test)\.\w+$", name) is not None
            or any(p in ("tests", "test", "__tests__", "spec") for p in parts[:-1]))


def module_of(rel: str, depth: int) -> str:
    parts = Path(rel).parts[:-1]
    return "/".join(parts[:depth]) + "/" if parts else "(root)"


def complexity(path: Path, text: str) -> tuple[float, float, str]:
    """Return (max, average, worst function name)."""
    if path.suffix == ".py" and cc_visit is not None:
        try:
            blocks = cc_visit(text)
        except (SyntaxError, ValueError):
            blocks = []
        if blocks:
            worst = max(blocks, key=lambda b: b.complexity)
            return (float(worst.complexity),
                    sum(b.complexity for b in blocks) / len(blocks), worst.name)
        return 1.0, 1.0, ""
    # heuristic for other languages: branch keywords per 25 code lines, +1
    n = len(BRANCH_RE.findall(text))
    lines = max(1, sum(1 for l in text.splitlines() if l.strip()))
    est = 1 + n / max(1, lines / 25)
    return est, est, "(heuristic)"


def scan(root: Path, days: int = 90, depth: int = 1) -> list[FileStats]:
    root = root.resolve()
    files = list_sources(root)
    rels = [p.relative_to(root).as_posix() for p in files]
    stats = {r: FileStats(path=r, module=module_of(r, depth), is_test=is_test_file(r)) for r in rels}
    # churn and commit counts from git
    log = git(root, "log", f"--since={days}.days", "--numstat", "--format=format:@@@")
    if log:
        for line in log.splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[2] in stats:
                a = int(parts[0]) if parts[0].isdigit() else 0
                d = int(parts[1]) if parts[1].isdigit() else 0
                stats[parts[2]].churn += a + d
                stats[parts[2]].commits += 1
    # age: last commit date per file (one git call), fallback to mtime
    last_seen: dict[str, int] = {}
    log2 = git(root, "log", "--name-only", "--format=@@@%ct")
    if log2:
        ts = None
        for line in log2.splitlines():
            if line.startswith("@@@"):
                ts = int(line[3:])
            elif line.strip() and ts is not None and line not in last_seen:
                last_seen[line] = ts
    now = time.time()
    test_blob = ""
    for p, r in zip(files, rels):
        text = p.read_text(errors="replace")
        st = stats[r]
        st.lines = sum(1 for l in text.splitlines() if l.strip())
        st.todos = len(TODO_RE.findall(text))
        st.max_cc, st.avg_cc, st.worst_fn = complexity(p, text)
        st.age_days = (now - last_seen.get(r, p.stat().st_mtime)) / 86400
        if st.is_test:
            test_blob += "\n" + p.name + "\n" + text
    # a file is "tested" if a test file names it or imports/mentions its stem
    for st in stats.values():
        if st.is_test:
            st.tested = True
            continue
        stem = Path(st.path).stem
        if stem == "__init__":
            stem = Path(st.path).parent.name or stem
        st.tested = bool(stem) and re.search(rf"\b{re.escape(stem)}\b", test_blob) is not None
    for st in stats.values():
        st.risk = file_risk(st)
    return list(stats.values())


# ------------------------------------------------------------------ forecasting
def norms(churn: float, lines: int, max_cc: float, todos: int) -> tuple[float, float, float]:
    churn_n = min(1.0, (churn / max(lines, 1)) / 1.5)
    cx_n = min(1.0, max(0.0, (max_cc - 4) / 16))
    todo_n = min(1.0, (todos * 100 / max(lines, 1)) / 3)
    return churn_n, cx_n, todo_n


def file_risk(st: FileStats) -> float:
    c, x, t = norms(st.churn, st.lines, st.max_cc, st.todos)
    untested = 0.0 if st.tested else 1.0
    # untested code only hurts when it is also changing or complex
    return round(0.35 * c + 0.30 * x + 0.15 * t + 0.20 * untested * max(c, x, 0.25), 3)


def classify(risk: float, untested_ratio: float, median_age: float, churn_n: float) -> str:
    if risk >= 0.55:
        return "Thunderstorms"
    if risk >= 0.40:
        return "Rain"
    if median_age > 365 and untested_ratio > 0.5 and churn_n < 0.05:
        return "Fog"
    if risk >= 0.28:
        return "Overcast"
    if risk >= 0.15:
        return "Partly cloudy"
    return "Sunny"


def forecast(files: list[FileStats]) -> list[ModuleWeather]:
    mods: dict[str, ModuleWeather] = {}
    for f in files:
        if f.is_test:
            continue  # tests are the shelter, not the weather
        m = mods.setdefault(f.module, ModuleWeather(f.module))
        m.files.append(f)
    for m in mods.values():
        m.lines = sum(f.lines for f in m.files)
        m.churn = sum(f.churn for f in m.files)
        m.max_cc = max(f.max_cc for f in m.files)
        m.todos = sum(f.todos for f in m.files)
        w = max(m.lines, 1)
        m.untested_ratio = sum(f.lines for f in m.files if not f.tested) / w
        ages = sorted(f.age_days for f in m.files)
        m.median_age = ages[len(ages) // 2]
        # line-weighted average of file risk, pulled up by the worst file
        avg = sum(f.risk * max(f.lines, 1) for f in m.files) / sum(max(f.lines, 1) for f in m.files)
        m.risk = round(0.6 * avg + 0.4 * max(f.risk for f in m.files), 3)
        churn_n = norms(m.churn, m.lines, 1, 0)[0]
        m.temp_c = round(4 + 32 * churn_n, 1)
        m.condition = classify(m.risk, m.untested_ratio, m.median_age, churn_n)
    return sorted(mods.values(), key=lambda m: -m.risk)


def forecast_text(mods: list[ModuleWeather], ascii_only: bool) -> str:
    lines = []
    for m in mods:
        icon = "" if ascii_only else CONDITIONS[m.condition][0] + " "
        worst = max(m.files, key=lambda f: f.risk)
        detail = [f"{m.temp_c:.0f}°C" if not ascii_only else f"{m.temp_c:.0f}C",
                  f"churn {m.churn} lines", f"max complexity {m.max_cc:.0f}",
                  f"{m.todos} TODOs", f"{(1 - m.untested_ratio) * 100:.0f}% covered by tests"]
        lines.append(f"{icon}{m.condition:<13} over {m.name:<20} ({', '.join(detail)})")
        if m.condition in ("Thunderstorms", "Rain", "Fog", "Overcast"):
            fn = f" (worst: {worst.worst_fn})" if worst.worst_fn and worst.worst_fn != "(heuristic)" else ""
            lines.append(f"    eye of the storm: {worst.path}{fn} - {CONDITIONS[m.condition][2]}")
    stormy = [m for m in mods if m.condition in ("Thunderstorms", "Rain")]
    if stormy:
        lines.append(f"\nOutlook: carry an umbrella in {', '.join(m.name for m in stormy)}.")
    else:
        lines.append("\nOutlook: clear skies across the codebase.")
    return "\n".join(lines)


def ascii_map(mods: list[ModuleWeather], width: int = 72) -> str:
    """Row-packed treemap: box area ~ lines of code, fill = weather pattern."""
    if not mods:
        return "(no source files)"
    total = sum(max(m.lines, 1) for m in mods)
    box_h = 5
    rows, cur, cur_w = [], [], 0
    for m in sorted(mods, key=lambda m: -m.lines):
        w = max(16, int(round(width * 2 * max(m.lines, 1) / total)))
        w = min(w, width)
        if cur and cur_w + w > width:
            rows.append(cur)
            cur, cur_w = [], 0
        cur.append((m, w))
        cur_w += w
    if cur:
        rows.append(cur)
    out = []
    for row in rows:
        # stretch the last box so each row is exactly `width` wide
        extra = width - sum(w for _, w in row)
        row[-1] = (row[-1][0], row[-1][1] + extra)
        grid = [[] for _ in range(box_h)]
        for m, w in row:
            pat = CONDITIONS[m.condition][1]
            inner = w - 2
            for y in range(box_h):
                if y in (0, box_h - 1):
                    grid[y].append("+" + "-" * inner + "+")
                    continue
                fill = (pat * (inner // 2 + 2))[(y % 2):(y % 2) + inner]
                if y == 1:
                    label = f" {m.name[: inner - 2]} "
                elif y == 2:
                    label = f" {m.condition} "[: inner]
                elif y == 3:
                    label = f" risk {m.risk:.2f} "[: inner]
                else:
                    label = ""
                if label:
                    s = (inner - len(label)) // 2
                    fill = fill[:s] + label + fill[s + len(label):]
                grid[y].append("|" + fill[:inner] + "|")
        out.extend("".join(parts) for parts in grid)
    legend = "  ".join(f"[{CONDITIONS[c][1]}] {c}" for c in CONDITIONS)
    return "\n".join(out) + "\n" + legend


def html_report(mods: list[ModuleWeather], files: list[FileStats], root: str, amap: str,
                text: str) -> str:
    def bar(v: float, color: str) -> str:
        return (f'<div class="bar"><span style="width:{min(100, v * 100):.0f}%;'
                f'background:{color}"></span></div>')
    colors = {"Thunderstorms": "#6a1b9a", "Rain": "#1565c0", "Overcast": "#607d8b",
              "Fog": "#9e9e9e", "Partly cloudy": "#f9a825", "Sunny": "#fbc02d"}
    cards = []
    for m in mods:
        icon = CONDITIONS[m.condition][0]
        c, x, t = norms(m.churn, m.lines, m.max_cc, m.todos)
        cards.append(f"""<div class="card" style="border-top:6px solid {colors[m.condition]}">
<div class="icon">{icon}</div><h3>{html.escape(m.name)}</h3>
<div class="cond">{m.condition} &middot; {m.temp_c:.0f}&deg;C &middot; risk {m.risk:.2f}</div>
<small>churn (temperature)</small>{bar(c, '#e53935')}
<small>complexity (pressure)</small>{bar(x, '#8e24aa')}
<small>TODO density (humidity)</small>{bar(t, '#1e88e5')}
<small>untested (no shelter)</small>{bar(m.untested_ratio, '#757575')}
<p>{html.escape(CONDITIONS[m.condition][2])}</p></div>""")
    rows = []
    for f in sorted((f for f in files if not f.is_test), key=lambda f: -f.risk):
        rows.append(f"<tr><td>{html.escape(f.path)}</td><td>{f.lines}</td><td>{f.churn}</td>"
                    f"<td>{f.max_cc:.0f} {html.escape(f.worst_fn)}</td><td>{f.todos}</td>"
                    f"<td>{'yes' if f.tested else '<b>no</b>'}</td><td>{f.age_days:.0f}</td>"
                    f"<td>{f.risk:.2f}</td></tr>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: file: 'self'; style-src 'unsafe-inline'"><title>Code weather: {html.escape(root)}</title>
<style>body{{font-family:system-ui,sans-serif;background:#f4f6f8;margin:24px;color:#222}}
.cards{{display:flex;flex-wrap:wrap;gap:14px}}.card{{background:#fff;border-radius:10px;padding:12px 16px;width:250px;box-shadow:0 1px 4px #0002}}
.icon{{font-size:42px;float:right}}.cond{{color:#555;margin-bottom:8px}}
.bar{{background:#eee;height:8px;border-radius:4px;margin:2px 0 6px}}.bar span{{display:block;height:8px;border-radius:4px}}
pre{{background:#10141a;color:#cfe3ff;padding:12px;border-radius:8px;overflow:auto}}
table{{border-collapse:collapse;background:#fff;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left;font-size:13px}}</style>
</head><body><h1>Code weather forecast</h1><p>{html.escape(root)} &mdash; generated {time.strftime('%Y-%m-%d %H:%M')}</p>
<div class="cards">{''.join(cards)}</div><h2>Weather map</h2><pre>{html.escape(amap)}</pre>
<h2>Forecast</h2><pre>{html.escape(text)}</pre>
<h2>Files by storm risk</h2><table><tr><th>file</th><th>lines</th><th>churn</th><th>max complexity</th>
<th>TODOs</th><th>tested</th><th>age (days)</th><th>risk</th></tr>{''.join(rows)}</table></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Print a weather forecast for a codebase (churn, complexity, TODOs, tests, age).",
        epilog="Example: code_weather.py ~/src/project --days 90 --html weather.html")
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--days", type=int, default=90, help="churn window in days")
    ap.add_argument("--depth", type=int, default=1, help="directory depth that defines a module")
    ap.add_argument("--html", help="write an HTML report here")
    ap.add_argument("--ascii", action="store_true", help="no emoji, pure ASCII output")
    ap.add_argument("--width", type=int, default=72, help="map width")
    args = ap.parse_args(argv)
    root = Path(args.path)
    if not root.is_dir():
        ap.error(f"not a directory: {root}")
    files = scan(root, args.days, args.depth)
    mods = forecast(files)
    amap = ascii_map(mods, args.width)
    text = forecast_text(mods, args.ascii)
    print(f"CODE WEATHER for {root.resolve()}  (churn window {args.days} days)\n")
    print(amap)
    print()
    print(text)
    if args.html:
        Path(args.html).write_text(html_report(mods, files, str(root.resolve()), amap,
                                               forecast_text(mods, False)), encoding="utf-8")
        print(f"\nHTML report -> {args.html}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
