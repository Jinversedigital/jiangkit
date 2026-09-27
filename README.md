# JiangKit 匠具

> 創作者與開發者的本機工具箱：一個指令、一個面板、資料不出門。
> A local-first toolbox for creators & developers — one CLI, one panel, your data stays home.

🌐 **作品集網站 / Portfolio site:** <https://jinversedigital.github.io/jiangkit/>

**這個 repo 是 JiangKit 的免費開源版（MIT）**，包含 18 個免費工具、CLI `jiang` 與本機網頁面板。
**JiangKit Pro 版（另 7 個工具）與四個網頁產品（Mockup Studio、Invoice Pro、Link-in-Bio Builder、Subtitle Studio）為商業產品、另行販售，原始碼不在此 repo。**

**This repository is the free, open-source edition of JiangKit (MIT)**: 18 free tools, the `jiang` CLI and a local web panel.
**JiangKit Pro (7 additional tools) and the four web products are commercial and sold separately; their source code is not in this repository.**

- [繁體中文](#繁體中文) · [English](#english)
- 安全報告 / Security: [`SECURITY_REPORT.md`](SECURITY_REPORT.md) · 第三方授權 / Third-party licences: [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) · 網路呼叫清單 / Network calls: [`docs/NETWORK.md`](docs/NETWORK.md)

---

## 繁體中文

### 安裝

需要 Python ≥ 3.10。影片工具需要系統安裝 **ffmpeg**；OCR 需要 **tesseract**（`jiang doctor` 會檢查）。本套件**未發佈到 PyPI**，請從原始碼安裝：

```bash
git clone https://github.com/Jinversedigital/jiangkit.git && cd jiangkit
python -m venv .venv && source .venv/bin/activate      # Windows：.venv\Scripts\activate
pip install -e .                        # 基本安裝（Pillow、requests、cryptography…）
pip install -e ".[video,data,docs]"     # 依需要加 extras
```

| extra | 內容 | 用於 |
|---|---|---|
| （基本） | pillow、requests、beautifulsoup4、cryptography、psutil | img、files、sys |
| `web` | lxml、trafilatura、imagehash | web |
| `scraper` | playwright | x_scraper（另需 `playwright install chromium`） |
| `data` | pandas、openpyxl、defusedxml、matplotlib | data |
| `files` | pyzipper | AES 加密備份 |
| `social` / `text` | qrcode / opencc | social、text |
| `docs` | PyMuPDF（**AGPL**，選用）、pypdf、pytesseract、markdown | doc（見 [`LICENSE-DOCS-AGPL.md`](LICENSE-DOCS-AGPL.md)） |
| `video` | faster-whisper | 自動字幕 |
| `experimental` | numpy、matplotlib、mido、radon | 實驗室 |
| `panel` | gradio | 本機網頁面板 |

可重現安裝（含雜湊）：`pip install --require-hashes -r requirements/base.lock.txt` 後 `pip install --no-deps -e .`

### 18 個免費工具

| 類別 | 工具 |
|---|---|
| media 影像與影片 | `img`（社群尺寸、浮水印、去 EXIF、轉檔、找重複）、`video`（ffmpeg 剪輯、9:16、GIF、字幕） |
| social 社群 | `social`（內容行事曆、主題標籤、字數檢查、UTM、QR code）、`x_scraper`（見下方說明） |
| docs 文件與文字 | `doc`（PDF 合併／分割／OCR）、`text`（繁簡轉換、字幕處理） |
| data 資料表 | `data`（CSV／Excel／JSON 轉換、統計、圖表） |
| files 檔案管理 | `files`（批次改名、整理、找重複、增量備份） |
| web 網頁 | `web`（擷取文字／連結／圖片、監看變化，SSRF 防護） |
| system 系統 | `sys`（桌面通知、工作紀錄、連接埠與行程） |
| experimental 實驗室 | git_symphony、shell_timemachine、image_to_level、data_sonify、code_weather、entropy_clock、regex_from_examples、dream_diff |

```bash
jiang --help                  # 全部工具（中英雙語）
jiang list                    # 列表（Pro 工具會標示，免費版執行時會以結束碼 3 提示）
jiang media img fit photo.jpg -p ig-portrait -o out/
jiang data data chart sales.csv --x month --y sales --kind bar -o chart.png
jiang experimental regex_from_examples -p 2026-09-27 -p 1999-01-05 -n hello
jiang panel                   # 只綁定 127.0.0.1:7860
```

> ⚠️ **關於 `social.x_scraper`：** 僅供個人使用，使用者須自行遵守 X 的服務條款；每次執行都會顯示服務條款警告。本專案不提供、不推廣任何違反服務條款的用途。

### 測試

```bash
pip install -e ".[web,scraper,data,files,social,text,docs,video,experimental,panel]" pytest
python -m pytest
```

### 授權

- 本 repo：[`LICENSE`](LICENSE)（MIT）。
- `docs.doc` 在執行時使用選用的 PyMuPDF（AGPL-3.0），本 repo 不內含 PyMuPDF：[`LICENSE-DOCS-AGPL.md`](LICENSE-DOCS-AGPL.md)。
- 第三方元件：[`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md)。
- Pro 版與網頁產品為另售的商業授權，不在此 repo。

---

## English

### Install

Python ≥ 3.10. Video tools need **ffmpeg**; OCR needs **tesseract** (`jiang doctor` checks both). JiangKit is **not published on PyPI**; install from source:

```bash
git clone https://github.com/Jinversedigital/jiangkit.git && cd jiangkit
python -m venv .venv && source .venv/bin/activate
pip install -e .                        # light base install
pip install -e ".[video,data,docs]"     # add extras as needed
```

Extras: `web`, `scraper`, `data`, `files`, `social`, `text`, `docs` (optional AGPL PyMuPDF — see [`LICENSE-DOCS-AGPL.md`](LICENSE-DOCS-AGPL.md)), `video`, `experimental`, `panel`. Hash-pinned lock files are in `requirements/`.

### The 18 free tools

- **media:** `img` (social sizes, watermark, EXIF strip, convert, dedupe), `video` (ffmpeg trim, 9:16, GIF, subtitles)
- **social:** `social` (content calendar, hashtags, caption limits, UTM links, QR codes), `x_scraper` (see note below)
- **docs:** `doc` (PDF merge/split/OCR), `text` (OpenCC, subtitle tools)
- **data:** `data` (CSV/Excel/JSON convert, stats, charts)
- **files:** `files` (batch rename, organise, dedupe, incremental backups)
- **web:** `web` (extract text/links/images, watch pages; SSRF-protected)
- **system:** `sys` (desktop notifications, logged jobs, ports & processes)
- **experimental:** git_symphony, shell_timemachine, image_to_level, data_sonify, code_weather, entropy_clock, regex_from_examples, dream_diff

```bash
jiang --help
jiang list
jiang panel        # binds to 127.0.0.1:7860 only
```

Exit codes: 0 ok · 1 error · 2 usage · 3 Pro-only / not in the free build · 4 missing extra · 5 rejected by a security check · 127 external program missing · 130 interrupted.

> ⚠️ **About `social.x_scraper`:** personal use only. You are responsible for complying with X's Terms of Service; the tool prints a ToS warning on every run. This project does not endorse any use that violates those terms.

### Privacy & security

No telemetry, no analytics, no auto-update. Exported images have EXIF/GPS stripped by default. Every network call is listed in [`docs/NETWORK.md`](docs/NETWORK.md). Hardening (SSRF, zip-slip, ffmpeg argument injection, CSV formula injection, HTML escaping + CSP) is summarised in [`SECURITY_REPORT.md`](SECURITY_REPORT.md).

### Tests

```bash
pip install -e ".[web,scraper,data,files,social,text,docs,video,experimental,panel]" pytest
python -m pytest
```

### Licence

MIT ([`LICENSE`](LICENSE)). Third-party components: [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md). **JiangKit Pro and the web products are commercial and sold separately — they are not part of this repository.**

## Repository layout

```text
src/jiangkit/   free-edition package (CLI, registry, i18n, security/, panel/, tools)
tests/          tests for the free tools + security tests
site/           portfolio site (zh-TW + en/), deployed to GitHub Pages
docs/           NETWORK.md, site-shots/
requirements/   hash-pinned lock files
```
