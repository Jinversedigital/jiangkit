#!/usr/bin/env python3
"""social_tools.py - social media content helpers.

Subcommands:
  calendar  Generate a content calendar (CSV or Markdown) for N days x slots x platforms
  tags      Hashtag group manager: add / list / remove / pick (random or rotating)
  check     Caption length checker against per-platform limits (X weighted counting)
  utm       Build UTM-tagged links (single or batch from CSV)
  qr        Generate a QR code as PNG or SVG
"""
from jiangkit.security.csvsafe import SafeDictWriter, SafeWriter  # CSV formula-injection escaping
import argparse
import csv
import datetime as dt
import json
import os
import random
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Per-platform limits (as of 2026; adjust in one place if platforms change)
PLATFORM_LIMITS = {
    "IG":       {"chars": 2200, "hashtags": 30, "mentions": 20, "preview": 125},
    "X":        {"chars": 280, "hashtags": 3, "mentions": None, "preview": None, "weighted": True},
    "Threads":  {"chars": 500, "hashtags": 1, "mentions": None, "preview": None},
    "Patreon":  {"chars": None, "hashtags": 10, "mentions": None, "preview": 200},
    "TikTok":   {"chars": 4000, "hashtags": 10, "mentions": None, "preview": 100},
    "YouTube":  {"chars": 5000, "hashtags": 15, "mentions": None, "preview": 100},
    "Facebook": {"chars": 63206, "hashtags": 10, "mentions": None, "preview": 125},
}
PLATFORM_ALIASES = {p.lower(): p for p in PLATFORM_LIMITS}
PLATFORM_ALIASES.update({"instagram": "IG", "twitter": "X", "yt": "YouTube", "fb": "Facebook"})
DEFAULT_STORE = Path(os.environ.get("TOOLKIT2_HASHTAGS",
                                    Path.home() / ".toolkit2" / "hashtags.json"))
WEEKDAYS_ZH = ["一", "二", "三", "四", "五", "六", "日"]


def norm_platform(p):
    key = p.strip().lower()
    if key not in PLATFORM_ALIASES:
        raise ValueError(f"Unknown platform '{p}'. Known: {', '.join(PLATFORM_LIMITS)}")
    return PLATFORM_ALIASES[key]


def split_list(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else []


# ---------------------------------------------------------------- hashtag store
def load_store(path):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    return {"groups": {}, "rotation": {}}


def save_store(store, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, ensure_ascii=False, indent=2), "utf-8")


def clean_tag(t):
    t = t.strip().lstrip("#").strip()
    return f"#{t}" if t else ""


def pick_tags(store, groups, count=None, platform=None, mode="random", seed=None,
              always=None):
    """Pick hashtags from one or more groups honouring the platform max."""
    pool = []
    for g in groups:
        if g not in store["groups"]:
            raise ValueError(f"No hashtag group '{g}'. Have: {', '.join(store['groups']) or '-'}")
        pool.extend(t for t in store["groups"][g] if t not in pool)
    fixed = [clean_tag(t) for t in (always or []) if clean_tag(t)]
    pool = [t for t in pool if t not in fixed]
    limit = PLATFORM_LIMITS[platform]["hashtags"] if platform else None
    n = count if count is not None else (limit if limit is not None else len(pool) + len(fixed))
    if limit is not None:
        n = min(n, limit)
    n_rest = max(0, n - len(fixed))
    if mode == "rotate":
        key = "+".join(groups)
        off = store.setdefault("rotation", {}).get(key, 0) % max(1, len(pool))
        ordered = pool[off:] + pool[:off]
        chosen = ordered[:n_rest]
        store["rotation"][key] = (off + n_rest) % max(1, len(pool))
    elif mode == "random":
        rng = random.Random(seed)  # nosec B311 - non-cryptographic randomness (timing jitter / visuals), not security relevant
        chosen = rng.sample(pool, min(n_rest, len(pool)))
    else:  # ordered
        chosen = pool[:n_rest]
    return (fixed + chosen)[:n]


def cmd_tags(a):
    store = load_store(a.store)
    if a.action == "add":
        tags = [clean_tag(t) for t in re.split(r"[,\s]+", " ".join(a.tags)) if clean_tag(t)]
        grp = store["groups"].setdefault(a.group, [])
        added = [t for t in dict.fromkeys(tags) if t not in grp]  # dedupe, keep order
        grp.extend(added)
        save_store(store, a.store)
        print(f"Group '{a.group}': +{len(added)} (total {len(grp)})")
    elif a.action == "remove":
        if not a.tags:
            store["groups"].pop(a.group, None)
            print(f"Removed group '{a.group}'")
        else:
            rm = {clean_tag(t) for t in a.tags}
            store["groups"][a.group] = [t for t in store["groups"].get(a.group, []) if t not in rm]
            print(f"Group '{a.group}' now has {len(store['groups'][a.group])} tags")
        save_store(store, a.store)
    elif a.action == "list":
        for g, tags in store["groups"].items():
            if a.group and g != a.group:
                continue
            print(f"[{g}] ({len(tags)}) {' '.join(tags)}")
    elif a.action == "pick":
        groups = split_list(a.group)
        plat = norm_platform(a.platform) if a.platform else None
        tags = pick_tags(store, groups, a.count, plat, a.mode, a.seed, split_list(a.always))
        if a.mode == "rotate":
            save_store(store, a.store)
        print(" ".join(tags))


# ---------------------------------------------------------------- caption check
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
HASHTAG_RE = re.compile(r"(?<![\w#])#[^\s#.,!?;:，。！？、]+", re.U)
MENTION_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_.]+")


def x_weighted_length(text):
    """Approximate X/Twitter weighted length: URLs = 23, CJK/emoji = 2, Latin = 1."""
    total = 0
    last = 0
    for m in URL_RE.finditer(text):
        total += _weigh(text[last:m.start()]) + 23
        last = m.end()
    return total + _weigh(text[last:])


def _weigh(s):
    n = 0
    s = unicodedata.normalize("NFC", s)
    for ch in s:
        cp = ord(ch)
        if cp in (0x200D, 0xFE0F):  # emoji joiners / variation selectors count 0
            continue
        light = (cp <= 4351 or 8192 <= cp <= 8205 or 8208 <= cp <= 8223 or 8242 <= cp <= 8247)
        n += 1 if light else 2
    return n


def check_caption(text, platform):
    lim = PLATFORM_LIMITS[platform]
    length = x_weighted_length(text) if lim.get("weighted") else len(text)
    tags = HASHTAG_RE.findall(text)
    mentions = MENTION_RE.findall(text)
    issues = []
    if lim["chars"] and length > lim["chars"]:
        issues.append(f"too long by {length - lim['chars']}")
    if lim["hashtags"] is not None and len(tags) > lim["hashtags"]:
        issues.append(f"{len(tags)} hashtags > max {lim['hashtags']}")
    if lim["mentions"] is not None and len(mentions) > lim["mentions"]:
        issues.append(f"{len(mentions)} mentions > max {lim['mentions']}")
    first_line = text.strip().split("\n")[0]
    warn = []
    if lim.get("preview") and len(first_line) > lim["preview"]:
        warn.append(f"first line {len(first_line)} chars; only ~{lim['preview']} show before 'more'")
    return {"platform": platform, "length": length, "limit": lim["chars"],
            "hashtags": len(tags), "hashtag_limit": lim["hashtags"], "mentions": len(mentions),
            "ok": not issues, "issues": issues, "warnings": warn}


def cmd_check(a):
    if a.text is not None:
        text = a.text
    elif a.file:
        text = Path(a.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    plats = [norm_platform(p) for p in split_list(a.platforms)] or list(PLATFORM_LIMITS)
    results = [check_caption(text, p) for p in plats]
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            lim = r["limit"] or "∞"
            status = "OK " if r["ok"] else "BAD"
            extra = "; ".join(r["issues"] + r["warnings"])
            print(f"[{status}] {r['platform']:<9} {r['length']:>5}/{lim:<6} "
                  f"tags {r['hashtags']}/{r['hashtag_limit']}  {extra}")
    sys.exit(0 if all(r["ok"] for r in results) else 1)


# ---------------------------------------------------------------- calendar
def build_calendar(start, days, slots, platforms, types=None, themes=None, store=None,
                   tag_groups=None, skip_weekdays=None, seed=None):
    rows = []
    types = types or ["Post"]
    themes = themes or [""]
    i = 0
    for d in range(days):
        day = start + dt.timedelta(days=d)
        if skip_weekdays and day.isoweekday() in skip_weekdays:
            continue
        for slot in slots:
            for plat in platforms:
                tags = ""
                if store and tag_groups:
                    tags = " ".join(pick_tags(store, tag_groups, platform=plat, mode="random",
                                              seed=None if seed is None else seed + i))
                rows.append({
                    "date": day.isoformat(),
                    "weekday": f"週{WEEKDAYS_ZH[day.weekday()]}",
                    "time": slot,
                    "platform": plat,
                    "content_type": types[i % len(types)],
                    "theme": themes[d % len(themes)],
                    "caption": f"[{plat} caption ≤{PLATFORM_LIMITS[plat]['chars'] or '∞'} chars]",
                    "hashtags": tags or f"[≤{PLATFORM_LIMITS[plat]['hashtags']} hashtags]",
                    "asset": "",
                    "status": "draft",
                })
                i += 1
    return rows


def cmd_calendar(a):
    start = dt.date.fromisoformat(a.start) if a.start else dt.date.today()
    platforms = [norm_platform(p) for p in split_list(a.platforms)]
    store = load_store(a.store) if a.tag_groups else None
    skip = {int(x) for x in split_list(a.skip_weekdays)}
    rows = build_calendar(start, a.days, split_list(a.slots), platforms, split_list(a.types),
                          split_list(a.themes), store, split_list(a.tag_groups), skip, a.seed)
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".md":
        cols = list(rows[0].keys()) if rows else []
        lines = [f"# 內容行事曆 {start} 起 {a.days} 天", "",
                 "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for r in rows:
            lines.append("| " + " | ".join(str(r[c]).replace("|", "\\|") for c in cols) + " |")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        with open(out, "w", newline="", encoding="utf-8-sig") as fh:
            w = SafeDictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["date"])
            w.writeheader()
            w.writerows(rows)
    print(f"Calendar: {len(rows)} slots -> {out}")


# ---------------------------------------------------------------- utm
def build_utm(url, source, medium, campaign, term=None, content=None, extra=None):
    parts = urlsplit(url if "://" in url else "https://" + url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if not k.startswith("utm_")]
    for k, v in [("utm_source", source), ("utm_medium", medium), ("utm_campaign", campaign),
                 ("utm_term", term), ("utm_content", content)]:
        if v:
            q.append((k, v))
    for kv in extra or []:
        k, v = kv.split("=", 1)
        q.append((k, v))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def cmd_utm(a):
    if a.csv:
        # Batch: CSV with columns url,source,medium,campaign[,term,content]
        with open(a.csv, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            r["utm_url"] = build_utm(r["url"], r.get("source") or a.source,
                                     r.get("medium") or a.medium,
                                     r.get("campaign") or a.campaign,
                                     r.get("term") or a.term, r.get("content") or a.content)
        out = a.output or a.csv.replace(".csv", "_utm.csv")
        with open(out, "w", newline="", encoding="utf-8-sig") as fh:
            w = SafeDictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} links -> {out}")
        return
    if not (a.url and a.source and a.campaign):
        sys.exit("url, --source and --campaign are required (or use --csv)")
    print(build_utm(a.url, a.source, a.medium, a.campaign, a.term, a.content, a.param))


# ---------------------------------------------------------------- qr
def cmd_qr(a):
    import qrcode
    ec = {"L": qrcode.constants.ERROR_CORRECT_L, "M": qrcode.constants.ERROR_CORRECT_M,
          "Q": qrcode.constants.ERROR_CORRECT_Q, "H": qrcode.constants.ERROR_CORRECT_H}[a.error]
    qr = qrcode.QRCode(error_correction=ec, box_size=a.box_size, border=a.border)
    qr.add_data(a.data)
    qr.make(fit=True)
    out = Path(a.output)
    if out.suffix.lower() == ".svg":
        import qrcode.image.svg
        img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        img.save(str(out))
    else:
        img = qr.make_image(fill_color=a.fg, back_color=a.bg)
        img.save(str(out))
    print(f"QR (version {qr.version}) -> {out}")


def build_parser():
    p = argparse.ArgumentParser(description="Social media content toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("calendar", help="Content calendar CSV/Markdown")
    s.add_argument("-o", "--output", required=True, help=".csv or .md")
    s.add_argument("--start", help="YYYY-MM-DD (default today)")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--slots", default="12:00,20:00", help="Comma times")
    s.add_argument("--platforms", default="IG,X,Threads")
    s.add_argument("--types", default="Reel,Carousel,Single,Story",
                   help="Content types rotated across slots")
    s.add_argument("--themes", default="", help="Daily themes rotated by day")
    s.add_argument("--tag-groups", default="", help="Fill hashtags from these saved groups")
    s.add_argument("--skip-weekdays", default="", help="ISO weekdays to skip, e.g. 7 for Sunday")
    s.add_argument("--store", default=str(DEFAULT_STORE))
    s.add_argument("--seed", type=int)
    s.set_defaults(func=cmd_calendar)

    s = sub.add_parser("tags", help="Hashtag groups")
    s.add_argument("action", choices=["add", "remove", "list", "pick"])
    s.add_argument("group", nargs="?", default="", help="Group name (pick: comma list)")
    s.add_argument("tags", nargs="*", help="Tags for add/remove")
    s.add_argument("--store", default=str(DEFAULT_STORE))
    s.add_argument("--platform", help="Enforce this platform's max hashtags")
    s.add_argument("-n", "--count", type=int)
    s.add_argument("--mode", choices=["random", "rotate", "ordered"], default="random")
    s.add_argument("--always", default="", help="Tags always included first (comma)")
    s.add_argument("--seed", type=int)
    s.set_defaults(func=cmd_tags)

    s = sub.add_parser("check", help="Check caption against platform limits (exit 1 if over)")
    s.add_argument("-t", "--text")
    s.add_argument("-f", "--file")
    s.add_argument("-p", "--platforms", default="", help="Comma list (default all)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("utm", help="Build UTM link")
    s.add_argument("url", nargs="?")
    s.add_argument("--source")
    s.add_argument("--medium", default="social")
    s.add_argument("--campaign")
    s.add_argument("--term")
    s.add_argument("--content")
    s.add_argument("--param", action="append", help="Extra key=value")
    s.add_argument("--csv", help="Batch CSV (url,source,medium,campaign,term,content)")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_utm)

    s = sub.add_parser("qr", help="QR code PNG/SVG")
    s.add_argument("data")
    s.add_argument("-o", "--output", required=True, help=".png or .svg")
    s.add_argument("--box-size", type=int, default=10)
    s.add_argument("--border", type=int, default=4)
    s.add_argument("--error", choices=list("LMQH"), default="M")
    s.add_argument("--fg", default="black")
    s.add_argument("--bg", default="white")
    s.set_defaults(func=cmd_qr)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ValueError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
