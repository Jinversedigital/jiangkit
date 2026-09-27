"""Exit codes shared by every JiangKit command (documented in README)."""
EXIT_OK = 0
EXIT_ERROR = 1          # generic runtime failure
EXIT_USAGE = 2          # bad arguments (argparse also uses 2)
EXIT_PRO_REQUIRED = 3   # Pro tool without a valid license
EXIT_MISSING_DEP = 4    # optional dependency / extra not installed
EXIT_UNSAFE_INPUT = 5   # rejected by a security check (path traversal, SSRF, ...)
EXIT_EXTERNAL_TOOL = 127  # ffmpeg / git / tesseract ... not found
EXIT_INTERRUPTED = 130


class JiangKitError(Exception):
    exit_code = EXIT_ERROR


class ProRequired(JiangKitError):
    exit_code = EXIT_PRO_REQUIRED


class UnsafeInput(JiangKitError):
    exit_code = EXIT_UNSAFE_INPUT
