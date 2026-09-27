"""Shared helpers for toolkit3 (fonts, image IO, face detection)."""
from __future__ import annotations

import glob
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
# Models live in a per-user cache (never inside the package); override with JIANGKIT_MODELS_DIR.
MODELS_DIR = Path(os.environ.get("JIANGKIT_MODELS_DIR") or (Path.home() / ".cache" / "jiangkit" / "models"))
YUNET_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
SFACE_PATH = MODELS_DIR / "face_recognition_sface_2021dec.onnx"
YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
SFACE_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
# Pinned SHA-256 of the exact model files we support (downloads are verified against these).
MODEL_SHA256 = {
    "face_detection_yunet_2023mar.onnx": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "face_recognition_sface_2021dec.onnx": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "C:/Windows/Fonts/msjh.ttc",  # Microsoft JhengHei (Traditional Chinese)
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def find_font(user_font: str | None = None) -> str | None:
    """Return a usable font path, preferring CJK-capable fonts."""
    if user_font and os.path.exists(user_font):
        return user_font
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    hits = glob.glob("/usr/share/fonts/**/*.tt[fc]", recursive=True)
    return hits[0] if hits else None


def load_font(size: int, user_font: str | None = None):
    from PIL import ImageFont

    path = find_font(user_font)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def list_files(folder: str | os.PathLike, exts: set[str], recursive: bool = False) -> list[Path]:
    folder = Path(folder)
    it = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in exts)


def imread(path: str | os.PathLike):
    """cv2.imread that also works with non-ASCII (e.g. Chinese) paths. Returns BGR or None."""
    import cv2

    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img


def imwrite(path: str | os.PathLike, img, quality: int = 92) -> None:
    import cv2

    ext = Path(path).suffix or ".jpg"
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if ext.lower() in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, img, params)
    if not ok:
        raise IOError(f"cannot encode {path}")
    buf.tofile(str(path))


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(path: Path, url: str) -> bool:
    """Download an ONNX model if missing (HTTPS only, size-capped, SHA-256 verified).

    Network call (documented in docs/NETWORK.md): github.com/opencv/opencv_zoo, only when the
    model file is absent. Set JIANGKIT_OFFLINE=1 to forbid it.
    """
    path = Path(path)
    expected = MODEL_SHA256.get(path.name)
    if path.exists() and path.stat().st_size > 1000:
        if expected is None or _sha256(path) == expected:
            return True
        print(f"[warn] model {path} failed SHA-256 check; ignoring it")
        return False
    if os.environ.get("JIANGKIT_OFFLINE") == "1":
        return False
    if not url.startswith("https://"):
        return False
    try:
        from jiangkit.security.net import download_to_file

        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[info] downloading {url} ...")
        tmp = path.with_suffix(path.suffix + ".part")
        download_to_file(url, tmp, max_bytes=200 * 1024 * 1024, allow_private=False)
        if expected is not None and _sha256(tmp) != expected:
            tmp.unlink(missing_ok=True)
            print(f"[warn] downloaded model hash mismatch for {path.name}; discarded")
            return False
        tmp.replace(path)
        return True
    except Exception as e:  # network may be unavailable
        print(f"[warn] could not download model {url}: {e}")
        return False


class FaceDetector:
    """YuNet face detector (OpenCV DNN) with Haar cascade fallback.

    detect() returns an (N, 15) float array in YuNet format:
    x, y, w, h, 5 landmarks (x, y) pairs, score. Haar results have zeros for landmarks.
    """

    def __init__(self, score_threshold: float = 0.7, prefer: str = "yunet"):
        import cv2

        self.cv2 = cv2
        self.kind = None
        self.score_threshold = score_threshold
        if prefer == "yunet" and ensure_model(YUNET_PATH, YUNET_URL):
            try:
                self.det = cv2.FaceDetectorYN.create(str(YUNET_PATH), "", (320, 320), score_threshold, 0.3, 5000)
                self.kind = "yunet"
            except Exception as e:
                print(f"[warn] YuNet unavailable ({e}); falling back to Haar cascade")
        if self.kind is None:
            self.det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            self.kind = "haar"

    def detect(self, img_bgr) -> np.ndarray:
        cv2 = self.cv2
        h, w = img_bgr.shape[:2]
        if self.kind == "yunet":
            # Downscale very large images for speed, then rescale boxes back.
            scale = min(1.0, 1280.0 / max(h, w))
            small = cv2.resize(img_bgr, (int(w * scale), int(h * scale))) if scale < 1 else img_bgr
            self.det.setInputSize((small.shape[1], small.shape[0]))
            _, faces = self.det.detect(small)
            if faces is None:
                return np.zeros((0, 15), np.float32)
            faces = faces.copy()
            faces[:, :14] /= scale
            return faces
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        rects = self.det.detectMultiScale(gray, 1.1, 5, minSize=(max(24, w // 30), max(24, w // 30)))
        out = np.zeros((len(rects), 15), np.float32)
        for i, (x, y, fw, fh) in enumerate(rects):
            out[i, :4] = (x, y, fw, fh)
            out[i, 14] = 1.0
        return out


def cover_resize(img, width: int, height: int):
    """Resize + center-crop an image to exactly width x height (like CSS object-fit: cover)."""
    import cv2

    h, w = img.shape[:2]
    s = max(width / w, height / h)
    nw, nh = max(width, int(round(w * s))), max(height, int(round(h * s)))
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    x0, y0 = (nw - width) // 2, (nh - height) // 2
    return r[y0 : y0 + height, x0 : x0 + width]


def contact_sheet(images: list, labels: list[str] | None = None, cols: int = 4, thumb_w: int = 360,
                  bg=(24, 24, 24), font_path: str | None = None):
    """Build a contact sheet (PIL.Image) from a list of BGR numpy images."""
    import cv2
    from PIL import Image, ImageDraw

    if not images:
        return Image.new("RGB", (thumb_w, thumb_w), bg)
    thumbs = []
    for im in images:
        h, w = im.shape[:2]
        th = int(h * thumb_w / w)
        thumbs.append(cv2.cvtColor(cv2.resize(im, (thumb_w, th)), cv2.COLOR_BGR2RGB))
    cell_h = max(t.shape[0] for t in thumbs) + 34
    cols = max(1, min(cols, len(thumbs)))
    rows = (len(thumbs) + cols - 1) // cols
    pad = 8
    sheet = Image.new("RGB", (cols * (thumb_w + pad) + pad, rows * (cell_h + pad) + pad), bg)
    draw = ImageDraw.Draw(sheet)
    font = load_font(18, font_path)
    for i, t in enumerate(thumbs):
        r, c = divmod(i, cols)
        x, y = pad + c * (thumb_w + pad), pad + r * (cell_h + pad)
        sheet.paste(Image.fromarray(t), (x, y))
        if labels:
            draw.text((x + 4, y + t.shape[0] + 6), labels[i], fill=(235, 235, 235), font=font)
    return sheet
