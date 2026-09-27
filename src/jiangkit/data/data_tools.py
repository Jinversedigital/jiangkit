#!/usr/bin/env python3
"""data_tools.py - tabular data utilities (CSV / Excel / JSON / JSONL).

Subcommands:
  convert   Convert between .csv .tsv .xlsx .json .jsonl (format picked by extension)
  merge     Stack files vertically, or join them on a key column
  dedupe    Remove duplicate rows (optionally by a subset of columns)
  filter    Keep rows matching conditions, e.g. --where "likes>=100" --where "platform==IG"
  sort      Sort by one or more columns
  select    Keep / reorder / rename columns
  stats     Quick summary: row count, dtypes, nulls, numeric describe, top values
  chart     Bar / line chart to PNG via matplotlib
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd


def read_any(path, sheet=None):
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".csv":
        return pd.read_csv(p, encoding="utf-8-sig")
    if ext == ".tsv":
        return pd.read_csv(p, sep="\t", encoding="utf-8-sig")
    if ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(p, sheet_name=sheet or 0)
    if ext == ".jsonl":
        return pd.read_json(p, lines=True)
    if ext == ".json":
        return pd.read_json(p)
    raise ValueError(f"Unsupported input format: {ext}")


# Spreadsheet formula-injection protection for CSV/TSV/XLSX output (openpyxl also turns any
# string starting with "=" into a live formula). Disable with --no-formula-escape.
ESCAPE_FORMULAS = True


def write_any(df, path, sheet="Sheet1"):
    p = Path(path)
    if ESCAPE_FORMULAS and p.suffix.lower() in (".csv", ".tsv", ".xlsx"):
        from jiangkit.security.csvsafe import escape_dataframe

        df = escape_dataframe(df)
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = p.suffix.lower()
    if ext == ".csv":
        # utf-8-sig so Excel on Windows opens Chinese text correctly
        df.to_csv(p, index=False, encoding="utf-8-sig")
    elif ext == ".tsv":
        df.to_csv(p, index=False, sep="\t", encoding="utf-8")
    elif ext == ".xlsx":
        df.to_excel(p, index=False, sheet_name=sheet)
    elif ext == ".jsonl":
        df.to_json(p, orient="records", lines=True, force_ascii=False)
    elif ext == ".json":
        df.to_json(p, orient="records", force_ascii=False, indent=2)
    else:
        raise ValueError(f"Unsupported output format: {ext}")
    print(f"Wrote {len(df)} rows x {len(df.columns)} cols -> {p}")


COND_RE = re.compile(r"^\s*(.+?)\s*(==|!=|>=|<=|>|<|~=|!~|\^=|\$=)\s*(.*?)\s*$")


def _coerce(series, value):
    """Convert the string value to the series' dtype when possible."""
    if pd.api.types.is_numeric_dtype(series):
        try:
            return float(value)
        except ValueError:
            pass
    return value


def apply_condition(df, cond):
    """Supported ops: == != > >= < <= ~= (contains / regex) !~ (not contains)
    ^= (startswith) $= (endswith)."""
    m = COND_RE.match(cond)
    if not m:
        raise ValueError(f"Cannot parse condition: {cond}")
    col, op, val = m.groups()
    if col not in df.columns:
        raise ValueError(f"Unknown column '{col}'. Available: {list(df.columns)}")
    s = df[col]
    v = _coerce(s, val)
    if op == "==":
        mask = s == v if not isinstance(v, str) else s.astype(str) == v
    elif op == "!=":
        mask = s != v if not isinstance(v, str) else s.astype(str) != v
    elif op in (">", ">=", "<", "<="):
        mask = {">": s > v, ">=": s >= v, "<": s < v, "<=": s <= v}[op]
    elif op == "~=":
        mask = s.astype(str).str.contains(val, regex=True, na=False)
    elif op == "!~":
        mask = ~s.astype(str).str.contains(val, regex=True, na=False)
    elif op == "^=":
        mask = s.astype(str).str.startswith(val, na=False)
    else:
        mask = s.astype(str).str.endswith(val, na=False)
    return df[mask]


def cmd_convert(a):
    write_any(read_any(a.input, a.sheet), a.output)


def cmd_merge(a):
    frames = [read_any(f) for f in a.inputs]
    if a.on:
        df = frames[0]
        for f in frames[1:]:
            df = df.merge(f, on=a.on, how=a.how)
    else:
        if a.source_col:
            for f, name in zip(frames, a.inputs):
                f[a.source_col] = Path(name).name
        df = pd.concat(frames, ignore_index=True)
    write_any(df, a.output)


def cmd_dedupe(a):
    df = read_any(a.input)
    before = len(df)
    df = df.drop_duplicates(subset=a.cols or None, keep=a.keep)
    print(f"Removed {before - len(df)} duplicate rows")
    write_any(df, a.output)


def cmd_filter(a):
    df = read_any(a.input)
    for cond in a.where:
        df = apply_condition(df, cond)
    if a.query:
        df = df.query(a.query)
    write_any(df, a.output)


def cmd_sort(a):
    df = read_any(a.input)
    cols, asc = [], []
    for c in a.by:
        # "col:desc" / "col:asc"; a leading "-" also means descending
        name, _, order = c.partition(":")
        desc = order.lower() == "desc" or name.startswith("-")
        cols.append(name.lstrip("-"))
        asc.append(not desc)
    write_any(df.sort_values(cols, ascending=asc, kind="stable"), a.output)


def cmd_select(a):
    df = read_any(a.input)
    if a.cols:
        df = df[a.cols]
    if a.drop:
        df = df.drop(columns=a.drop)
    if a.rename:
        mapping = dict(r.split("=", 1) for r in a.rename)
        df = df.rename(columns=mapping)
    write_any(df, a.output)


def summarize(df, top=5):
    lines = [f"Rows: {len(df)}  Columns: {len(df.columns)}", "", "Column overview:"]
    for c in df.columns:
        lines.append(f"  {c:<24} {str(df[c].dtype):<10} nulls={int(df[c].isna().sum()):<6}"
                     f" unique={df[c].nunique()}")
    num = df.select_dtypes("number")
    if not num.empty:
        lines += ["", "Numeric summary:", num.describe().T.round(3).to_string()]
    obj = df.select_dtypes(exclude="number")
    for c in obj.columns:
        vc = obj[c].value_counts().head(top)
        lines += ["", f"Top values of '{c}':"] + [f"  {k}: {v}" for k, v in vc.items()]
    return "\n".join(lines)


def cmd_stats(a):
    text = summarize(read_any(a.input, a.sheet), a.top)
    print(text)
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")


def setup_cjk_font():
    """Pick an installed CJK font so Chinese labels render instead of boxes."""
    import matplotlib
    from matplotlib import font_manager
    wanted = ["Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Sans CJK SC", "Microsoft JhengHei",
              "PingFang TC", "Heiti TC", "WenQuanYi Zen Hei", "Arial Unicode MS"]
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for w in wanted:
        if w in installed:
            matplotlib.rcParams["font.sans-serif"] = [w] + matplotlib.rcParams["font.sans-serif"]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def cmd_chart(a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    setup_cjk_font()
    df = read_any(a.input, a.sheet)
    ys = a.y or [c for c in df.select_dtypes("number").columns if c != a.x]
    if not ys:
        sys.exit("No numeric columns to plot; pass --y")
    if a.agg:
        df = df.groupby(a.x, sort=False)[ys].agg(a.agg).reset_index()
    if a.top:
        df = df.sort_values(ys[0], ascending=False).head(a.top)
    fig, ax = plt.subplots(figsize=(a.width, a.height), dpi=a.dpi)
    extra = {"marker": "o"} if a.kind == "line" else {}
    df.plot(x=a.x, y=ys, kind=a.kind, ax=ax, **extra)
    ax.set_title(a.title or f"{', '.join(ys)} by {a.x}")
    ax.set_xlabel(a.x)
    ax.grid(axis="y", alpha=0.3)
    plt.xticks(rotation=a.rotate, ha="right" if a.rotate else "center")
    fig.tight_layout()
    fig.savefig(a.output)
    print(f"Chart -> {a.output}")


def build_parser():
    p = argparse.ArgumentParser(description="Tabular data toolkit (CSV/Excel/JSON/JSONL)")
    p.add_argument("--no-formula-escape", action="store_true",
                   help="do not prefix =,+,-,@ cells with ' in CSV/XLSX output (formula-injection guard)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def io_args(s, multi=False):
        if multi:
            s.add_argument("inputs", nargs="+")
        else:
            s.add_argument("input")
        s.add_argument("-o", "--output", required=True)

    s = sub.add_parser("convert", help="Convert formats by extension")
    io_args(s)
    s.add_argument("--sheet", help="Excel sheet name")
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser("merge", help="Concatenate or join files")
    io_args(s, multi=True)
    s.add_argument("--on", nargs="+", help="Join key column(s); omit to stack rows")
    s.add_argument("--how", choices=["inner", "left", "right", "outer"], default="outer")
    s.add_argument("--source-col", help="When stacking, add a column with the source filename")
    s.set_defaults(func=cmd_merge)

    s = sub.add_parser("dedupe", help="Drop duplicate rows")
    io_args(s)
    s.add_argument("--cols", nargs="+", help="Only compare these columns")
    s.add_argument("--keep", choices=["first", "last"], default="first")
    s.set_defaults(func=cmd_dedupe)

    s = sub.add_parser("filter", help="Filter rows",
                       description="Ops: == != > >= < <= ~=(regex contains) !~ ^= $=")
    io_args(s)
    s.add_argument("-w", "--where", action="append", default=[],
                   help='Condition, repeatable (AND): "likes>=100"')
    s.add_argument("-q", "--query", help="Advanced pandas query expression")
    s.set_defaults(func=cmd_filter)

    s = sub.add_parser("sort", help="Sort rows; use col:desc for descending")
    io_args(s)
    s.add_argument("--by", nargs="+", required=True, help="e.g. --by platform likes:desc")
    s.set_defaults(func=cmd_sort)

    s = sub.add_parser("select", help="Select / drop / rename columns")
    io_args(s)
    s.add_argument("--cols", nargs="+", help="Columns to keep, in this order")
    s.add_argument("--drop", nargs="+")
    s.add_argument("--rename", nargs="+", help="old=new pairs")
    s.set_defaults(func=cmd_select)

    s = sub.add_parser("stats", help="Quick summary")
    s.add_argument("input")
    s.add_argument("--sheet")
    s.add_argument("--top", type=int, default=5)
    s.add_argument("-o", "--output", help="Also save summary text")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("chart", help="Bar/line chart to PNG")
    io_args(s)
    s.add_argument("--x", required=True)
    s.add_argument("--y", nargs="+", help="Numeric column(s); default all numeric")
    s.add_argument("--kind", choices=["bar", "line", "barh"], default="bar")
    s.add_argument("--agg", choices=["sum", "mean", "count", "max", "min"],
                   help="Group by --x and aggregate first")
    s.add_argument("--top", type=int, help="Keep top N rows by first y")
    s.add_argument("--title")
    s.add_argument("--sheet")
    s.add_argument("--width", type=float, default=10)
    s.add_argument("--height", type=float, default=5.5)
    s.add_argument("--dpi", type=int, default=120)
    s.add_argument("--rotate", type=int, default=0, help="X label rotation")
    s.set_defaults(func=cmd_chart)
    return p


def main(argv=None):
    global ESCAPE_FORMULAS
    args = build_parser().parse_args(argv)
    ESCAPE_FORMULAS = not args.no_formula_escape
    try:
        args.func(args)
    except (ValueError, KeyError, FileNotFoundError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
