"""Tests for web_tools.py against a local HTTP server (no internet needed)."""
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from jiangkit.web import web_tools as W


def png(color, size=(200, 200)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


STATE = {"price": "NT$ 1,200"}
ARTICLE = """<html><head><meta charset="utf-8"><title>AI 網紅日報</title>
<meta property="og:image" content="/img/og.png"></head><body>
<nav><a href="/">首頁</a> <a href="/about">關於</a></nav>
<article><h1>今日重點</h1>
<p>這是一篇關於 AI 生成圖片與短影音趨勢的測試文章，內容足夠長以便主文擷取演算法判斷它是正文。
我們討論 Reels 的 9:16 格式、IG 直式 1080x1350 以及 X 的 1600x900 橫圖。</p>
<p>第二段：漫畫與短劇的製作流程，包含分鏡、上色、配音與字幕，這些都可以用自動化工具加速。</p>
<img src="/img/a.png" alt="A圖"><img srcset="/img/small.png 300w, /img/b.png 1200w" src="/img/small.png">
<img src="/img/icon.png"><img src="/img/a.png"><img src="data:image/gif;base64,R0lG">
<div style="background-image:url('/img/bg.png')"></div>
<a href="https://other.example.com/x">外部連結</a> <a href="/post/2#top">下一篇</a>
<a href="javascript:void(0)">js</a></article>
<footer>© 2026 footer text</footer>
<div class="price">{price}</div><div class="clock">更新時間 {clock}</div>
</body></html>"""
CLOCK = {"n": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/img/"):
            body = {"/img/icon.png": png("red", (8, 8))}.get(self.path) or png(
                {"a": "blue", "b": "green", "og": "yellow", "bg": "purple", "small": "gray"}[self.path[5:-4]])
            ctype = "image/png"
        else:
            CLOCK["n"] += 1
            body = ARTICLE.replace("{price}", STATE["price"]).replace("{clock}", str(CLOCK["n"])).encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_fetch_json(site, tmp_path):
    out = tmp_path / "p.json"
    assert W.main(["fetch", site + "/article", "--format", "json", "-o", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["title"] == "AI 網紅日報"
    assert "9:16" in d["text"] and "短劇" in d["text"]
    assert "footer text" not in d["text"]
    urls = [l["url"] for l in d["links"]]
    assert site + "/post/2" in urls and "https://other.example.com/x" in urls
    assert not any(u.startswith("javascript") for u in urls)
    imgs = [i["url"] for i in d["images"]]
    assert imgs[0] == site + "/img/og.png"
    assert site + "/img/b.png" in imgs and site + "/img/bg.png" in imgs
    assert imgs.count(site + "/img/a.png") == 1 and not any(u.startswith("data:") for u in imgs)


def test_fetch_md_same_domain(site, capsys):
    assert W.main(["fetch", site + "/article", "--what", "links", "--same-domain"]) == 0
    out = capsys.readouterr().out
    assert "/post/2" in out and "other.example.com" not in out


def test_images_download(site, tmp_path):
    d = tmp_path / "imgs"
    assert W.main(["images", site + "/article", "-d", str(d), "--min-bytes", "300", "--delay", "0"]) == 0
    files = sorted(p.name.split("_", 1)[1] for p in d.iterdir())
    assert files == ["a.png", "b.png", "bg.png", "og.png", "small.png"]  # icon too small, a.png once


def test_watch_cycle(site, tmp_path):
    st = tmp_path / "state.json"
    base = ["watch", site + "/article", "--state", str(st)]
    # whole page, clock changes every request → ignore it via regex
    ign = ["--ignore", r"更新時間 \d+"]
    assert W.main(base + ign) == 0          # baseline
    assert W.main(base + ign) == 0          # unchanged
    assert W.main(base) == 1                # without --ignore the changing clock IS a change
    # selector-only watch
    sel = base + ["-s", ".price"]
    assert W.main(sel) == 0
    assert W.main(sel) == 0
    STATE["price"] = "NT$ 990"
    assert W.main(sel) == 1                 # changed → exit 1
    assert W.main(sel) == 0                 # stable again
    data = json.loads(st.read_text(encoding="utf-8"))
    assert data[site + "/article :: .price"]["content"] == "NT$ 990"
    assert W.main(base + ["-s", ".nope"]) == 2  # selector not found → error
    STATE["price"] = "NT$ 1,200"


def test_extract_helpers():
    assert W._srcset_best("a.jpg 1x, b.jpg 2x") == "b.jpg"
    assert W._srcset_best("a.jpg 100w, b.jpg 50w") == "a.jpg"
    t = W.extract_text("<html><body><script>x=1</script><p>哈囉</p></body></html>", main_only=False)
    assert t == "哈囉"
