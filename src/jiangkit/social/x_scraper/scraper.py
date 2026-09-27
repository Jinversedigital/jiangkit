#!/usr/bin/env python3
"""X (Twitter) scraper CLI built on Playwright.

Safety design:
  * Uses a *dedicated* persistent Chromium profile directory. You log in
    yourself via `login` (headed browser). This script never asks for,
    reads, or stores passwords and never touches other browsers' cookies.
  * Polite: random delays between scrolls, a hard max-scroll cap, stall
    detection, and backoff on HTTP 429 to reduce the risk of account locks.

Data is captured by intercepting X's own GraphQL JSON responses
(SearchTimeline / UserTweets / TweetDetail) while scrolling, with a DOM
fallback if no GraphQL responses are seen.
"""
from __future__ import annotations

import argparse
import mimetypes
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

if __package__ in (None, ""):  # allow `python x_scraper/scraper.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from x_scraper.parser import in_date_range, match_op, parse_dom_item, parse_graphql
    from x_scraper.storage import TweetStore, load_jsonl
else:
    from .parser import in_date_range, match_op, parse_dom_item, parse_graphql
    from .storage import TweetStore, load_jsonl

# Login profile (cookies!) lives in the user's data dir, never inside the package/repo,
# so it can't end up in a wheel, zip or git commit.
DEFAULT_PROFILE = Path.home() / ".local" / "share" / "jiangkit" / "x_profile"
MEDIA_HOSTS = ("pbs.twimg.com", "video.twimg.com", "abs.twimg.com", "ton.twimg.com")
MAX_MEDIA_BYTES = 1024 * 1024 * 1024  # 1 GiB per file hard cap
DEFAULT_BASE = "https://x.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")

# JS run in the page for the DOM fallback. Returns raw dicts for parse_dom_item().
DOM_EXTRACT_JS = r"""
() => Array.from(document.querySelectorAll('article[data-testid="tweet"]')).map(a => {
  const q = s => a.querySelector(s);
  const timeEl = q('time');
  const link = timeEl ? timeEl.closest('a') : q('a[href*="/status/"]');
  const num = s => {
    const el = q(s); if (!el) return null;
    const lbl = el.getAttribute('aria-label') || '';
    const m = lbl.match(/[\d][\d.,]*\s*[KkMmBb萬万億亿]?/);
    if (m) return m[0];
    const t = (el.innerText || '').trim();
    return t || null;
  };
  const un = q('[data-testid="User-Name"]');
  const tt = q('[data-testid="tweetText"]');
  return {
    url: link ? link.href : null,
    text: tt ? tt.innerText : '',
    datetime: timeEl ? timeEl.getAttribute('datetime') : null,
    name: un ? un.innerText.split('\n')[0] : null,
    replies: num('[data-testid="reply"]'),
    reposts: num('[data-testid="retweet"], [data-testid="unretweet"]'),
    likes: num('[data-testid="like"], [data-testid="unlike"]'),
    views: num('a[href$="/analytics"]'),
    media: Array.from(a.querySelectorAll('[data-testid="tweetPhoto"] img, video'))
             .map(e => e.getAttribute('src') || e.getAttribute('poster')).filter(Boolean),
    hashtags: Array.from(a.querySelectorAll('a[href*="/hashtag/"]'))
             .map(e => e.innerText.replace(/^#/, '')),
    is_retweet: !!a.querySelector('[data-testid="socialContext"]'),
  };
})
"""


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------- URLs
def build_search_url(base: str, query: str, mode: str, since: str | None, until: str | None) -> str:
    q = query
    if since:
        q += f" since:{since}"
    if until:
        q += f" until:{until}"
    url = f"{base}/search?q={quote(q)}&src=typed_query"
    if mode == "latest":
        url += "&f=live"
    elif mode == "media":
        url += "&f=media"
    return url  # "top" = no f parameter


def parse_status_id(s: str) -> str:
    m = re.search(r"status(?:es)?/(\d+)", s)
    if m:
        return m.group(1)
    if s.isdigit():
        return s
    raise SystemExit(f"無法辨識貼文 ID / cannot parse post id from: {s}")


# ------------------------------------------------------------------ browser
def launch(pw, profile: Path, headless: bool):
    """Launch Chromium with the dedicated persistent profile dir."""
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(profile), headless=headless, user_agent=UA,
        viewport={"width": 1280, "height": 1600}, locale="zh-TW",
        args=["--disable-blink-features=AutomationControlled"])


def cmd_login(args) -> int:
    """Open a headed browser on the login page; the HUMAN logs in manually."""
    from playwright.sync_api import sync_playwright
    print("將開啟瀏覽器，請你親手登入 X。本程式不會讀取或儲存密碼。\n"
          "登入完成、看到首頁後，關閉瀏覽器視窗（或回到這裡按 Enter）即可。\n"
          f"Profile 目錄: {args.profile}")
    with sync_playwright() as pw:
        ctx = launch(pw, Path(args.profile), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{args.base_url}/i/flow/login")
        try:
            input("登入完成後按 Enter 關閉… ")
        except (EOFError, KeyboardInterrupt):
            # Non-interactive: wait until the user closes the window.
            page.wait_for_event("close", timeout=0)
        try:
            ctx.close()
        except Exception:  # nosec B110 - best-effort optional step; failure is intentionally non-fatal
            pass
    print("完成。之後的爬取指令會沿用這個 profile 的登入狀態。")
    return 0


def scrape(url: str, ops: set[str], args, keep=None) -> int:
    """Core loop: open URL, scroll politely, collect records from GraphQL/DOM."""
    from playwright.sync_api import sync_playwright

    store = TweetStore(args.out, fresh=args.fresh)
    if store.resumed:
        log(f"續傳模式：已載入 {store.resumed} 筆既有資料，只會新增新貼文 (--fresh 可重來)")
    pending: list = []
    stats = {"graphql": 0, "dom": 0, "rate_limited": 0}

    def on_response(resp):
        op = match_op(resp.url)
        if op and op in ops:
            pending.append((op, resp))

    def accept(rec: dict) -> bool:
        if not in_date_range(rec, args.since, args.until):
            return False
        if keep and not keep(rec):
            return False
        return store.add(rec)

    def drain() -> int:
        new = 0
        while pending:
            op, resp = pending.pop(0)
            if resp.status == 429:
                stats["rate_limited"] += 1
                continue
            try:
                payload = resp.json()
            except Exception:  # nosec B112 - skip malformed item and continue; intentionally non-fatal
                continue
            stats["graphql"] += 1
            for rec in parse_graphql(payload, op):
                if accept(rec):
                    new += 1
                    if args.limit and store.added >= args.limit:
                        return new
        return new

    def dom_pass(page) -> int:
        new = 0
        try:
            items = page.evaluate(DOM_EXTRACT_JS)
        except Exception as e:
            log(f"DOM 解析失敗: {e}")
            return 0
        for it in items:
            rec = parse_dom_item(it)
            if rec and accept(rec):
                new += 1
                stats["dom"] += 1
                if args.limit and store.added >= args.limit:
                    break
        return new

    code = 0
    with sync_playwright() as pw:
        ctx = launch(pw, Path(args.profile), headless=not args.headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("response", on_response)
        log(f"開啟 {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(int(random.uniform(*args.delay) * 1000))  # nosec B311 - non-cryptographic randomness (timing jitter / visuals), not security relevant
        if "/i/flow/login" in page.url or "/login" in urlparse(page.url).path:
            log("尚未登入（被導向登入頁）。請先執行: run.sh x login")
            ctx.close()
            store.close()
            return 2

        use_dom = args.dom_fallback == "always"
        stall = 0
        for i in range(args.max_scrolls + 1):
            new = drain()
            if use_dom or (args.dom_fallback == "auto" and stats["graphql"] == 0 and i >= 1):
                if not use_dom and args.dom_fallback == "auto":
                    log("未攔截到 GraphQL 回應，改用 DOM 解析 (fallback)")
                    use_dom = True
                new += dom_pass(page)
            log(f"捲動 {i}/{args.max_scrolls}：本輪新增 {new}，累計 {store.added}")
            if args.limit and store.added >= args.limit:
                log("已達 --limit")
                break
            if stats["rate_limited"]:
                if stats["rate_limited"] > 2:
                    log("多次 429 (rate limit)，停止以保護帳號")
                    code = 3
                    break
                wait = random.uniform(60, 120) if not args.fast_backoff else 1  # nosec B311 - non-cryptographic randomness (timing jitter / visuals), not security relevant
                log(f"遇到 429，暫停 {wait:.0f} 秒")
                time.sleep(wait)
            stall = 0 if new else stall + 1
            if stall >= args.stall:
                log(f"連續 {stall} 次捲動沒有新資料，結束")
                break
            if i == args.max_scrolls:
                log("已達 --max-scrolls 上限")
                break
            # Polite scroll: random distance + random delay, longer pause every 10 scrolls.
            page.mouse.wheel(0, random.randint(1800, 3200))  # nosec B311 - non-cryptographic randomness (timing jitter / visuals), not security relevant
            d = random.uniform(*args.delay)  # nosec B311 - non-cryptographic randomness (timing jitter / visuals), not security relevant
            if i and i % 10 == 0:
                d += random.uniform(*args.delay) * 2  # nosec B311 - non-cryptographic randomness (timing jitter / visuals), not security relevant
            page.wait_for_timeout(int(d * 1000))
        drain()
        ctx.close()
    store.close()
    log(f"完成：新增 {store.added} 筆 → {store.jsonl} / {store.csv}")
    return code


def cmd_search(args) -> int:
    url = build_search_url(args.base_url, args.query, args.mode, args.since, args.until)
    return scrape(url, {"SearchTimeline"}, args)


def cmd_user(args) -> int:
    handle = args.handle.lstrip("@")
    url = f"{args.base_url}/{handle}" + ("/with_replies" if args.with_replies else "")
    keep = None
    if not args.include_others:
        keep = lambda r: (r.get("author_handle") or "").lower() == handle.lower()
    return scrape(url, {"UserTweets", "UserTweetsAndReplies"}, args, keep)


def cmd_replies(args) -> int:
    tid = parse_status_id(args.post)
    url = f"{args.base_url}/i/status/{tid}"
    keep = None
    if args.exclude_focal:
        keep = lambda r: r["id"] != tid
    return scrape(url, {"TweetDetail"}, args, keep)


# ------------------------------------------------------------ media download
def media_filename(tid: str, idx: int, url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if not ext:
        fm = re.search(r"format=(\w+)", url)
        ext = "." + fm.group(1) if fm else ".bin"
    return f"{tid}_{idx}{ext}"


def upgrade_media_url(url: str) -> str:
    """Request original-size images from pbs.twimg.com."""
    if "pbs.twimg.com/media/" in url and "name=" not in url:
        return url + ("&" if "?" in url else "?") + "name=orig"
    return url


def _media_url_allowed(u: str, any_host: bool) -> bool:
    p = urlparse(u)
    if p.scheme not in ("https", "http"):
        return False
    if any_host:
        return True
    return p.scheme == "https" and (p.hostname or "") in MEDIA_HOSTS


def cmd_download_media(args) -> int:
    from jiangkit.security.net import SSRFError, make_session, validate_url
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    recs = load_jsonl(args.jsonl)
    want = set(args.types.split(","))
    # SSRF-safe session: public IPs only (connected socket re-checked), no env proxies.
    sess = make_session(user_agent=UA)
    sess.max_redirects = 3
    ok = skip = fail = 0
    for r in recs:
        for i, u in enumerate(r.get("media_urls") or []):
            kind = "video" if (".mp4" in u or "video.twimg.com" in u) else "image"
            if kind not in want:
                continue
            if not _media_url_allowed(u, getattr(args, "any_host", False)):
                log(f"略過非 X 媒體網址 / skipped non-X media host: {u[:80]}")
                fail += 1
                continue
            # id comes from scraped JSON: keep only digits/letters so it can't form a path
            fn = out / media_filename(re.sub(r"[^0-9A-Za-z_-]", "_", str(r["id"]))[:40], i, u)
            if fn.exists() and fn.stat().st_size > 0:
                skip += 1
                continue
            try:
                url2 = upgrade_media_url(u)
                validate_url(url2, getattr(sess, "_jiangkit_allow_private", None))
                resp = sess.get(url2, timeout=(10, 60), stream=True, allow_redirects=False)
                if resp.is_redirect:
                    raise ValueError(f"unexpected redirect to {resp.headers.get('Location', '?')[:80]}")
                resp.raise_for_status()
                if fn.suffix == ".bin":
                    ext = mimetypes.guess_extension(resp.headers.get("content-type", "").split(";")[0]) or ".bin"
                    fn = fn.with_suffix(ext)
                tmp = fn.with_suffix(fn.suffix + ".part")
                size = 0
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(1 << 16):
                        size += len(chunk)
                        if size > MAX_MEDIA_BYTES:
                            raise ValueError("media file exceeds size cap")
                        fh.write(chunk)
                tmp.rename(fn)
                ok += 1
            except (SSRFError, Exception) as e:  # noqa: B014 - SSRFError listed for clarity
                fail += 1
                log(f"下載失敗 {u}: {e}")
            time.sleep(random.uniform(*args.delay))  # nosec B311 - non-cryptographic randomness (timing jitter / visuals), not security relevant
    log(f"下載完成：成功 {ok}、略過(已存在) {skip}、失敗 {fail} → {out}")
    return 1 if fail and not ok else 0


# --------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="x_scraper",
        description="X (Twitter) 爬蟲：先用 `login` 親手登入，再用 search / user / replies 爬取。")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, scrape_opts=True):
        p.add_argument("--profile", default=str(DEFAULT_PROFILE), help="專用 Chromium profile 目錄")
        p.add_argument("--base-url", default=DEFAULT_BASE, help=argparse.SUPPRESS)  # for tests
        if not scrape_opts:
            return
        p.add_argument("-o", "--out", required=True, help="輸出檔名前綴，會產生 .jsonl 與 .csv")
        p.add_argument("--limit", type=int, default=200, help="最多新增幾筆 (0=不限)")
        p.add_argument("--since", help="起始日 YYYY-MM-DD (含)")
        p.add_argument("--until", help="結束日 YYYY-MM-DD (不含)")
        p.add_argument("--max-scrolls", type=int, default=40, help="最多捲動次數 (防鎖帳號)")
        p.add_argument("--stall", type=int, default=5, help="連續幾次無新資料就停止")
        p.add_argument("--delay", type=float, nargs=2, default=[2.5, 6.0], metavar=("MIN", "MAX"),
                       help="每次捲動的隨機延遲秒數")
        p.add_argument("--headed", action="store_true", help="顯示瀏覽器視窗")
        p.add_argument("--fresh", action="store_true", help="不續傳，覆蓋既有輸出")
        p.add_argument("--dom-fallback", choices=["auto", "always", "never"], default="auto",
                       help="GraphQL 攔截不到時是否改用 DOM 解析")
        p.add_argument("--fast-backoff", action="store_true", help=argparse.SUPPRESS)  # tests only

    p = sub.add_parser("login", help="開啟有畫面的瀏覽器讓你親手登入 (不處理密碼)")
    common(p, scrape_opts=False)
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("search", help="關鍵字 / hashtag 搜尋")
    p.add_argument("query", help='例如 "#AI美女" 或 "AI art lang:ja"')
    p.add_argument("--mode", choices=["latest", "top", "media"], default="latest")
    common(p)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("user", help="某帳號的時間軸")
    p.add_argument("handle", help="帳號，如 @elonmusk 或 elonmusk")
    p.add_argument("--with-replies", action="store_true", help="包含該帳號的回覆")
    p.add_argument("--include-others", action="store_true", help="保留非本人貼文 (預設過濾)")
    common(p)
    p.set_defaults(func=cmd_user)

    p = sub.add_parser("replies", help="單篇貼文的回覆")
    p.add_argument("post", help="貼文網址或 ID")
    p.add_argument("--exclude-focal", action="store_true", help="不輸出原貼文本身")
    common(p)
    p.set_defaults(func=cmd_replies)

    p = sub.add_parser("download-media", help="從 JSONL 下載圖片/影片")
    p.add_argument("jsonl", help="爬取產生的 .jsonl")
    p.add_argument("-d", "--out-dir", default="media", help="存放目錄")
    p.add_argument("--types", default="image,video", help="image,video 或其一")
    p.add_argument("--delay", type=float, nargs=2, default=[0.5, 1.5], metavar=("MIN", "MAX"))
    p.add_argument("--any-host", action="store_true",
                   help="不限 *.twimg.com 網域（仍封鎖內網位址） / allow non-X media hosts (private IPs still blocked)")
    p.set_defaults(func=cmd_download_media)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "limit", None) == 0:
        args.limit = None
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
