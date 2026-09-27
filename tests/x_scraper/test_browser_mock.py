"""End-to-end tests of the Playwright loop against a LOCAL mock of X.

No real X traffic: a tiny HTTP server serves a page that fetches
/i/api/graphql/<hash>/<Op> JSON (our fixtures) on load and on scroll, exactly
like x.com does. This validates response interception, scrolling, limits,
dedupe, DOM fallback, login-wall detection and media download.
"""
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jiangkit.social.x_scraper.scraper import main
from jiangkit.social.x_scraper.storage import load_jsonl

FIX = Path(__file__).parent / "fixtures"

PAGE = """<!doctype html><html><body style="height:20000px">
<div id="feed"></div>
<script>
const path = location.pathname;
const op = path.startsWith('/search') ? 'SearchTimeline'
         : path.startsWith('/i/status/') ? 'TweetDetail' : 'UserTweets';
let page = 0, loading = false;
async function more() {
  if (loading) return; loading = true;
  const r = await fetch('/i/api/graphql/HASH123/' + op + '?page=' + page);
  page++; loading = false;
}
more();
window.addEventListener('scroll', more);
</script></body></html>"""

DOM_PAGE = """<!doctype html><html><body>
<article data-testid="tweet">
  <div data-testid="User-Name"><span>DOM 使用者</span>\n<span>@domuser</span></div>
  <a href="/domuser/status/777"><time datetime="2026-09-10T05:00:00.000Z">Sep 10</time></a>
  <div data-testid="tweetText">DOM 備援測試 <a href="/hashtag/測試">#測試</a></div>
  <div data-testid="tweetPhoto"><img src="https://pbs.twimg.com/media/DOM1.jpg"></div>
  <button data-testid="reply" aria-label="5 則回覆。回覆"></button>
  <button data-testid="retweet" aria-label="2 reposts. Repost"></button>
  <button data-testid="like" aria-label="1.3K Likes. Like"></button>
  <a href="/domuser/status/777/analytics" aria-label="45,000 views"></a>
</article></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path
        if p.startswith("/i/api/graphql/"):
            op = p.split("/")[5].split("?")[0]
            n = int(p.split("page=")[1]) if "page=" in p else 0
            files = {"SearchTimeline": ["search_page1.json", "search_page2.json"],
                     "UserTweets": ["user_tweets.json"],
                     "TweetDetail": ["tweet_detail.json"]}[op]
            if n < len(files):
                body = (FIX / files[n]).read_bytes()
            else:  # end of timeline: only a cursor, no tweets
                body = b'{"data":{"x":{"instructions":[]}}}'
            return self._send(body, "application/json")
        if p.startswith("/media/"):
            return self._send(b"\x89PNG fake image " + p.encode(), "image/png")
        if p.startswith("/lockeduser"):
            self.send_response(302)
            self.send_header("Location", "/i/flow/login")
            self.end_headers()
            return
        if p.startswith("/i/flow/login"):
            return self._send(b"<html>login</html>", "text/html")
        if p.startswith("/domuser"):
            return self._send(DOM_PAGE.encode(), "text/html; charset=utf-8")
        return self._send(PAGE.encode(), "text/html; charset=utf-8")


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def run(server, tmp_path, *args):
    return main([*args, "--base-url", server, "--profile", str(tmp_path / "profile"),
                 "--delay", "0.2", "0.3", "--stall", "2", "--max-scrolls", "6"])


def test_search_intercepts_graphql(server, tmp_path):
    out = tmp_path / "s"
    assert run(server, tmp_path, "search", "#AIart", "-o", str(out)) == 0
    recs = load_jsonl(out.with_suffix(".jsonl"))
    assert [r["id"][-1] for r in recs] == ["1", "2", "3", "4"]  # both pages, deduped, no ad
    assert all(r["source_op"] == "SearchTimeline" for r in recs)
    assert out.with_suffix(".csv").exists()
    # resume: second run adds nothing
    assert run(server, tmp_path, "search", "#AIart", "-o", str(out)) == 0
    assert len(load_jsonl(out.with_suffix(".jsonl"))) == 4


def test_limit_and_date_filter(server, tmp_path):
    out = tmp_path / "lim"
    assert run(server, tmp_path, "search", "x", "-o", str(out), "--limit", "2") == 0
    assert len(load_jsonl(out.with_suffix(".jsonl"))) == 2
    out2 = tmp_path / "date"
    assert run(server, tmp_path, "search", "x", "-o", str(out2),
               "--since", "2026-09-21", "--until", "2026-09-23") == 0
    assert {r["id"][-1] for r in load_jsonl(out2.with_suffix(".jsonl"))} == {"2", "3"}


def test_user_timeline_filters_others(server, tmp_path):
    out = tmp_path / "u"
    assert run(server, tmp_path, "user", "@ai_idol_mei", "-o", str(out)) == 0
    recs = load_jsonl(out.with_suffix(".jsonl"))
    assert len(recs) == 3 and all(r["author_handle"] == "ai_idol_mei" for r in recs)


def test_replies(server, tmp_path):
    out = tmp_path / "r"
    assert run(server, tmp_path, "replies", "https://x.com/ai_idol_mei/status/1830000000000000020",
               "-o", str(out), "--exclude-focal") == 0
    recs = load_jsonl(out.with_suffix(".jsonl"))
    assert [r["id"][-2:] for r in recs] == ["21", "22", "23"]


def test_dom_fallback(server, tmp_path):
    out = tmp_path / "dom"
    assert run(server, tmp_path, "user", "domuser", "-o", str(out)) == 0
    recs = load_jsonl(out.with_suffix(".jsonl"))
    assert len(recs) == 1
    r = recs[0]
    assert r["id"] == "777" and r["source_op"] == "DOM"
    assert r["likes"] == 1300 and r["replies"] == 5 and r["reposts"] == 2 and r["views"] == 45000
    assert r["hashtags"] == ["測試"] and r["author_name"] == "DOM 使用者"
    assert r["created_at"] == "2026-09-10T05:00:00+00:00"


def test_login_wall_detected(server, tmp_path):
    assert run(server, tmp_path, "user", "lockeduser", "-o", str(tmp_path / "l")) == 2


def test_download_media(server, tmp_path):
    jl = tmp_path / "m.jsonl"
    jl.write_text("\n".join(json.dumps(r) for r in [
        {"id": "1", "media_urls": [f"{server}/media/a.jpg", f"{server}/media/b.png"]},
        {"id": "2", "media_urls": [f"{server}/media/v.mp4"]},
    ]), encoding="utf-8")
    d = tmp_path / "dl"
    args = ["download-media", str(jl), "-d", str(d), "--delay", "0", "0", "--any-host"]  # local mock host
    assert main(args) == 0
    assert sorted(p.name for p in d.iterdir()) == ["1_0.jpg", "1_1.png", "2_0.mp4"]
    assert main(args + ["--types", "image"]) == 0  # re-run: skipped, still OK
