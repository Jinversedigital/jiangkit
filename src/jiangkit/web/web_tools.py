#!/usr/bin/env python3
"""Web utilities: clean text/link/image extraction, bulk image download,
and a cron-friendly page-change watcher.

Security: every request goes through jiangkit.security.net (SSRF protection: only public IPs,
re-validated on every redirect hop and on the connected socket; timeouts; size caps).
Use --allow-private (or JIANGKIT_ALLOW_PRIVATE_NET=1) only for pages on your own LAN.

Exit codes for `watch`: 0 = unchanged (or first baseline), 1 = changed, 2 = error.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from jiangkit.security.net import make_session, safe_get

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".svg", ".bmp")
MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
ALLOW_PRIVATE: bool | None = None  # set by --allow-private; None = env JIANGKIT_ALLOW_PRIVATE_NET


def get(url: str, timeout: int = 30) -> requests.Response:
    r = safe_get(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
                 timeout=(5, timeout), max_bytes=MAX_PAGE_BYTES, allow_private=ALLOW_PRIVATE)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding  # fix mis-declared Chinese pages
    return r


# --------------------------------------------------------------- extraction
def extract_text(html: str, url: str | None = None, main_only: bool = True) -> str:
    """Main-article text via trafilatura, falling back to BeautifulSoup."""
    if main_only:
        try:
            import trafilatura
            t = trafilatura.extract(html, url=url, include_comments=False, include_tables=True,
                                    favor_recall=True)
            if t and t.strip():
                return t.strip()
        except Exception:  # nosec B110 - best-effort optional step; failure is intentionally non-fatal
            pass
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe"]):
        tag.decompose()
    if main_only:
        for tag in soup(["nav", "footer", "header", "aside", "form"]):
            tag.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def extract_links(html: str, base: str, same_domain: bool = False) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(base).netloc
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        u = urldefrag(urljoin(base, href))[0]
        if same_domain and urlparse(u).netloc != host:
            continue
        if u not in seen:
            seen.add(u)
            out.append({"url": u, "text": a.get_text(" ", strip=True)})
    return out


def _srcset_best(srcset: str) -> str | None:
    best, best_w = None, -1.0
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 1.0
        if len(bits) > 1:
            m = re.match(r"([\d.]+)([wx])", bits[1])
            if m:
                w = float(m.group(1))
        if w > best_w:
            best, best_w = bits[0], w
    return best


def extract_images(html: str, base: str) -> list[dict]:
    """<img> (src, data-src, srcset largest), <source srcset>, og:image, CSS inline bg."""
    soup = BeautifulSoup(html, "lxml")
    found: list[dict] = []
    seen = set()

    def add(u, alt=""):
        if not u or u.startswith("data:"):
            return
        u = urljoin(base, u.strip())
        if u not in seen:
            seen.add(u)
            found.append({"url": u, "alt": alt})
    for m in soup.find_all("meta", attrs={"property": ["og:image", "twitter:image"]}):
        add(m.get("content"), "og:image")
    for m in soup.find_all("meta", attrs={"name": ["twitter:image"]}):
        add(m.get("content"), "twitter:image")
    for img in soup.find_all("img"):
        alt = img.get("alt", "")
        ss = img.get("srcset") or img.get("data-srcset")
        add(_srcset_best(ss) if ss else None, alt)
        add(img.get("data-src") or img.get("data-original") or img.get("src"), alt)
    for s in soup.find_all("source"):
        if s.get("srcset"):
            add(_srcset_best(s["srcset"]))
    for el in soup.find_all(style=True):
        for u in re.findall(r"url\(['\"]?([^'\")]+)['\"]?\)", el["style"]):
            add(u)
    return found


def cmd_fetch(a) -> int:
    r = get(a.url)
    html, url = r.text, r.url
    what = a.what or ["text", "links", "images"]
    res: dict = {"url": url, "title": None}
    t = BeautifulSoup(html, "lxml").title
    res["title"] = t.get_text(strip=True) if t else None
    if "text" in what:
        res["text"] = extract_text(html, url, main_only=not a.full_text)
    if "links" in what:
        res["links"] = extract_links(html, url, a.same_domain)
    if "images" in what:
        res["images"] = extract_images(html, url)
    if a.format == "json":
        out = json.dumps(res, ensure_ascii=False, indent=2)
    else:  # markdown
        parts = [f"# {res['title'] or url}", f"來源: {url}"]
        if "text" in res:
            parts += ["## 內文", res["text"]]
        if "links" in res:
            parts += [f"## 連結 ({len(res['links'])})"] + [f"- [{l['text'] or l['url']}]({l['url']})" for l in res["links"]]
        if "images" in res:
            parts += [f"## 圖片 ({len(res['images'])})"] + [f"- ![{i['alt']}]({i['url']})" for i in res["images"]]
        out = "\n\n".join(parts) + "\n"
    if a.output:
        Path(a.output).write_text(out, encoding="utf-8")
        print(f"已寫入 {a.output}", file=sys.stderr)
    else:
        print(out)
    return 0


def _img_name(url: str, content: bytes, ctype: str) -> str:
    name = Path(urlparse(url).path).name or "image"
    name = re.sub(r"[^\w.\-]+", "_", name)[:80]
    if not name.lower().endswith(IMG_EXT):
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
               "image/avif": ".avif", "image/svg+xml": ".svg"}.get(ctype.split(";")[0].strip(), ".img")
        name += ext
    return f"{hashlib.sha1(content, usedforsecurity=False).hexdigest()[:8]}_{name}"  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1 - SHA-1 used only for de-duplication/file naming (usedforsecurity=False)


def cmd_images(a) -> int:
    r = get(a.url)
    imgs = extract_images(r.text, r.url)
    if a.pattern:
        imgs = [i for i in imgs if re.search(a.pattern, i["url"])]
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = make_session(ALLOW_PRIVATE, user_agent=UA)
    s.headers.update({"Referer": r.url})
    ok = small = fail = 0
    hashes = set()
    for i in imgs[: a.limit or None]:
        try:
            resp = safe_get(i["url"], session=s, timeout=(5, 30), max_bytes=MAX_IMAGE_BYTES)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if not ctype.startswith("image/") and not i["url"].lower().split("?")[0].endswith(IMG_EXT):
                continue
            data = resp.content
            if len(data) < a.min_bytes:
                small += 1
                continue
            h = hashlib.sha1(data, usedforsecurity=False).hexdigest()  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1 - SHA-1 used only for de-duplication/file naming (usedforsecurity=False)
            if h in hashes:
                continue
            hashes.add(h)
            (out / _img_name(i["url"], data, ctype)).write_bytes(data)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"失敗 {i['url']}: {e}", file=sys.stderr)
        time.sleep(a.delay)
    print(f"找到 {len(imgs)} 張，下載 {ok}，太小略過 {small}，失敗 {fail} → {out}")
    return 0


# -------------------------------------------------------------------- watch
def snapshot(url: str, selector: str | None, ignore: list[str], raw: bool) -> str:
    html = get(url).text
    soup = BeautifulSoup(html, "lxml")
    if selector:
        nodes = soup.select(selector)
        if not nodes:
            raise ValueError(f"CSS selector 沒有找到任何元素: {selector}")
        parts = [str(n) if raw else n.get_text("\n", strip=True) for n in nodes]
        text = "\n".join(parts)
    else:
        text = html if raw else extract_text(html, url, main_only=False)
    for pat in ignore:
        text = re.sub(pat, "", text)
    return "\n".join(ln.rstrip() for ln in text.splitlines() if ln.strip())


def cmd_watch(a) -> int:
    state_path = Path(a.state)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    key = a.url + (f" :: {a.selector}" if a.selector else "")
    try:
        snap = snapshot(a.url, a.selector, a.ignore or [], a.raw)
    except Exception as e:
        print(f"錯誤: {e}", file=sys.stderr)
        return 2
    h = hashlib.sha256(snap.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    prev = state.get(key)
    changed = bool(prev) and prev["hash"] != h
    if not prev:
        print(f"[基準] 第一次記錄 {key}  hash={h[:12]}")
    elif changed:
        print(f"[已變更] {key}\n  上次: {prev['checked_at']}  現在: {now}")
        diff = difflib.unified_diff(prev["content"].splitlines(), snap.splitlines(),
                                    "before", "after", lineterm="", n=1)
        lines = list(diff)
        print("\n".join(lines[: a.max_diff_lines]))
        if len(lines) > a.max_diff_lines:
            print(f"… 另有 {len(lines) - a.max_diff_lines} 行差異")
    else:
        print(f"[未變更] {key}  hash={h[:12]}")
    state[key] = {"hash": h, "content": snap[: a.max_store], "checked_at": now,
                  "changed_at": now if (changed or not prev) else prev.get("changed_at")}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if changed else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="web_tools", description="網頁工具：抽取文字/連結/圖片、批次下載圖片、監看網頁變化")
    ap.add_argument("--allow-private", action="store_true",
                    help="允許連到內網/本機位址（預設封鎖以防 SSRF） / allow private & loopback targets")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="抓網頁並輸出乾淨文字、連結、圖片")
    p.add_argument("url")
    p.add_argument("--what", nargs="+", choices=["text", "links", "images"], help="要輸出哪些 (預設全部)")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--full-text", action="store_true", help="輸出整頁文字而非只有主文")
    p.add_argument("--same-domain", action="store_true", help="只保留同網域連結")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("images", help="下載網頁上的所有圖片")
    p.add_argument("url")
    p.add_argument("-d", "--out-dir", default="images")
    p.add_argument("--min-bytes", type=int, default=5000, help="小於此大小略過 (icon 等)")
    p.add_argument("--pattern", help="只下載網址符合此 regex 的圖片")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--delay", type=float, default=0.3, help="每張間隔秒數")
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("watch", help="監看網頁/CSS 區塊是否變更 (exit 1=有變更，適合 cron)")
    p.add_argument("url")
    p.add_argument("-s", "--selector", help="只監看此 CSS selector，如 '.price' 或 '#news li'")
    p.add_argument("--state", default=str(Path.home() / ".local" / "state" / "jiangkit" / "web_watch_state.json"),
                   help="狀態 JSON 檔")
    p.add_argument("--ignore", action="append", help="比對前先移除符合的 regex（可多次），如時間戳")
    p.add_argument("--raw", action="store_true", help="比對原始 HTML 而非文字")
    p.add_argument("--max-diff-lines", type=int, default=80)
    p.add_argument("--max-store", type=int, default=200000, help="state 內保存的內容上限字元")
    p.set_defaults(func=cmd_watch)
    return ap


def main(argv=None) -> int:
    global ALLOW_PRIVATE
    a = build_parser().parse_args(argv)
    ALLOW_PRIVATE = True if a.allow_private else None
    try:
        return a.func(a)
    except requests.RequestException as e:
        print(f"網路錯誤: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
