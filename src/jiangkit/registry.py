"""Tool registry: category → tool → module, tier, extra, bilingual description.

Tier policy (see README “免費 vs Pro”):
* free  — open source (MIT), portfolio / traffic tools
* pro   — commercial, needs an Ed25519-signed license (jiang license activate)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    category: str
    name: str
    module: str
    tier: str            # "free" | "pro"
    extra: str           # pip extra that provides its dependencies ("" = base install)
    zh: str
    en: str
    note: str = ""

    @property
    def id(self) -> str:
        return f"{self.category}.{self.name}"


CATEGORIES: dict[str, tuple[str, str]] = {
    "media": ("影像與影片", "Images & video"),
    "social": ("社群", "Social"),
    "docs": ("文件與文字", "Documents & text"),
    "data": ("資料表", "Tabular data"),
    "files": ("檔案管理", "File management"),
    "comic": ("漫畫與分鏡", "Comics & storyboards"),
    "web": ("網頁與品牌", "Web & brand"),
    "ai": ("AI 視覺", "AI vision"),
    "system": ("系統", "System"),
    "experimental": ("實驗室", "Experimental lab"),
}

TOOLS: list[Tool] = [
    Tool("media", "img", "jiangkit.media.img_tools", "free", "",
         "批次圖片：IG/Reels/X 尺寸、浮水印、去 EXIF、轉檔、找重複", "Batch images: social sizes, watermark, EXIF strip, convert, find dupes"),
    Tool("media", "video", "jiangkit.media.video_tools", "free", "video",
         "ffmpeg 影片：剪輯、合併、9:16、抽幀、GIF、字幕、自動字幕", "ffmpeg video: trim, concat, 9:16, frames, GIF, subtitles, auto-subs"),
    Tool("media", "beat_reels", "jiangkit.media.beat_reels", "pro", "media",
         "依音樂節拍自動剪直式 Reels", "Beat-synced vertical Reels maker"),
    Tool("media", "best_frame", "jiangkit.media.best_frame", "pro", "media",
         "從影片挑最佳縮圖（清晰度/人臉/構圖）", "Pick the best thumbnails from a video (sharpness/faces/composition)"),
    Tool("social", "x_scraper", "jiangkit.social.x_scraper.scraper", "free", "scraper",
         "X 爬蟲（僅限個人使用，請遵守服務條款）", "X scraper (personal use only; respect the ToS)", note="tos_x"),
    Tool("social", "trend_radar", "jiangkit.social.trend_radar", "pro", "trends",
         "上升中的主題標籤／關鍵字與最佳發文時段", "Rising hashtags/keywords and best posting hours"),
    Tool("social", "social", "jiangkit.social.social_tools", "free", "social",
         "內容行事曆、主題標籤組、貼文字數檢查、UTM 連結、QR code", "Content calendar, hashtag groups, caption limits, UTM links, QR codes"),
    Tool("docs", "doc", "jiangkit.docs.doc_tools", "free", "docs",
         "PDF 合併/分割/旋轉/壓縮/轉圖/OCR、Markdown/DOCX 轉 PDF（AGPL 隔離模組）", "PDF merge/split/rotate/compress/render/OCR, Markdown/DOCX→PDF (AGPL-isolated)"),
    Tool("docs", "text", "jiangkit.docs.text_tools", "free", "text",
         "中文文字與字幕：繁簡轉換、字幕平移/合併/轉檔、字數、批次取代", "Chinese text & subtitles: OpenCC, subtitle shift/merge/convert, counts, batch replace"),
    Tool("data", "data", "jiangkit.data.data_tools", "free", "data",
         "CSV/Excel/JSON 轉換、合併、去重、篩選、排序、統計、圖表", "CSV/Excel/JSON convert, merge, dedupe, filter, sort, stats, charts"),
    Tool("files", "files", "jiangkit.files.file_tools", "free", "files",
         "批次改名、依類型整理、找重複、容量報告、增量備份（可 AES 加密壓縮）", "Batch rename, organise, dedupe, disk usage, incremental (AES-zip) backups"),
    Tool("comic", "comic", "jiangkit.comic.comic_tools", "pro", "",
         "劇本轉分鏡表 CSV、可列印 HTML 分鏡、圖片拼成漫畫頁", "Script → shot list CSV, printable HTML storyboard, compose comic pages"),
    Tool("web", "web", "jiangkit.web.web_tools", "free", "web",
         "網頁擷取文字/連結/圖片、監看變化（SSRF 防護）", "Extract text/links/images, watch pages (SSRF-protected)"),
    Tool("web", "palette_brand", "jiangkit.web.palette_brand", "pro", "brand",
         "從截圖取色並產生品牌色票與 CSS/Tailwind", "Palette → brand kit (CSS/Tailwind)"),
    Tool("ai", "face_consistency", "jiangkit.ai.face_consistency", "pro", "media",
         "角色臉部一致性評分（YuNet + SFace）", "Character face-consistency scoring (YuNet + SFace)"),
    Tool("ai", "auto_tagger", "jiangkit.ai.auto_tagger", "pro", "ai",
         "本機 CLIP 自動標籤、分類、以文搜圖", "Local CLIP tagging, sorting and text→image search"),
    Tool("system", "sys", "jiangkit.system.sys_tools", "free", "",
         "桌面通知、指令工作紀錄、連接埠與行程查詢", "Desktop notify, logged job runner, ports & processes"),
    Tool("experimental", "git_symphony", "jiangkit.experimental.git_symphony", "free", "experimental",
         "把 git 歷史變成音樂", "Turn git history into music"),
    Tool("experimental", "shell_timemachine", "jiangkit.experimental.shell_timemachine.shell_timemachine", "free", "",
         "Shell 時光機：記錄指令並回答「我昨天在哪個資料夾做了什麼」", "Shell time machine: record and query your command history"),
    Tool("experimental", "image_to_level", "jiangkit.experimental.image_to_level", "free", "experimental",
         "照片變成可玩的終端機平台關卡", "Photo → playable terminal platformer level"),
    Tool("experimental", "data_sonify", "jiangkit.experimental.data_sonify", "free", "experimental",
         "用耳朵聽 CSV 資料", "Listen to a CSV"),
    Tool("experimental", "code_weather", "jiangkit.experimental.code_weather", "free", "experimental",
         "程式碼庫天氣預報（變動、複雜度）", "Weather forecast for a codebase"),
    Tool("experimental", "entropy_clock", "jiangkit.experimental.entropy_clock", "free", "",
         "系統越亂越凌亂的終端時鐘", "A clock that gets messier as your system does"),
    Tool("experimental", "regex_from_examples", "jiangkit.experimental.regex_from_examples", "free", "",
         "從正反例子自動合成正規表示式", "Synthesize a regex from examples"),
    Tool("experimental", "dream_diff", "jiangkit.experimental.dream_diff", "free", "",
         "語意層級的文字差異比對（HTML 報告）", "Semantic diff with an HTML report"),
]

BY_ID = {t.id: t for t in TOOLS}
BY_MODULE = {t.module: t for t in TOOLS}

# import name -> pip extra (for friendly "missing dependency" messages)
IMPORT_EXTRA = {
    "cv2": "media", "librosa": "media", "soundfile": "media", "torch": "ai", "open_clip": "ai",
    "sklearn": "brand", "pandas": "data", "openpyxl": "data", "matplotlib": "data", "pymupdf": "docs",
    "fitz": "docs", "pypdf": "docs", "pytesseract": "docs", "markdown": "docs", "trafilatura": "web",
    "lxml": "web", "imagehash": "web", "playwright": "scraper", "pyzipper": "files", "qrcode": "social",
    "opencc": "text", "faster_whisper": "video", "mido": "experimental", "radon": "experimental",
    "numpy": "experimental", "gradio": "panel",
}


def tools_in(category: str) -> list[Tool]:
    return [t for t in TOOLS if t.category == category]


def get(category: str, name: str) -> Tool | None:
    return BY_ID.get(f"{category}.{name}")


def module_available(tool: Tool) -> bool:
    """False when the tool's module is absent (Pro modules are stripped from the free wheel)."""
    import importlib.util

    try:
        return importlib.util.find_spec(tool.module) is not None
    except ModuleNotFoundError:
        return False
