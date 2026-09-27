# THIRD_PARTY_LICENSES — JiangKit 匠具 套件

產生日期：2026-09-27（UTC+8）。資料來源：`pip-licenses`（套件 venv 實際安裝版本）、`npm ls --omit=dev` + 各套件 `package.json`、
模型／素材由人工查核上游授權頁。原始輸出保存在 `reports/`（`pip-licenses-*.json`、`npm-licenses-*.tsv`）。

> 本文件列出**執行時**相依與隨附素材。開發工具（pytest、bandit、semgrep、pip-audit、vite、typescript、playwright）不隨產品散布，另列於文末。

## 0. 結論摘要（能否販售封閉式 Pro 版）

| 項目 | 授權 | 影響 | 處理方式 |
|---|---|---|---|
| **PyMuPDF**（`docs.doc` 用） | AGPL-3.0 / Artifex 商業授權 | 若把 PyMuPDF 與封閉程式一起散布，會觸發 AGPL | **隔離**：只有免費 MIT 模組 `docs.doc` 會 import；它是可選 extra `[docs]`，任何 wheel/zip 都**不內含** PyMuPDF 本體；Pro 模組完全不 import。若要把 PDF 功能做成付費功能，需購買 Artifex 商業授權或改用 pypdf（BSD）。 |
| **InsightFace buffalo_l 權重** | 非商業研究用途 | 不能用於付費產品 | **已移除**：`ai.face_consistency` 只保留 YuNet（MIT）+ SFace（Apache-2.0）後端。 |
| **Piper `en_US-lessac` 聲音**（原 subtitle-studio 示範影片旁白） | Blizzard 2013 授權（非商業） | 不能隨付費產品散布 | **已替換**：改用 Piper `en_US-ljspeech-high`（LJ Speech 資料集 Public Domain、模型 MIT）重新合成，並附預先做好的 SRT。見 `products/subtitle-studio/samples/DEMO-CLIP-LICENSE.md`。 |
| **piper-tts** 1.8.0 | GPL-3.0 | 僅賣方電腦上用來產生音檔 | 不隨任何產品散布；產出的音訊不受 GPL 約束。 |
| soxr（librosa 相依） | LGPL-2.1+ | 透過 pip 動態載入，使用者可替換 | 可商用；不修改、不靜態連結、不重新打包進單一執行檔。 |
| certifi / orjson / tqdm / dompurify | MPL-2.0（dompurify 為 MPL-2.0 **或** Apache-2.0） | 檔案層級 copyleft | 未修改原始碼，可商用；dompurify 選 Apache-2.0。 |
| tld（trafilatura→courlan 相依） | MPL-1.1 **或** GPL-2.0 **或** LGPL-2.1 | 三選一 | 選 MPL-1.1。 |
| simple-icons（linkinbio） | CC0-1.0 | 圖示本身為公有領域 | ⚠️ 品牌標誌仍是各公司**商標**，只能用來指向該服務，不可暗示合作／背書（已寫在 linkinbio README「授權與商標」）。 |
| FFmpeg、Tesseract、pandoc、zsh | LGPL/GPL、Apache-2.0、GPL-2.0+、MIT-like | 外部程式 | **不隨附**，由使用者自行安裝；我們只以子行程呼叫（不連結）。此公開 repo 不含任何 zsh 執行檔（測試在找不到系統 zsh 時自動略過）。 |

**沒有發現會阻擋販售封閉 Pro 版的 GPL/AGPL/非商業元件**（上述 AGPL 已隔離、非商業項目已移除或替換）。

## 1. AI 模型與權重（皆為首次使用時下載，不內含於任何發行檔）

| 模型 | 用途 | 授權 | 來源／固定版本 |
|---|---|---|---|
| YuNet `face_detection_yunet_2023mar.onnx` | 臉部偵測（face_consistency、best_frame） | MIT | opencv/opencv_zoo；下載時驗證 SHA-256 `8f2383e4…2552fa4` |
| SFace `face_recognition_sface_2021dec.onnx` | 臉部特徵（face_consistency） | Apache-2.0 | opencv/opencv_zoo；SHA-256 `0ba9fbfa…87c34e79` |
| CLIP ViT-B-32 `laion2b_s34b_b79k` | auto_tagger（預設） | MIT | `laion/CLIP-ViT-B-32-laion2B-s34B-b79K`，固定 commit `1a25a446…`，只下載 `open_clip_model.safetensors`（非 pickle） |
| OpenAI CLIP `clip-vit-base-patch32` | auto_tagger 後備（需另裝 transformers） | MIT（openai/CLIP） | 固定 commit `3d74acf9…`；此版本只有 `pytorch_model.bin`，由 transformers 以 `weights_only` 載入 |
| faster-whisper `small` / `tiny`（Systran，CTranslate2 格式） | video autosub、subtitle-studio 聽寫 | MIT（Whisper 權重 MIT） | `Systran/faster-whisper-*`；CTranslate2 格式非 pickle |
| ~~InsightFace buffalo_l~~ | — | 非商業 | **已移除** |

## 2. 字型與媒體素材

| 素材 | 授權 | 備註 |
|---|---|---|
| Noto Sans/Serif CJK（系統字型） | SIL OFL 1.1 | 執行時使用系統已安裝字型，不隨附 |
| DejaVu Sans（系統字型） | Bitstream Vera / Public Domain | 僅用於渲染示範影片畫面文字，不隨附字型檔 |
| `subtitle-studio/samples/demo-clip.mp4` | 旁白：LJ Speech（Public Domain）+ piper-voices 模型（MIT）；畫面自製 | 見 `DEMO-CLIP-LICENSE.md` |
| 網站與 sales-kit 截圖、示範輸出 | 自製（執行本套件工具產生） | 使用合成／程式產生的圖片，**不含任何真人或個人參考圖** |

## 3. Python 執行時相依（套件 venv：`.venv`，共 134 個）

| 套件 | 版本 | 授權 | 備註 |
|---|---|---|---|

| annotated-doc | 0.0.5 | MIT |  |
| annotated-types | 0.8.0 | MIT |  |
| anyio | 4.15.1 | MIT |  |
| audioop-lts | 0.2.2 | PSF-2.0 |  |
| av | 18.1.0 | BSD-3-Clause |  |
| babel | 2.18.0 | BSD License |  |
| beautifulsoup4 | 4.15.0 | MIT License |  |
| brotli | 1.2.0 | MIT |  |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | ℹ️ MPL（檔案層級 copyleft，未修改可商用） |
| cffi | 2.1.1 | MIT-0 |  |
| charset-normalizer | 3.5.1 | MIT |  |
| click | 8.5.0 | BSD-3-Clause |  |
| cloudpickle | 3.1.2 | BSD License |  |
| colorama | 0.4.6 | BSD License |  |
| contourpy | 1.4.0 | BSD-3-Clause |  |
| courlan | 1.4.0 | Apache-2.0 |  |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |  |
| ctranslate2 | 4.8.2 | MIT |  |
| cycler | 0.12.1 | BSD License |  |
| dateparser | 1.4.3 | BSD-3-Clause |  |
| decorator | 5.3.1 | BSD-2-Clause |  |
| et_xmlfile | 2.0.0 | MIT License |  |
| fastapi | 0.141.1 | MIT |  |
| faster-whisper | 1.2.1 | MIT License |  |
| filelock | 4.0.4 | MIT |  |
| flatbuffers | 25.12.19 | Apache Software License |  |
| fonttools | 4.66.0 | MIT |  |
| fsspec | 2026.9.0 | BSD-3-Clause |  |
| ftfy | 6.3.1 | Apache-2.0 |  |
| gradio | 6.28.0 | Apache-2.0 |  |
| gradio_client | 2.7.1 | Apache-2.0 |  |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |  |
| groovy | 0.1.2 | MIT License |  |
| h11 | 0.16.0 | MIT License |  |
| hf-gradio | 0.4.1 | MIT |  |
| hf-xet | 1.6.0 | Apache-2.0 |  |
| htmldate | 1.10.0 | Apache-2.0 |  |
| httpcore | 1.0.9 | BSD-3-Clause |  |
| httpx | 0.28.1 | BSD License |  |
| huggingface_hub | 1.33.0 | Apache Software License |  |
| idna | 3.20 | BSD-3-Clause |  |
| ImageHash | 4.3.2 | 2-clause BSD License |  |
| ImageIO | 2.37.4 | BSD-2-Clause |  |
| iniconfig | 2.3.0 | MIT |  |
| jiangkit | 1.0.0 | MIT (free tier); see LICENSE and THIRD_PARTY_LICENSES. |  |
| Jinja2 | 3.1.6 | BSD License |  |
| joblib | 1.6.0 | BSD-3-Clause |  |
| jusText | 3.0.2 | BSD License |  |
| kiwisolver | 1.5.1 | BSD License |  |
| lazy-loader | 0.6 | BSD-3-Clause |  |
| librosa | 1.0.0 | ISC License (ISCL) |  |
| llvmlite | 0.49.0 | BSD-2-Clause AND Apache-2.0 WITH LLVM-exception |  |
| lxml | 6.1.3 | BSD-3-Clause |  |
| lxml_html_clean | 0.4.5 | BSD-3-Clause |  |
| mando | 0.7.1 | MIT License |  |
| Markdown | 3.11 | BSD-3-Clause |  |
| markdown-it-py | 4.2.0 | MIT License |  |
| MarkupSafe | 3.0.3 | BSD-3-Clause |  |
| matplotlib | 3.11.2 | Python Software Foundation License |  |
| mdurl | 0.1.2 | MIT License |  |
| mido | 1.3.3 | MIT License |  |
| mpmath | 1.3.0 | BSD License |  |
| msgpack | 1.2.2 | Apache-2.0 |  |
| narwhals | 2.26.0 | MIT |  |
| networkx | 3.7 | BSD-3-Clause |  |
| numba | 0.67.0 | BSD License |  |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |  |
| onnxruntime | 1.30.0 | MIT License |  |
| open_clip_torch | 3.3.0 | MIT License |  |
| opencc-python-reimplemented | 0.1.7 | Apache Software License |  |
| opencv-python-headless | 5.0.0.93 | Apache Software License |  |
| openpyxl | 3.1.5 | MIT License |  |
| orjson | 3.12.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | ℹ️ MPL（檔案層級 copyleft，未修改可商用） |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |  |
| pandas | 3.0.6 | BSD License |  |
| pillow | 12.3.0 | MIT-CMU |  |
| platformdirs | 4.12.0 | MIT |  |
| playwright | 1.63.0 | Apache-2.0 |  |
| pluggy | 1.6.0 | MIT License |  |
| pooch | 1.9.0 | BSD-3-Clause |  |
| protobuf | 7.36.2 | 3-Clause BSD License |  |
| psutil | 7.2.2 | BSD-3-Clause |  |
| pycparser | 3.0 | BSD-3-Clause |  |
| pycryptodomex | 3.23.0 | BSD License; Public Domain |  |
| pydantic | 2.13.5 | MIT |  |
| pydantic_core | 2.46.5 | MIT |  |
| pydub | 0.25.1 | MIT License |  |
| pyee | 13.0.1 | MIT License |  |
| Pygments | 2.21.0 | BSD-2-Clause |  |
| pymupdf | 1.28.2 | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License | ⚠️ AGPL（已隔離） |
| pyparsing | 3.3.3 | MIT |  |
| pypdf | 6.19.0 | BSD-3-Clause |  |
| pytesseract | 0.3.13 | Apache Software License |  |
| pytest | 9.1.1 | MIT |  |
| python-dateutil | 2.9.0.post0 | Apache Software License; BSD License |  |
| python-multipart | 0.0.32 | Apache-2.0 |  |
| pytz | 2026.4 | MIT License |  |
| PyWavelets | 1.10.0 | MIT AND BSD-3-Clause |  |
| PyYAML | 6.0.3 | MIT License |  |
| pyzipper | 0.4.0 | MIT |  |
| qrcode | 8.2 | BSD License; Other/Proprietary License | ℹ️ 分類標籤雜訊（實為 BSD） |
| radon | 6.0.1 | MIT License |  |
| regex | 2026.9.10 | Apache-2.0 AND CNRI-Python |  |
| requests | 2.34.2 | Apache Software License |  |
| rich | 15.0.0 | MIT License |  |
| safehttpx | 0.1.7 | MIT License |  |
| safetensors | 0.8.0 | Apache Software License |  |
| scikit-image | 0.26.0 | BSD License |  |
| scikit-learn | 1.9.1 | BSD-3-Clause |  |
| scipy | 1.18.1 | BSD License |  |
| semantic-version | 2.10.0 | BSD License |  |
| shellingham | 1.5.4 | ISC License (ISCL) |  |
| six | 1.17.0 | MIT License |  |
| soundfile | 0.14.0 | BSD License |  |
| soupsieve | 2.10 | MIT |  |
| soxr | 1.1.0 | LGPL-2.1-or-later | ℹ️ LGPL（動態連結，可商用） |
| starlette | 1.7.0 | BSD-3-Clause |  |
| sympy | 1.14.0 | BSD License |  |
| threadpoolctl | 3.7.0 | BSD-3-Clause |  |
| tifffile | 2026.9.20 | BSD-3-Clause |  |
| timm | 1.0.30 | Apache Software License |  |
| tld | 0.13.2 | MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later | ℹ️ MPL（檔案層級 copyleft，未修改可商用） |
| tokenizers | 0.23.2 | Apache Software License |  |
| tomlkit | 0.14.0 | MIT License |  |
| torch | 2.14.0+cpu | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD |  |
| torchvision | 0.29.0+cpu | BSD |  |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | ℹ️ MPL（檔案層級 copyleft，未修改可商用） |
| trafilatura | 2.2.0 | Apache-2.0 |  |
| typer | 0.27.2 | MIT |  |
| typing-inspection | 0.4.4 | MIT |  |
| typing_extensions | 4.16.0 | PSF-2.0 |  |
| tzlocal | 5.4.4 | MIT |  |
| urllib3 | 2.8.0 | MIT |  |
| uvicorn | 0.54.0 | BSD-3-Clause |  |

## 4. Subtitle Studio 後端相依（`products/subtitle-studio/.venv`，共 36 個）

| 套件 | 版本 | 授權 | 備註 |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT |  |
| annotated-types | 0.8.0 | MIT |  |
| anyio | 4.15.1 | MIT |  |
| av | 18.1.0 | BSD-3-Clause |  |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | ℹ️ MPL（檔案層級 copyleft，未修改可商用） |
| cffi | 2.1.1 | MIT-0 |  |
| click | 8.5.0 | BSD-3-Clause |  |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |  |
| ctranslate2 | 4.8.2 | MIT |  |
| fastapi | 0.141.1 | MIT |  |
| faster-whisper | 1.2.1 | MIT License |  |
| filelock | 4.0.4 | MIT |  |
| flatbuffers | 25.12.19 | Apache Software License |  |
| fsspec | 2026.9.0 | BSD-3-Clause |  |
| h11 | 0.16.0 | MIT License |  |
| hf-xet | 1.6.0 | Apache-2.0 |  |
| httpcore | 1.0.9 | BSD-3-Clause |  |
| httpx | 0.28.1 | BSD License |  |
| huggingface_hub | 1.33.0 | Apache Software License |  |
| idna | 3.20 | BSD-3-Clause |  |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |  |
| onnxruntime | 1.30.0 | MIT License |  |
| opencc-python-reimplemented | 0.1.7 | Apache Software License |  |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |  |
| protobuf | 7.36.2 | 3-Clause BSD License |  |
| pycparser | 3.0 | BSD-3-Clause |  |
| pydantic | 2.13.5 | MIT |  |
| pydantic_core | 2.46.5 | MIT |  |
| python-multipart | 0.0.32 | Apache-2.0 |  |
| PyYAML | 6.0.3 | MIT License |  |
| starlette | 1.7.0 | BSD-3-Clause |  |
| tokenizers | 0.23.2 | Apache Software License |  |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | ℹ️ MPL（檔案層級 copyleft，未修改可商用） |
| typing-inspection | 0.4.4 | MIT |  |
| typing_extensions | 4.16.0 | PSF-2.0 |  |
| uvicorn | 0.54.0 | BSD-3-Clause |  |

## 5. 網頁產品 npm 執行時相依（`npm ls --omit=dev`）

### mockup-studio

| 套件 | 版本 | 授權 |
|---|---|---|
| @noble/ed25519 | 3.2.0 | MIT |
| fflate | 0.8.3 | MIT |

### invoice-pro

| 套件 | 版本 | 授權 |
|---|---|---|
| @babel/runtime | 7.29.7 | MIT |
| @noble/ed25519 | 3.2.0 | MIT |
| @types/pako | 2.0.4 | MIT |
| @types/raf | 3.4.3 | MIT |
| @types/trusted-types | 2.0.7 | MIT |
| base64-arraybuffer | 1.0.2 | MIT |
| canvg | 3.0.11 | MIT |
| core-js | 3.50.0 | MIT |
| css-line-break | 2.1.0 | MIT |
| dompurify | 3.4.16 | (MPL-2.0 OR Apache-2.0) |
| fast-png | 6.4.0 | MIT |
| fflate | 0.8.3 | MIT |
| html2canvas | 1.4.1 | MIT |
| iobuffer | 5.4.0 | MIT |
| jspdf | 4.2.1 | MIT |
| pako | 2.2.0 | (MIT AND Zlib) |
| performance-now | 2.1.0 | MIT |
| raf | 3.4.1 | MIT |
| regenerator-runtime | 0.13.11 | MIT |
| rgbcolor | 1.0.1 | MIT OR SEE LICENSE IN FEEL-FREE.md |
| stackblur-canvas | 2.7.0 | MIT |
| svg-pathdata | 6.0.3 | MIT |
| text-segmentation | 1.0.3 | MIT |
| utrie | 1.0.2 | MIT |

### linkinbio-builder

| 套件 | 版本 | 授權 |
|---|---|---|
| @noble/ed25519 | 3.2.0 | MIT |
| simple-icons | 16.33.0 | CC0-1.0 |

## 6. 開發／稽核工具（不隨產品散布）

pytest（MIT）、bandit（Apache-2.0）、semgrep CLI（LGPL-2.1，僅本機掃描）、pip-audit（Apache-2.0）、pip-licenses（MIT）、
gitleaks（MIT）、vite（MIT）、typescript（Apache-2.0）、playwright（Apache-2.0）、uv（MIT/Apache-2.0）。

## 7. 重新產生

```bash
.venv-audit/bin/pip-licenses --python .venv/bin/python --format=json --with-urls > reports/pip-licenses-suite.json
(cd products/<app> && npm ls --omit=dev --all --parseable)   # 再讀各 package.json 的 license 欄位
```
