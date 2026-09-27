"""Shared security helpers used by every JiangKit tool.

- paths:  path-traversal / zip-slip safe joins and extraction
- net:    SSRF-safe HTTP (public IPs only, re-checked per redirect and per connected socket)
- proc:   argument-list subprocess helpers + ffmpeg argument sanitising
- csvsafe: CSV/Excel formula-injection escaping
- redact: secret redaction for logs
- images: EXIF/metadata stripping on export
"""
