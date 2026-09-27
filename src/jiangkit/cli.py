"""`jiang` — single entry point for every JiangKit tool.

    jiang                           overview (categories + tools)
    jiang list [--json]             all tools with tier
    jiang <category>                tools in a category
    jiang <category> <tool> [...]   run a tool (its own --help works)
    jiang license status|activate KEY|deactivate
    jiang panel [--host 127.0.0.1] [--port 7860]
    jiang doctor                    check optional dependencies / external programs
    jiang version

Exit codes: 0 ok · 1 error · 2 usage · 3 Pro license required · 4 missing dependency ·
5 unsafe input rejected · 127 external program missing · 130 interrupted
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import traceback

from . import __version__
from ._brand import BRAND_FULL, CLI_NAME, TAGLINE_EN, TAGLINE_ZH
from .errors import (EXIT_ERROR, EXIT_INTERRUPTED, EXIT_MISSING_DEP, EXIT_OK, EXIT_PRO_REQUIRED,
                     EXIT_UNSAFE_INPUT, EXIT_USAGE, JiangKitError, ProRequired)
from .i18n import pair, t
from .registry import CATEGORIES, IMPORT_EXTRA, TOOLS, get, tools_in


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _badge(tier: str) -> str:
    return "[Pro]" if tier == "pro" else "[Free]"


def overview() -> str:
    lines = [f"{BRAND_FULL} {__version__}", TAGLINE_ZH, TAGLINE_EN, "",
             f"{pair('用法', 'Usage')}: {CLI_NAME} <category> <tool> [args...]   ({CLI_NAME} <category> <tool> --help)", ""]
    for cat, (zh, en) in CATEGORIES.items():
        lines.append(f"  {cat:<13} {zh} / {en}")
        for tl in tools_in(cat):
            lines.append(f"      {tl.name:<20} {_badge(tl.tier):<7} {pair(tl.zh, tl.en)}")
    lines += ["", f"  license       {pair('授權：status / activate KEY / deactivate', 'License: status / activate KEY / deactivate')}",
              f"  panel         {pair('本機網頁面板（預設 127.0.0.1）', 'Local web panel (127.0.0.1 by default)')}",
              f"  doctor        {pair('檢查相依套件與外部程式', 'Check optional deps & external programs')}",
              f"  list          {pair('列出全部工具（--json）', 'List all tools (--json)')}"]
    return "\n".join(lines)


def _missing_extra(exc: ImportError) -> str:
    name = (getattr(exc, "name", None) or "").split(".")[0]
    return IMPORT_EXTRA.get(name, "all")


def _safety_errors() -> tuple[type, ...]:
    from .security.net import ResponseTooLarge, SSRFError
    from .security.paths import UnsafePathError
    from .security.proc import UnsafeArgumentError

    return (SSRFError, ResponseTooLarge, UnsafePathError, UnsafeArgumentError)


def run_tool(category: str, name: str, argv: list[str]) -> int:
    tool = get(category, name)
    if tool is None:
        _err(t("unknown_tool", cat=category, name=name))
        return EXIT_USAGE
    if tool.note:
        _err(t(tool.note))
    if tool.tier == "pro":
        from . import license as lic
        from .registry import module_available

        if not module_available(tool):
            _err(t("pro_not_in_build", tool=tool.id))
            return EXIT_PRO_REQUIRED

        st = lic.status()
        if not st.is_pro:
            _err(t("pro_required", tool=tool.id, cli=CLI_NAME))
            if st.reason not in ("missing", ""):
                _err(t("license_invalid", reason=st.reason))
            return EXIT_PRO_REQUIRED
    try:
        mod = importlib.import_module(tool.module)
    except ImportError as e:
        extra = tool.extra or _missing_extra(e)
        _err(t("missing_dep", mod=getattr(e, "name", "?"), pkg="jiangkit", extra=extra))
        return EXIT_MISSING_DEP
    old_argv0 = sys.argv[0]
    sys.argv[0] = f"{CLI_NAME} {category} {name}"
    safety = _safety_errors()
    try:
        rc = mod.main(argv)
        return int(rc) if isinstance(rc, (int, bool)) else EXIT_OK
    except SystemExit as e:  # argparse / sys.exit inside tools
        code = e.code
        if code is None:
            return EXIT_OK
        if isinstance(code, int):
            return code
        _err(str(code))
        return EXIT_ERROR
    except KeyboardInterrupt:
        _err(t("interrupted"))
        return EXIT_INTERRUPTED
    except safety as e:
        _err(t("unsafe_input", msg=_redact(str(e))))
        return EXIT_UNSAFE_INPUT
    except ProRequired:
        _err(t("pro_required", tool=tool.id, cli=CLI_NAME))
        return EXIT_PRO_REQUIRED
    except ImportError as e:
        _err(t("missing_dep", mod=getattr(e, "name", "?"), pkg="jiangkit", extra=tool.extra or _missing_extra(e)))
        return EXIT_MISSING_DEP
    except JiangKitError as e:
        _err(f"{t('error')}: {_redact(str(e))}")
        return e.exit_code
    except Exception as e:  # last-resort handler: short message, traceback only in debug
        _err(f"{t('error')}: {type(e).__name__}: {_redact(str(e))}")
        if os.environ.get("JIANGKIT_DEBUG") == "1":
            traceback.print_exc()
        return EXIT_ERROR
    finally:
        sys.argv[0] = old_argv0


def _redact(s: str) -> str:
    from .security.redact import redact

    return redact(s)


def cmd_license(argv: list[str]) -> int:
    from . import license as lic

    sub = argv[0] if argv else "status"
    if sub == "status":
        st = lic.status()
        if st.valid:
            print(t("license_ok", tier=st.tier, buyer=st.payload.get("b", "?"), expiry=st.payload.get("x", "—")))
            return EXIT_OK
        print(t("license_none") if st.reason == "missing" else t("license_invalid", reason=st.reason))
        return EXIT_OK if st.reason == "missing" else EXIT_PRO_REQUIRED
    if sub == "activate":
        if len(argv) < 2:
            _err(f"{t('usage')}: {CLI_NAME} license activate <KEY>")
            return EXIT_USAGE
        st, path = lic.activate(argv[1])
        if not st.valid:
            _err(t("license_invalid", reason=st.reason))
            return EXIT_PRO_REQUIRED
        print(t("license_saved", path=path))
        print(t("license_ok", tier=st.tier, buyer=st.payload.get("b", "?"), expiry=st.payload.get("x", "—")))
        return EXIT_OK
    if sub == "deactivate":
        lic.deactivate()
        print(t("license_removed"))
        return EXIT_OK
    _err(f"{t('usage')}: {CLI_NAME} license status|activate KEY|deactivate")
    return EXIT_USAGE


def cmd_list(argv: list[str]) -> int:
    if "--json" in argv:
        print(json.dumps([{"id": x.id, "tier": x.tier, "extra": x.extra, "zh": x.zh, "en": x.en} for x in TOOLS],
                         ensure_ascii=False, indent=2))
        return EXIT_OK
    for x in TOOLS:
        print(f"{x.id:<32} {_badge(x.tier):<7} {pair(x.zh, x.en)}")
    return EXIT_OK


def cmd_doctor(argv: list[str]) -> int:
    rows = []
    for mod in sorted(IMPORT_EXTRA):
        try:
            importlib.import_module(mod)
            ok = True
        except Exception:
            ok = False
        rows.append((mod, IMPORT_EXTRA[mod], ok))
    print(pair("Python 套件", "Python packages") + ":")
    for mod, extra, ok in rows:
        print(f"  {'✔' if ok else '✘'} {mod:<16} [{extra}]")
    print(pair("外部程式", "External programs") + ":")
    for prog in ("ffmpeg", "ffprobe", "git", "tesseract", "pandoc"):
        print(f"  {'✔' if shutil.which(prog) else '✘'} {prog}")
    return EXIT_OK


def cmd_panel(argv: list[str]) -> int:
    try:
        from .panel.app import main as panel_main
    except ImportError as e:
        _err(t("missing_dep", mod=getattr(e, "name", "gradio"), pkg="jiangkit", extra="panel"))
        return EXIT_MISSING_DEP
    return panel_main(argv) or 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(overview())
        return EXIT_OK
    head, rest = argv[0], argv[1:]
    if head in ("-V", "--version", "version"):
        print(f"{BRAND_FULL} {__version__}")
        return EXIT_OK
    special = {"license": cmd_license, "list": cmd_list, "doctor": cmd_doctor, "panel": cmd_panel}
    if head in special:
        return special[head](rest)
    if head not in CATEGORIES:
        _err(t("unknown_category", name=head))
        _err(f"{t('categories')}: {', '.join(CATEGORIES)}")
        return EXIT_USAGE
    if not rest or rest[0] in ("-h", "--help"):
        zh, en = CATEGORIES[head]
        print(f"{head}: {zh} / {en}\n")
        for tl in tools_in(head):
            print(f"  {tl.name:<20} {_badge(tl.tier):<7} {pair(tl.zh, tl.en)}")
        print(f"\n{t('usage')}: {CLI_NAME} {head} <tool> --help")
        return EXIT_OK
    return run_tool(head, rest[0], rest[1:])


if __name__ == "__main__":
    sys.exit(main())
