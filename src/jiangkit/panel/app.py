"""JiangKit local web panel (Gradio) — one tab per category.

Security defaults
* binds to 127.0.0.1 (a non-loopback --host prints a warning and needs --i-know-lan)
* no public share links (Gradio `share` is never enabled)
* upload size cap (JIANGKIT_PANEL_MAX_UPLOAD_MB, default 200 MB) + allowed extensions per input
* every job runs in its own temp folder under a private work dir (0700); folders older than
  JIANGKIT_PANEL_TTL_HOURS (default 6 h) are purged, the whole work dir is removed on exit,
  and Gradio's own upload cache is cleaned hourly (delete_cache)
* only the work dir is exposed to the browser (allowed_paths); tools that touch arbitrary
  folders (files/system/X scraper) stay CLI-only
* generated HTML reports are offered as downloads, never rendered inline
* Pro tabs check the Ed25519 license on every call
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
import io
import ipaddress
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .. import __version__
from .._brand import BRAND, BRAND_ZH, CLI_NAME
from ..registry import CATEGORIES, tools_in

MAX_UPLOAD_MB = int(os.environ.get("JIANGKIT_PANEL_MAX_UPLOAD_MB", "200"))
TTL_HOURS = float(os.environ.get("JIANGKIT_PANEL_TTL_HOURS", "6"))
_WORK: Path | None = None
_CLIP: dict = {}

IMG = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"]
VID = [".mp4", ".mov", ".m4v", ".webm", ".mkv"]
AUD = [".mp3", ".wav", ".m4a", ".flac", ".ogg"]
TAB = [".csv", ".tsv", ".xlsx", ".json", ".jsonl"]
TXT = [".txt", ".md", ".srt", ".vtt"]


# ----------------------------------------------------------------------------- work dir
def work_dir() -> Path:
    global _WORK
    if _WORK is None:
        base = os.environ.get("JIANGKIT_PANEL_WORKDIR")
        if base:
            _WORK = Path(base)
            _WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
        else:
            _WORK = Path(tempfile.mkdtemp(prefix="jiangkit_panel_"))
            atexit.register(shutil.rmtree, _WORK, True)
        os.chmod(_WORK, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - 0o700/0o600 private permissions (this is the hardening, not a weakness)
    return _WORK


def purge_old_runs(max_age_hours: float = TTL_HOURS) -> int:
    cutoff = time.time() - max_age_hours * 3600
    n = 0
    for d in work_dir().iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        except OSError:
            pass
    return n


def run_dir(tool: str) -> Path:
    purge_old_runs()
    d = Path(tempfile.mkdtemp(prefix=f"{time.strftime('%Y%m%d-%H%M%S')}_{tool}_", dir=work_dir()))
    return d


def _paths(files) -> list[str]:
    if not files:
        return []
    files = files if isinstance(files, list) else [files]
    return [f if isinstance(f, str) else getattr(f, "name", str(f)) for f in files]


def _check_upload(paths: list[str], exts: list[str]) -> list[str]:
    import gradio as gr

    out = []
    for p in paths:
        pp = Path(p)
        if pp.suffix.lower() not in exts:
            raise gr.Error(f"不支援的檔案類型 / unsupported file type: {pp.suffix or '?'}")
        if pp.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
            raise gr.Error(f"檔案太大 / file too large (> {MAX_UPLOAD_MB} MB)")
        out.append(p)
    return out


def _stage(files, dest: Path, exts: list[str]) -> Path:
    from ..security.paths import safe_filename, safe_join

    dest.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(_check_upload(_paths(files), exts)):
        shutil.copy2(p, safe_join(dest, f"{i:03d}_{safe_filename(Path(p).name)}"))
    return dest


def _one(files, exts: list[str], what: str) -> str:
    import gradio as gr

    ps = _paths(files)
    if not ps:
        raise gr.Error(f"請上傳{what} / please upload {what}")
    return _check_upload(ps[:1], exts)[0]


def _cli(module, argv: list[str]) -> tuple[int, str]:
    """Run a tool's main(argv) capturing stdout+stderr (argument lists only, never a shell)."""
    buf = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            rc = module.main(argv)
            code = int(rc) if isinstance(rc, (int, bool)) else 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            if not isinstance(e.code, int) and e.code is not None:
                print(e.code)
    from ..security.redact import redact

    return code, redact(buf.getvalue())


def _logged(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fn(*a, **kw)
    return res, buf.getvalue()


def _require_pro(tool_id: str) -> None:
    import gradio as gr

    from .. import license as lic

    from ..registry import TOOLS, module_available

    tool = next((x for x in TOOLS if x.id == tool_id), None)
    if tool is not None and not module_available(tool):
        raise gr.Error(f"「{tool_id}」不包含在免費版中。 / '{tool_id}' is not included in the free build.")
    if not lic.status().is_pro:
        raise gr.Error(f"「{tool_id}」是 Pro 功能，請到「授權 License」分頁啟用。 / '{tool_id}' is a Pro feature — activate a license in the License tab.")


def _fail_if(code: int, log: str) -> None:
    import gradio as gr

    if code not in (0, None):
        raise gr.Error(f"執行失敗 / failed (exit {code}): {log[-400:]}")


# ----------------------------------------------------------------------------- FREE handlers
def ui_img_fit(files, preset, mode):
    from ..media import img_tools

    d = run_dir("img")
    src = _stage(files, d / "in", IMG)
    code, log = _cli(img_tools, ["fit", str(src), "--preset", preset, "--mode", mode, "-o", str(d / "out")])
    _fail_if(code, log)
    outs = sorted(str(p) for p in (d / "out").glob("*"))
    return outs, outs, log


def ui_img_strip(files):
    from ..media import img_tools

    d = run_dir("exif")
    src = _stage(files, d / "in", IMG)
    code, log = _cli(img_tools, ["strip-exif", str(src), "-o", str(d / "out")])
    _fail_if(code, log)
    return sorted(str(p) for p in (d / "out").glob("*")), log


def ui_video(video, action, width, fps):
    from ..media import video_tools

    src = _one(video, VID, "影片 video")
    d = run_dir("video")
    if action == "gif":
        out = d / "clip.gif"
        code, log = _cli(video_tools, ["gif", src, "--width", str(int(width)), "--fps", str(int(fps)), "-o", str(out)])
    else:
        out = d / "vertical.mp4"
        code, log = _cli(video_tools, ["vertical", src, "--size", "720x1280", "-o", str(out)])
    _fail_if(code, log)
    return str(out), log


def ui_utm(url, source, medium, campaign):
    from ..social import social_tools

    if not url:
        return "", ""
    code, log = _cli(social_tools, ["utm", url, "--source", source or "ig", "--medium", medium or "social",
                                    "--campaign", campaign or "launch"])
    return log.strip(), ""


def ui_qr(data):
    from ..social import social_tools

    if not data:
        return None
    d = run_dir("qr")
    code, log = _cli(social_tools, ["qr", data[:2000], "-o", str(d / "qr.png")])
    _fail_if(code, log)
    return str(d / "qr.png")


def ui_caption(text, platforms):
    from ..social import social_tools

    code, log = _cli(social_tools, ["check", "-t", text or "", "-p", ",".join(platforms or [])])
    return log


def ui_zh(text, config):
    from ..docs import text_tools

    code, log = _cli(text_tools, ["zh", "-t", text or "", "-c", config])
    return log.strip()


def ui_subconv(sub, target):
    from ..docs import text_tools

    src = _one(sub, [".srt", ".vtt"], "字幕 subtitles")
    d = run_dir("subconv")
    out = d / f"converted.{target}"
    code, log = _cli(text_tools, ["sub-conv", src, "-o", str(out)])
    _fail_if(code, log)
    return str(out), log


def ui_data(table, target):
    from ..data import data_tools

    src = _one(table, TAB, "資料表 table")
    d = run_dir("data")
    out = d / f"converted.{target}"
    code, log = _cli(data_tools, ["convert", src, "-o", str(out)])
    _fail_if(code, log)
    code2, stats = _cli(data_tools, ["stats", src])
    return str(out), stats


def ui_web(url, what):
    from ..web import web_tools

    if not url:
        return "", ""
    d = run_dir("web")
    out = d / "page.md"
    code, log = _cli(web_tools, ["fetch", url, "--what", *(what or ["text"]), "-o", str(out)])
    if code != 0:
        return "", log
    return out.read_text(encoding="utf-8")[:20000], log


def ui_dream(old, new):
    from ..experimental import dream_diff

    d = run_dir("dream")
    (d / "old.txt").write_text(old or "", encoding="utf-8")
    (d / "new.txt").write_text(new or "", encoding="utf-8")
    code, log = _cli(dream_diff, [str(d / "old.txt"), str(d / "new.txt"), "--html", str(d / "diff.html")])
    return log, str(d / "diff.html") if (d / "diff.html").exists() else None


def ui_regex(pos, neg):
    from ..experimental import regex_from_examples

    argv = []
    for p in (pos or "").splitlines()[:200]:
        if p.strip():
            argv += ["-p", p.strip()]
    for n in (neg or "").splitlines()[:200]:
        if n.strip():
            argv += ["-n", n.strip()]
    if not argv:
        return ""
    code, log = _cli(regex_from_examples, argv)
    return log


# ----------------------------------------------------------------------------- PRO handlers
def ui_beat(music, media, texts, size, bpc, fit, max_dur):
    _require_pro("media.beat_reels")
    from ..media import beat_reels

    m = _one(music, AUD, "音樂 music")
    d = run_dir("reel")
    mdir = _stage(media, d / "media", IMG + VID)
    meta, log = _logged(beat_reels.make_reel, m, str(mdir), str(d / "reel.mp4"), beat_reels.parse_size(size), 30,
                        int(bpc), texts=beat_reels.load_texts(texts, None), max_duration=float(max_dur) or None, fit=fit)
    return str(d / "reel.mp4"), log


def ui_best(video, top, samples, no_faces):
    _require_pro("media.best_frame")
    from ..media import best_frame

    v = _one(video, VID, "影片 video")
    d = run_dir("best")
    res, log = _logged(best_frame.pick_best, v, str(d), int(top), int(samples), no_faces=bool(no_faces),
                       weights={"face_score": 0.0} if no_faces else None)
    gal = [(str(d / p["file"]), f"#{p['rank']} {p['time']}s · {p['total']:.2f}") for p in res["picks"]]
    return gal, str(d / "contact_sheet.jpg"), log


def ui_trend(file, window, tz, min_count):
    _require_pro("social.trend_radar")
    from ..social import trend_radar

    f = _one(file, [".csv", ".jsonl"], "貼文 CSV/JSONL")
    d = run_dir("trend")
    r, log = _logged(trend_radar.analyze, f, str(d), window, None, tz, 15, int(min_count))
    md = Path(r["report"]).read_text(encoding="utf-8").replace("![heatmap](heatmap_hours.png)", "")
    return md, r["heatmap"], log


def ui_comic(script, title):
    _require_pro("comic.comic")
    from ..comic import comic_tools

    d = run_dir("comic")
    (d / "script.md").write_text(script or "", encoding="utf-8")
    code, log = _cli(comic_tools, ["board", str(d / "script.md"), "-o", str(d / "storyboard.html"), "--title", title or "Storyboard"])
    code2, log2 = _cli(comic_tools, ["parse", str(d / "script.md"), "-o", str(d / "shots.csv")])
    return str(d / "storyboard.html") if (d / "storyboard.html").exists() else None, \
        str(d / "shots.csv") if (d / "shots.csv").exists() else None, log + log2


def ui_palette(image, k, name, prefix):
    _require_pro("web.palette_brand")
    from ..web import palette_brand

    img = _one(image, IMG, "圖片 image")
    d = run_dir("palette")
    _, log = _logged(palette_brand.build_kit, img, str(d), int(k), prefix or "brand", name or "Brand")
    return (str(d / "swatches.png"), (d / "brand.css").read_text(encoding="utf-8"),
            (d / "tailwind.config.js").read_text(encoding="utf-8"), str(d / "brand.json"))


def ui_face(refs, imgs, thr, title):
    _require_pro("ai.face_consistency")
    from ..ai import face_consistency

    d = run_dir("face")
    rdir = _stage(refs, d / "refs", IMG)
    idir = _stage(imgs, d / "imgs", IMG)
    s, log = _logged(face_consistency.check_consistency, [str(rdir)], [str(idir)], str(d / "out"),
                     float(thr) if thr else None, 0.05, "max", "auto", title or "Character", None)
    rows = [[r["rank"], r["status"], round(r["score"], 3) if r["score"] is not None else None, r["faces"], Path(r["name"]).name]
            for r in s["results"]]
    return rows, str(d / "out" / "report.html"), log


def ui_tag(images, threshold, search):
    _require_pro("ai.auto_tagger")
    from ..ai import auto_tagger

    d = run_dir("tag")
    src = _stage(images, d / "images", IMG)
    if "b" not in _CLIP:
        _CLIP["b"] = auto_tagger.ClipBackend()
    res, log = _logged(auto_tagger.tag_folder, str(src), str(auto_tagger.DEFAULT_LABELS), str(d / "tags"),
                       float(threshold), 3, False, None, None, False, search or None, False, _CLIP["b"])
    rows = [[Path(r["file"]).name, "; ".join(f"{g}: {v['label']} ({v['prob']:.2f})" for g, v in r["groups"].items())]
            for r in res["rows"]]
    hits = [(h["file"], f"{h['score']:.3f}") for h in (res["search"] or [])[:12]]
    return rows, hits, log


# ----------------------------------------------------------------------------- license tab
def ui_license_status():
    from .. import license as lic

    st = lic.status()
    if st.valid:
        return f"✅ {st.tier} · buyer {st.payload.get('b', '?')} · expires {st.payload.get('x', '—')}"
    return "免費版 Free tier" if st.reason == "missing" else f"❌ invalid ({st.reason})"


def ui_license_activate(key):
    from .. import license as lic

    st, path = lic.activate(key or "")
    return ui_license_status() if st.valid else f"❌ 授權碼無效 / invalid key ({st.reason})"


# ----------------------------------------------------------------------------- UI
CSS = """
.jk-hero{padding:6px 0 2px} .jk-hero h1{margin:0;font-size:1.6rem}
.jk-pro{display:inline-block;background:#1f2937;color:#fbbf24;border-radius:6px;padding:0 6px;font-size:.8rem;margin-left:4px}
"""


def _pro_label(name: str, tier: str) -> str:
    return f"{name} · PRO" if tier == "pro" else name


def build_app():
    import gradio as gr

    theme = gr.themes.Soft(primary_hue="indigo", neutral_hue="slate")
    with gr.Blocks(title=f"{BRAND} {BRAND_ZH} Panel", delete_cache=(3600, 24 * 3600)) as app:
        gr.HTML(f"<div class='jk-hero'><h1>🧰 {BRAND} {BRAND_ZH} <small style='font-weight:400'>v{__version__}</small></h1>"
                "<p>本機執行、資料不外流。Local-only: files stay on this computer; outputs auto-delete after "
                f"{TTL_HOURS:g} h. Upload limit {MAX_UPLOAD_MB} MB.</p></div>")
        with gr.Tab("🎬 影像 Media"):
            with gr.Tab("圖片尺寸 Image fit"):
                with gr.Row():
                    with gr.Column():
                        f = gr.File(label="圖片 images", file_count="multiple", file_types=IMG, type="filepath")
                        preset = gr.Dropdown(["ig-square", "ig-portrait", "ig-story", "x-post", "yt-thumb"], value="ig-portrait", label="尺寸 preset")
                        mode = gr.Radio(["crop", "pad", "blur"], value="blur", label="方式 mode")
                        b = gr.Button("轉換 Convert", variant="primary")
                    with gr.Column():
                        g, fo, lg = gr.Gallery(label="結果 result", columns=3), gr.File(label="下載 download", file_count="multiple"), gr.Textbox(label="log", lines=4)
                b.click(ui_img_fit, [f, preset, mode], [g, fo, lg])
            with gr.Tab("移除 EXIF Strip metadata"):
                with gr.Row():
                    f2 = gr.File(label="圖片 images", file_count="multiple", file_types=IMG, type="filepath")
                    with gr.Column():
                        o2, l2 = gr.File(label="乾淨檔案 clean files", file_count="multiple"), gr.Textbox(label="log")
                gr.Button("移除 Strip", variant="primary").click(ui_img_strip, [f2], [o2, l2])
            with gr.Tab("影片 Video"):
                with gr.Row():
                    with gr.Column():
                        v = gr.File(label="影片 video", file_types=VID, type="filepath")
                        act = gr.Radio([("GIF", "gif"), ("直式 9:16 vertical", "vertical")], value="gif", label="動作 action")
                        w = gr.Slider(160, 1080, 480, step=16, label="GIF 寬 width")
                        fp = gr.Slider(5, 30, 12, step=1, label="GIF fps")
                        bv = gr.Button("處理 Run", variant="primary")
                    with gr.Column():
                        vo, vl = gr.File(label="輸出 output"), gr.Textbox(label="log", lines=4)
                bv.click(ui_video, [v, act, w, fp], [vo, vl])
            with gr.Tab(_pro_label("節拍 Reels Beat reels", "pro")):
                with gr.Row():
                    with gr.Column():
                        mu = gr.File(label="音樂 music", file_types=AUD, type="filepath")
                        me = gr.File(label="圖片/影片 media", file_count="multiple", file_types=IMG + VID, type="filepath")
                        tx = gr.Textbox(label="字幕（| 分隔） captions", placeholder="新品上市|限時優惠")
                        sz = gr.Dropdown(["1080x1920", "720x1280", "540x960"], value="720x1280", label="尺寸 size")
                        bpc = gr.Slider(1, 8, 2, step=1, label="每幾拍切換 beats per cut")
                        fit = gr.Radio(["cover", "blur"], value="blur", label="填滿 fit")
                        mx = gr.Number(15, label="最長秒數 max seconds (0=全曲)")
                        bb = gr.Button("產生 Generate", variant="primary")
                    with gr.Column():
                        ro, rl = gr.Video(label="成品 reel"), gr.Textbox(label="log")
                bb.click(ui_beat, [mu, me, tx, sz, bpc, fit, mx], [ro, rl])
            with gr.Tab(_pro_label("最佳截圖 Best frame", "pro")):
                with gr.Row():
                    with gr.Column():
                        bvid = gr.File(label="影片 video", file_types=VID, type="filepath")
                        top = gr.Slider(1, 24, 6, step=1, label="張數 top")
                        smp = gr.Slider(20, 400, 80, step=10, label="取樣 samples")
                        nof = gr.Checkbox(label="不評分人臉 no faces")
                        b3 = gr.Button("挑選 Pick", variant="primary")
                    with gr.Column():
                        gal, sheet, l3 = gr.Gallery(label="Top", columns=3), gr.Image(label="contact sheet"), gr.Textbox(label="log")
                b3.click(ui_best, [bvid, top, smp, nof], [gal, sheet, l3])
        with gr.Tab("📣 社群 Social"):
            with gr.Tab("UTM + QR"):
                with gr.Row():
                    with gr.Column():
                        u = gr.Textbox(label="網址 URL", placeholder="https://example.com/landing")
                        src = gr.Textbox("ig", label="source")
                        med = gr.Textbox("social", label="medium")
                        cam = gr.Textbox("launch", label="campaign")
                        bu = gr.Button("產生 Build", variant="primary")
                    with gr.Column():
                        uo = gr.Textbox(label="UTM 連結 link")
                        qo = gr.Image(label="QR code")
                bu.click(ui_utm, [u, src, med, cam], [uo, gr.Textbox(visible=False)]).then(ui_qr, [uo], [qo])
            with gr.Tab("貼文檢查 Caption check"):
                ct = gr.Textbox(label="貼文 caption", lines=5)
                pl = gr.CheckboxGroup(["IG", "X", "Threads", "FB", "TikTok", "YouTube"], value=["IG", "X", "Threads"], label="平台 platforms")
                co = gr.Textbox(label="結果 result", lines=6)
                gr.Button("檢查 Check", variant="primary").click(ui_caption, [ct, pl], [co])
            with gr.Tab(_pro_label("趨勢雷達 Trend radar", "pro")):
                with gr.Row():
                    with gr.Column(scale=1):
                        tf = gr.File(label="posts CSV / JSONL", file_types=[".csv", ".jsonl"], type="filepath")
                        win = gr.Dropdown(["6h", "12h", "24h", "3d", "7d"], value="24h", label="視窗 window")
                        tz = gr.Textbox("Asia/Taipei", label="時區 timezone")
                        mc = gr.Slider(1, 20, 3, step=1, label="最少次數 min count")
                        b4 = gr.Button("分析 Analyze", variant="primary")
                        heat, l4 = gr.Image(label="熱力圖 heatmap"), gr.Textbox(label="log")
                    with gr.Column(scale=2):
                        md4 = gr.Markdown()
                b4.click(ui_trend, [tf, win, tz, mc], [md4, heat, l4])
            with gr.Tab("X 爬蟲 X scraper"):
                gr.Markdown(f"""**僅限命令列、個人使用。** CLI-only, personal use.

`{CLI_NAME} social x_scraper login` → `{CLI_NAME} social x_scraper search "關鍵字" -o out`

> ⚠️ 自動化擷取可能違反 X 服務條款；請只用於你有權存取的內容、個人研究或備份，勿轉售、騷擾或大量蒐集個資。
> Automated collection may violate X's Terms of Service. Use only for personal research/backup of content you may access; never for resale, harassment or bulk personal-data harvesting. This feature is not part of any paid offering.""")
        with gr.Tab("📄 文件 Docs"):
            with gr.Tab("繁簡轉換 zh convert"):
                zt = gr.Textbox(label="文字 text", lines=6, value="软件和网络的信息")
                zc = gr.Dropdown(["s2twp", "s2t", "t2s", "tw2sp"], value="s2twp", label="OpenCC")
                zo = gr.Textbox(label="結果 result", lines=6)
                gr.Button("轉換 Convert", variant="primary").click(ui_zh, [zt, zc], [zo])
            with gr.Tab("字幕轉檔 Subtitle convert"):
                sf = gr.File(label="SRT / VTT", file_types=[".srt", ".vtt"], type="filepath")
                tg = gr.Radio(["srt", "vtt"], value="vtt", label="輸出 to")
                so, sl = gr.File(label="輸出 output"), gr.Textbox(label="log")
                gr.Button("轉換 Convert", variant="primary").click(ui_subconv, [sf, tg], [so, sl])
        with gr.Tab("📊 資料 Data"):
            df = gr.File(label="CSV / XLSX / JSON / JSONL", file_types=TAB, type="filepath")
            dt = gr.Radio(["xlsx", "csv", "json", "jsonl"], value="xlsx", label="輸出格式 to")
            do, ds = gr.File(label="輸出 output"), gr.Textbox(label="統計 stats", lines=12)
            gr.Button("轉換＋統計 Convert + stats", variant="primary").click(ui_data, [df, dt], [do, ds])
        with gr.Tab("📁 檔案 Files"):
            gr.Markdown(f"檔案整理會直接操作你的資料夾，為了安全只提供命令列。File tools act on your folders, so they are CLI-only:\n\n"
                        f"`{CLI_NAME} files files rename ./photos -t \"trip_{{n:03d}}\"`（預設 dry-run）· `{CLI_NAME} files files dupes ./photos` · `{CLI_NAME} files files backup src dst`")
        with gr.Tab("🎞️ 漫畫 Comic · PRO"):
            cs = gr.Code(label="劇本 script (Markdown)", language="markdown",
                         value="# 示範短劇\n## Scene 1: 咖啡廳 - 日\n### Shot 1 | 中景 MS | 3s\n小美坐在窗邊看手機。\nMEI: 你今天來得真早。\n### Shot 2 | 特寫 CU | 2s\nKEN (O.S.)：因為想見妳。\n> SFX: 門鈴聲\n")
            ctl = gr.Textbox("示範短劇", label="標題 title")
            with gr.Row():
                ch, cc = gr.File(label="storyboard.html"), gr.File(label="shots.csv")
            cl = gr.Textbox(label="log")
            gr.Button("產生分鏡 Build storyboard", variant="primary").click(ui_comic, [cs, ctl], [ch, cc, cl])
        with gr.Tab("🌐 網頁 Web"):
            with gr.Tab("擷取網頁 Fetch"):
                wu = gr.Textbox(label="網址 URL（僅公開網址；內網位址會被封鎖 / public URLs only, private IPs blocked）")
                ww = gr.CheckboxGroup(["text", "links", "images"], value=["text"], label="內容 what")
                wo, wl = gr.Textbox(label="Markdown", lines=14), gr.Textbox(label="log")
                gr.Button("擷取 Fetch", variant="primary").click(ui_web, [wu, ww], [wo, wl])
            with gr.Tab(_pro_label("品牌色票 Palette → brand kit", "pro")):
                with gr.Row():
                    with gr.Column():
                        pim = gr.File(label="圖片/截圖 image", file_types=IMG, type="filepath")
                        k = gr.Slider(2, 12, 6, step=1, label="顏色數 k")
                        bn, bp = gr.Textbox("Brand", label="品牌名 name"), gr.Textbox("brand", label="CSS 前綴 prefix")
                        b5 = gr.Button("產生 Build", variant="primary")
                        sw = gr.Image(label="swatches")
                    with gr.Column():
                        css = gr.Code(label="brand.css", language="css")
                        tw = gr.Code(label="tailwind.config.js", language="javascript")
                        bj = gr.File(label="brand.json")
                b5.click(ui_palette, [pim, k, bn, bp], [sw, css, tw, bj])
        with gr.Tab("🤖 AI · PRO"):
            with gr.Tab("臉部一致性 Face consistency"):
                with gr.Row():
                    with gr.Column():
                        refs = gr.File(label="參考臉 references", file_count="multiple", file_types=IMG, type="filepath")
                        imgs = gr.File(label="待檢查 candidates", file_count="multiple", file_types=IMG, type="filepath")
                        thr = gr.Number(value=None, label="門檻 threshold（空白=自動）")
                        ttl = gr.Textbox("Character", label="角色名 name")
                        b6 = gr.Button("檢查 Check", variant="primary")
                    with gr.Column():
                        fr = gr.Dataframe(headers=["rank", "status", "score", "faces", "file"], label="結果 results")
                        fh, fl = gr.File(label="report.html（下載後開啟 download）"), gr.Textbox(label="log")
                b6.click(ui_face, [refs, imgs, thr, ttl], [fr, fh, fl])
            with gr.Tab("自動標籤 Auto tagger"):
                with gr.Row():
                    with gr.Column():
                        ti = gr.File(label="圖片 images", file_count="multiple", file_types=IMG, type="filepath")
                        th = gr.Slider(0.05, 0.95, 0.35, label="門檻 threshold")
                        q = gr.Textbox(label="語意搜尋 search（選填）", placeholder="a cup of coffee")
                        b7 = gr.Button("標籤 Tag", variant="primary")
                    with gr.Column():
                        tr, tg2, tl = gr.Dataframe(headers=["file", "tags"], label="tags"), gr.Gallery(label="搜尋 search", columns=4), gr.Textbox(label="log")
                b7.click(ui_tag, [ti, th, q], [tr, tg2, tl])
        with gr.Tab("🖥️ 系統 System"):
            gr.Markdown(f"系統工具需要本機權限，只提供命令列 / CLI-only: `{CLI_NAME} system sys ports` · `{CLI_NAME} system sys notify -m 完成` · `{CLI_NAME} system sys run --name backup -- rsync ...`")
        with gr.Tab("🧪 實驗室 Lab"):
            with gr.Tab("語意差異 Dream diff"):
                with gr.Row():
                    o1 = gr.Textbox(label="舊版 old", lines=8, value="本合約有效期間為一年，乙方應於每月五日前付款。")
                    n1 = gr.Textbox(label="新版 new", lines=8, value="本合約有效期間為兩年，乙方得於每月十日前付款。")
                dl, dh = gr.Textbox(label="摘要 summary", lines=8), gr.File(label="diff.html")
                gr.Button("比較 Compare", variant="primary").click(ui_dream, [o1, n1], [dl, dh])
            with gr.Tab("例子生成正規式 Regex from examples"):
                with gr.Row():
                    rp = gr.Textbox(label="要符合 positives（每行一個）", lines=6, value="2024-01-05\n1999-12-31\n2026-09-27")
                    rn = gr.Textbox(label="不可符合 negatives", lines=6, value="2024/01/05\n24-1-5\nhello")
                ro2 = gr.Textbox(label="結果 result", lines=10)
                gr.Button("生成 Synthesize", variant="primary").click(ui_regex, [rp, rn], [ro2])
        with gr.Tab("🔑 授權 License"):
            ls = gr.Textbox(label="狀態 status", value=ui_license_status)
            lk = gr.Textbox(label="授權碼 license key", type="password")
            gr.Button("啟用 Activate", variant="primary").click(ui_license_activate, [lk], [ls])
            rows = "".join(f"| {t.id} | {'Pro' if t.tier == 'pro' else 'Free'} | {t.zh} |\n" for c in CATEGORIES for t in tools_in(c))
            gr.Markdown("授權在本機以 Ed25519 公鑰離線驗證。License keys are verified offline with an embedded Ed25519 public key.\n\n"
                        "| 工具 tool | 版本 tier | 說明 |\n|---|---|---|\n" + rows)
    app.jk_theme, app.jk_css = theme, CSS
    return app


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog=f"{CLI_NAME} panel", description="本機網頁面板 / local web panel")
    ap.add_argument("--host", default="127.0.0.1", help="預設只綁定本機 / default loopback only")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--i-know-lan", action="store_true",
                    help="允許綁定非本機位址（任何同網段的人都能使用面板）/ allow a non-loopback host")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    if not _is_loopback(a.host) and not a.i_know_lan:
        print("拒絕綁定非本機位址；如確定要讓區網存取，請加 --i-know-lan\n"
              "Refusing to bind a non-loopback address; add --i-know-lan if you really want LAN access.", file=sys.stderr)
        return 2
    wd = work_dir()
    app = build_app()
    app.queue(default_concurrency_limit=2)
    app.launch(server_name=a.host, server_port=a.port, share=False, inbrowser=not a.no_browser,
               allowed_paths=[str(wd)], max_file_size=f"{MAX_UPLOAD_MB}mb", show_error=True,
               theme=app.jk_theme, css=app.jk_css, footer_links=[], ssr_mode=False, mcp_server=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
