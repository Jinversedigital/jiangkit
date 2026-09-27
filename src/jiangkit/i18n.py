"""Minimal bilingual (zh-TW / English) message catalogue for the CLI and panel.

Language: JIANGKIT_LANG=zh|en|both (default both → "中文 / English").
"""
from __future__ import annotations

import os

MESSAGES: dict[str, tuple[str, str]] = {
    "usage": ("用法", "Usage"),
    "categories": ("分類", "Categories"),
    "tools": ("工具", "Tools"),
    "unknown_category": ("未知的分類：{name}", "Unknown category: {name}"),
    "unknown_tool": ("分類 {cat} 中沒有工具：{name}", "No tool named {name} in category {cat}"),
    "pro_required": ("「{tool}」是 Pro 功能，需要有效授權碼。執行 `{cli} license activate <KEY>` 啟用。",
                     "'{tool}' is a Pro feature and needs a valid license. Run `{cli} license activate <KEY>`."),
    "pro_not_in_build": ("「{tool}」不包含在免費版中。請安裝購買後取得的 Pro 版 wheel。",
                         "'{tool}' is not included in the free build. Install the Pro wheel you received after purchase."),
    "license_invalid": ("授權碼無效：{reason}", "License key invalid: {reason}"),
    "license_ok": ("授權有效：{tier}（{buyer}），到期：{expiry}", "License valid: {tier} ({buyer}), expires: {expiry}"),
    "license_none": ("尚未啟用授權（免費版）", "No license activated (free tier)"),
    "license_saved": ("授權碼已儲存到 {path}", "License saved to {path}"),
    "license_removed": ("已移除授權碼", "License removed"),
    "missing_dep": ("缺少相依套件 {mod}。請安裝：pip install \"{pkg}[{extra}]\"",
                    "Missing dependency {mod}. Install: pip install \"{pkg}[{extra}]\""),
    "interrupted": ("已中斷", "Interrupted"),
    "error": ("錯誤", "Error"),
    "unsafe_input": ("輸入被安全檢查拒絕：{msg}", "Input rejected by safety check: {msg}"),
    "tos_x": ("注意：X 爬蟲僅供個人研究／備份自己可合法存取的內容。自動化擷取可能違反 X 服務條款，"
              "請自行承擔風險，勿用於商業轉售、騷擾或大量蒐集個資。",
              "Notice: the X scraper is for personal research/backup of content you may lawfully access. "
              "Automated collection may violate X's Terms of Service; use at your own risk and never for resale, "
              "harassment or bulk personal-data harvesting."),
    "free": ("免費", "Free"),
    "pro": ("Pro", "Pro"),
}


def lang() -> str:
    v = os.environ.get("JIANGKIT_LANG", "both").lower()
    return v if v in ("zh", "en", "both") else "both"


def t(key: str, **kw) -> str:
    zh, en = MESSAGES.get(key, (key, key))
    zh, en = zh.format(**kw), en.format(**kw)
    mode = lang()
    if mode == "zh":
        return zh
    if mode == "en":
        return en
    return f"{zh}\n{en}" if len(zh) + len(en) > 70 else f"{zh} / {en}"


def pair(zh: str, en: str) -> str:
    mode = lang()
    return zh if mode == "zh" else en if mode == "en" else f"{zh} / {en}"
