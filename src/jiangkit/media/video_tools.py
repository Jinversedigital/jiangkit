#!/usr/bin/env python3
"""ffmpeg wrapper for short-form video work (Reels / Shorts / 短劇).

Subcommands: info, trim, concat, vertical (9:16 blur-pad or crop), frames,
gif, subs (burn or mux .srt), audio, autosub (faster-whisper, optional).
Every command prints the exact ffmpeg command it runs (use --dry-run to only print).
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess  # nosec B404 - subprocess is required to drive external tools; every call uses an argv list
import sys
import tempfile
from pathlib import Path

from jiangkit.security.proc import (concat_list_line, escape_filter_path, ff_font, ff_int, ff_lang, ff_rate,
                                    ff_size, ff_time, media_path)

DRY_RUN = False


def ff(args: list[str], quiet: bool = True) -> int:
    """Run ffmpeg with sane defaults; print the command first."""
    cmd = ["ffmpeg", "-hide_banner", "-y"] + (["-loglevel", "error"] if quiet else []) + args
    print("$ " + shlex.join(cmd), file=sys.stderr)
    if DRY_RUN:
        return 0
    r = subprocess.run(cmd)  # nosec B603 - argv list; paths normalised by media_path(), values validated
    if r.returncode != 0:
        print(f"ffmpeg 失敗 (exit {r.returncode})", file=sys.stderr)
    return r.returncode


def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",  # nosec B603 B607
                          "-show_streams", media_path(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def has_audio(path: str) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


def video_size(path: str) -> tuple[int, int]:
    for s in probe(path)["streams"]:
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise ValueError("no video stream")


def parse_size(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


def default_out(src: str, suffix: str, ext: str | None = None) -> str:
    p = Path(src)
    return str(p.with_name(f"{p.stem}{suffix}{ext or p.suffix}"))


ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]


# ----------------------------------------------------------------- commands
def cmd_info(a) -> int:
    d = probe(a.input)
    fmt = d["format"]
    print(f"檔案: {a.input}\n長度: {float(fmt.get('duration', 0)):.2f}s  大小: {int(fmt.get('size', 0)) / 1e6:.2f}MB")
    for s in d["streams"]:
        if s["codec_type"] == "video":
            print(f"影像: {s['codec_name']} {s['width']}x{s['height']} {s.get('r_frame_rate')}fps")
        elif s["codec_type"] == "audio":
            print(f"音訊: {s['codec_name']} {s.get('sample_rate')}Hz {s.get('channels')}ch")
        elif s["codec_type"] == "subtitle":
            print(f"字幕: {s['codec_name']} {s.get('tags', {}).get('language', '')}")
    return 0


def cmd_trim(a) -> int:
    out = a.output or default_out(a.input, "_trim")
    t = ["-ss", a.start] + (["-to", a.end] if a.end else []) + (["-t", a.duration] if a.duration else [])
    if a.copy:  # fast, keyframe-aligned (may be slightly off)
        return ff(t + ["-i", a.input, "-c", "copy", "-avoid_negative_ts", "make_zero", out])
    return ff(["-i", a.input] + t + ENC + [out])  # accurate re-encode


def cmd_concat(a) -> int:
    out = a.output
    if a.copy:  # concat demuxer, all inputs must share codec/params
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            for f in a.inputs:
                fh.write(concat_list_line(f))  # quotes/newlines escaped per ffmpeg concat syntax
        rc = ff(["-f", "concat", "-safe", "0", "-i", fh.name, "-c", "copy", out])
        Path(fh.name).unlink(missing_ok=True)
        return rc
    # Re-encode path: normalise every clip to same size/fps/sar; add silence if no audio.
    W, H = parse_size(a.size) if a.size else video_size(a.inputs[0])
    args, filt, n = [], [], len(a.inputs)
    for f in a.inputs:
        args += ["-i", f]
    silent_idx = {}
    for i, f in enumerate(a.inputs):
        if not has_audio(f):
            silent_idx[i] = n + len(silent_idx)
            args += ["-f", "lavfi", "-t", str(float(probe(f)["format"]["duration"])),
                     "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    for i in range(n):
        filt.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={a.fps},format=yuv420p[v{i}]")
        ai = silent_idx.get(i, i)
        filt.append(f"[{ai}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    filt.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    return ff(args + ["-filter_complex", ";".join(filt), "-map", "[v]", "-map", "[a]"] + ENC + [out])


def cmd_vertical(a) -> int:
    W, H = parse_size(a.size)
    out = a.output or default_out(a.input, "_9x16", ".mp4")
    if a.mode == "crop":
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
        return ff(["-i", a.input, "-vf", vf] + ENC + [out])
    fc = (f"[0:v]split[bgs][fgs];"
          f"[bgs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"boxblur={a.blur}:2,eq=brightness=-0.08[bg];"
          f"[fgs]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
    return ff(["-i", a.input, "-filter_complex", fc, "-map", "[v]", "-map", "0:a?"] + ENC + [out])


def cmd_frames(a) -> int:
    d = Path(a.out_dir or Path(a.input).stem + "_frames")
    d.mkdir(parents=True, exist_ok=True)
    t = (["-ss", a.start] if a.start else []) + (["-t", a.duration] if a.duration else [])
    q = ["-q:v", "2"] if a.format == "jpg" else []
    return ff(t + ["-i", a.input, "-vf", f"fps={a.fps}"] + q + [str(d / f"frame_%05d.{a.format}")])


def cmd_gif(a) -> int:
    out = a.output or default_out(a.input, "", ".gif")
    t = (["-ss", a.start] if a.start else []) + (["-t", a.duration] if a.duration else [])
    fc = (f"fps={a.fps},scale={a.width}:-1:flags=lanczos,split[s0][s1];"
          f"[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5")
    return ff(t + ["-i", a.input, "-filter_complex", fc, "-loop", "0", out])


def _sub_filter_path(p: str) -> str:
    # Escape for ffmpeg filter args; only 'boring' characters are accepted (see burn_subs).
    return escape_filter_path(p)


def burn_subs(inp: str, srt: str, out: str, font: str, size: int, margin: int) -> int:
    font = ff_font(font)
    size, margin = int(size), int(margin)
    style = f"FontName={font},FontSize={size},Outline=2,Shadow=0,MarginV={margin},Alignment=2"
    # Copy the subtitle file to a temp dir with a fixed safe name so no character from the
    # user's path can inject into the filtergraph (':', quotes, ',', ';', '[' ...).
    with tempfile.TemporaryDirectory(prefix="jk_subs_") as td:
        safe_srt = Path(td) / ("subs" + (Path(srt).suffix.lower() if Path(srt).suffix.lower() in (".srt", ".ass", ".vtt") else ".srt"))
        if not DRY_RUN:
            shutil.copyfile(srt, safe_srt)
        vf = f"subtitles='{_sub_filter_path(str(safe_srt))}':charenc=UTF-8:force_style='{style}'"
        return ff(["-i", media_path(inp), "-vf", vf] + ENC + [media_path(out)])


def cmd_subs(a) -> int:
    if a.mode == "burn":
        out = a.output or default_out(a.input, "_subbed", ".mp4")
        return burn_subs(a.input, a.srt, out, a.font, a.font_size, a.margin)
    out = a.output or default_out(a.input, "_softsub")
    codec = "mov_text" if out.lower().endswith((".mp4", ".mov", ".m4v")) else "srt"
    return ff(["-i", a.input, "-sub_charenc", "UTF-8", "-i", a.srt, "-map", "0", "-map", "1",
               "-c", "copy", "-c:s", codec, "-metadata:s:s:0", f"language={a.lang}", out])


def cmd_audio(a) -> int:
    out = a.output or default_out(a.input, "", "." + a.format)
    codec = {"mp3": ["-c:a", "libmp3lame", "-q:a", "2"], "wav": ["-c:a", "pcm_s16le"],
             "m4a": ["-c:a", "aac", "-b:a", "192k"], "flac": ["-c:a", "flac"]}[a.format]
    return ff(["-i", a.input, "-vn"] + codec + [out])


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments) -> str:
    """segments: iterable of objects/dicts with start, end, text."""
    lines = []
    for i, s in enumerate(segments, 1):
        g = (lambda k: s[k]) if isinstance(s, dict) else (lambda k: getattr(s, k))
        lines += [str(i), f"{_ts(g('start'))} --> {_ts(g('end'))}", g("text").strip(), ""]
    return "\n".join(lines)


def cmd_autosub(a) -> int:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("未安裝 faster-whisper，略過自動字幕。安裝: .venv/bin/pip install faster-whisper", file=sys.stderr)
        return 3
    srt = a.srt or default_out(a.input, "", ".srt")
    print(f"載入 Whisper 模型 {a.model} ({a.device}/{a.compute_type})… 首次會下載模型", file=sys.stderr)
    model = WhisperModel(a.model, device=a.device, compute_type=a.compute_type)
    segments, info = model.transcribe(a.input, language=a.language, vad_filter=a.vad,
                                      initial_prompt=a.prompt)
    segments = list(segments)
    Path(srt).write_text(segments_to_srt(segments), encoding="utf-8")
    print(f"語言: {info.language} ({info.language_probability:.2f})，{len(segments)} 段 → {srt}", file=sys.stderr)
    if a.burn:
        return burn_subs(a.input, srt, default_out(a.input, "_subbed", ".mp4"), a.font, a.font_size, 60)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="video_tools", description="ffmpeg 影片工具（剪輯、合併、直式 9:16、抽幀、GIF、字幕、音訊、自動字幕）")
    ap.add_argument("--dry-run", action="store_true", help="只印出 ffmpeg 指令不執行")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="顯示影片資訊")
    p.add_argument("input")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("trim", help="剪出片段")
    p.add_argument("input")
    p.add_argument("-ss", "--start", default="0", type=ff_time, help="開始時間 (秒或 HH:MM:SS.ms)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("-to", "--end", type=ff_time, help="結束時間")
    g.add_argument("-t", "--duration", type=ff_time, help="長度")
    p.add_argument("--copy", action="store_true", help="不重新編碼（快，但切點對齊關鍵影格）")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_trim)

    p = sub.add_parser("concat", help="合併多段影片")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--size", type=ff_size, help="統一輸出尺寸 WxH (預設=第一段)")
    p.add_argument("--fps", default="30", type=ff_rate)
    p.add_argument("--copy", action="store_true", help="用 concat demuxer 不重編碼 (各段格式須相同)")
    p.set_defaults(func=cmd_concat)

    p = sub.add_parser("vertical", help="轉成 9:16 直式 (Reels/Shorts)")
    p.add_argument("input")
    p.add_argument("--mode", choices=["blur", "crop"], default="blur", help="blur=模糊背景補邊, crop=置中裁切")
    p.add_argument("--size", default="1080x1920", type=ff_size)
    p.add_argument("--blur", type=ff_int(0, 200), default=20, help="背景模糊強度")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_vertical)

    p = sub.add_parser("frames", help="依 fps 抽出影格")
    p.add_argument("input")
    p.add_argument("--fps", default="1", type=ff_rate, help="每秒幾張，如 1、0.5、1/10")
    p.add_argument("--format", choices=["jpg", "png"], default="jpg")
    p.add_argument("-ss", "--start", type=ff_time)
    p.add_argument("-t", "--duration", type=ff_time)
    p.add_argument("-d", "--out-dir")
    p.set_defaults(func=cmd_frames)

    p = sub.add_parser("gif", help="轉 GIF（調色盤優化）")
    p.add_argument("input")
    p.add_argument("--fps", default="12", type=ff_rate)
    p.add_argument("--width", default="480", type=ff_int(16, 4096))
    p.add_argument("-ss", "--start", type=ff_time)
    p.add_argument("-t", "--duration", type=ff_time)
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_gif)

    p = sub.add_parser("subs", help=".srt 字幕：燒進畫面(burn) 或封裝為可開關字幕(mux)")
    p.add_argument("input")
    p.add_argument("srt")
    p.add_argument("--mode", choices=["burn", "mux"], default="burn")
    p.add_argument("--font", default="Noto Sans CJK TC", type=ff_font)
    p.add_argument("--font-size", type=ff_int(4, 200), default=16)
    p.add_argument("--margin", type=ff_int(0, 2000), default=60, help="字幕距底部距離")
    p.add_argument("--lang", default="chi", type=ff_lang, help="mux 時的語言標籤")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_subs)

    p = sub.add_parser("audio", help="抽出音訊")
    p.add_argument("input")
    p.add_argument("-f", "--format", choices=["mp3", "wav", "m4a", "flac"], default="mp3")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_audio)

    p = sub.add_parser("autosub", help="用 faster-whisper 自動產生 .srt (可選 --burn)")
    p.add_argument("input")
    p.add_argument("--model", default="small", help="tiny/base/small/medium/large-v3")
    p.add_argument("--language", help="zh / ja / en … (預設自動偵測)")
    p.add_argument("--prompt", help="提示詞，例如 '以下是繁體中文字幕。' 可讓輸出偏向繁體")
    p.add_argument("--device", default="auto", help="auto / cpu / cuda")
    p.add_argument("--compute-type", default="int8", help="int8 / float16 …")
    p.add_argument("--no-vad", dest="vad", action="store_false", help="關閉靜音過濾")
    p.add_argument("--srt", help="輸出 .srt 路徑")
    p.add_argument("--burn", action="store_true", help="產生後直接燒進影片")
    p.add_argument("--font", default="Noto Sans CJK TC", type=ff_font)
    p.add_argument("--font-size", type=ff_int(4, 200), default=16)
    p.set_defaults(func=cmd_autosub)
    return ap


def main(argv=None) -> int:
    global DRY_RUN
    if not shutil.which("ffmpeg"):
        print("找不到 ffmpeg，請先安裝: sudo apt install ffmpeg", file=sys.stderr)
        return 127
    a = build_parser().parse_args(argv)
    DRY_RUN = a.dry_run
    _sanitize_paths(a)
    return a.func(a)


def _sanitize_paths(a) -> None:
    """Turn every user path into an absolute local path so ffmpeg can never read it as an
    option ('-f ...') or a protocol URL ('concat:', 'http://', 'pipe:' ...)."""
    for attr in ("input", "srt", "output"):
        v = getattr(a, attr, None)
        if v:
            setattr(a, attr, media_path(v, must_exist=(attr != "output")))
    if getattr(a, "inputs", None):
        a.inputs = [media_path(v, must_exist=True) for v in a.inputs]
    if getattr(a, "out_dir", None):
        a.out_dir = media_path(a.out_dir)


if __name__ == "__main__":
    sys.exit(main())
