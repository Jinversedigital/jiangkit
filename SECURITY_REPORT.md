# JiangKit 匠具 安全報告（SECURITY_REPORT）— 免費開源版 / Free edition

> 範圍：此公開 repo 內的 Python 套件 `jiangkit` 免費版（18 個工具、CLI、本機面板、`jiangkit.security` 模組）。
> JiangKit Pro 模組與四個網頁產品為另售的商業產品，不在此 repo，其稽核細節不在此公開。
> 原始稽核日期：2026-09-27（UTC+8）。
>
> Scope: the free, open-source `jiangkit` package in this repository. JiangKit Pro and the web products are sold separately and are not covered here.

## 1. 摘要 / Summary

| 檢查 | 工具 | 結果 |
|---|---|---|
| Python 依賴漏洞 | pip-audit | `requirements/*.lock.txt`：0 個已知漏洞（稽核當日） |
| Python 靜態分析 | bandit | 初始 3 高 / 4 中 / 48 低 → **0**（修正或逐行 `# nosec <ID> - 理由`） |
| 多語言靜態分析 | semgrep（p/python、p/security-audit、p/secrets） | 套件原始碼無未處理項目 |
| 祕密外洩 | gitleaks | 此 repo：**0 筆**（見 §7） |
| 測試 | pytest | 見 §8 |

依賴版本以 `uv pip compile --generate-hashes` 鎖定（`requirements/base.lock.txt`、`requirements/all.lock.txt`，可 `pip install --require-hashes`）。

## 2. 發現並修正的問題

嚴重度：🔴 高　🟠 中　🟡 低

### 2.1 授權與祕密
- 授權驗證採 **Ed25519 公鑰簽章**：套件只內嵌**公鑰**（`src/jiangkit/license.py`），完全離線驗證。私鑰與發碼工具不在此 repo、也不在任何發行檔。
- 🟠 **Shell 時光機** 以未加鹽 SHA-256 記錄祕密指紋（可暴力比對）→ 改為本機隨機金鑰的 HMAC 標籤（金鑰檔 0600），資料庫 0600，暫存 env 檔一定刪除，hook 使用 `umask 077`，輸出經遮蔽。
- 🟡 日誌／錯誤訊息遮蔽 token、密碼、Authorization、cookie 等（`jiangkit.security.redact` + logging filter）。

### 2.2 Python 工具
- 🔴 **舊 Gradio 儀表板綁定 0.0.0.0** → 新面板預設 `127.0.0.1`，綁定非 loopback 需明確旗標 `--i-know-lan`；上傳大小上限、工作目錄 0700、輸出 6 小時後自動清除、結束時清除。
- 🔴 **sys_tools PowerShell 命令注入**（通知標題／內容直接拼進指令）→ 參數化並正確跳脫；`subprocess` 一律使用參數陣列。
- 🟠 **SSRF**（web_tools 抓取任意 URL）→ `jiangkit.security.net`：只允許 http/https；封鎖私有、loopback、link-local、CGNAT、multicast、保留位址與雲端 metadata；DNS 解析後檢查、每次轉址重新檢查、socket 層檢查實際連線對象（防 DNS rebinding）；忽略 proxy 環境變數；逾時、大小與轉址次數上限。x_scraper 僅允許 X 網域。
- 🟠 **Zip-slip / 路徑穿越**：拒絕 `..`、絕對路徑、磁碟代號、符號連結；zip bomb 限制；`safe_join`、`safe_filename`。
- 🟠 **批次改名格式字串注入**（`{0.__class__...}`）→ 只允許白名單欄位。
- 🟠 **ffmpeg 參數注入** → 輸入一律轉絕對路徑／`file:`、拒絕 URL 與協定、濾鏡參數白名單驗證。
- 🟠 **HTML 報告 XSS**（dream_diff、code_weather 等）→ 全部 `html.escape`，每份報告帶 CSP。
- 🟠 **CSV / XLSX 公式注入** → `jiangkit.security.csvsafe` 於所有 CSV/XLSX 匯出加前置 `'`。
- 🟠 **XXE**：openpyxl 讀取不受信任 XLSX → 安裝 `defusedxml`（openpyxl 自動採用），有測試確認。
- 🟡 YAML 一律 `safe_load`；不對不受信任資料使用 pickle（測試掃描確認）；JSON 大小限制。
- 🟡 **隱私**：匯出圖片預設移除 EXIF/GPS；無任何遙測（測試確認）。
- 🟡 bandit：md5/sha1 用於非安全用途 → `usedforsecurity=False`；`assert` 改為明確例外。

## 3. 抑制（suppression）原則
所有 `# nosec` / `nosemgrep` 皆寫明規則 ID 與理由（例如：固定執行檔的參數陣列 subprocess、`sys run --shell` 明確 opt-in、使用者重跑**自己的**歷史指令）。

## 4. 網路呼叫
完整清單見 [`docs/NETWORK.md`](docs/NETWORK.md)。基本安裝的工具不會主動連網；只有使用者明確執行的指令才會連線，且經 SSRF／網域白名單、逾時、大小限制。

## 5. 授權檢查的誠實說明
任何用戶端檢查都能被修改程式碼繞過；Ed25519 只保證授權碼無法被偽造。免費版本身不含 Pro 模組原始碼，Pro 工具在免費版中會以結束碼 3 提示。

## 6. 殘餘風險
- 本機面板**沒有登入驗證**，依賴只綁 127.0.0.1；若以 `--i-know-lan` 對外開放，同網段的人都能使用。
- faster-whisper 首次下載的模型未固定 revision。
- `sys run --shell` 與 `tm run` 會執行使用者自己提供的指令（設計如此）。
- x_scraper 使用使用者自己的登入 session；是否符合 X 服務條款由使用者自行負責。

## 7. 公開 repo 掃描 / Public repo scan
發佈前對此 repo 的全部檔案執行 gitleaks，並以 grep 檢查 PEM 私鑰標頭、授權碼、GitHub token、`.env` 等；Pro 模組原始碼不存在於 repo 中（由測試與發佈腳本確認）。結果：**0 筆洩漏**。

## 8. 測試 / Tests
`tests/`：toolkit（img/video/web）、toolkit2（data/doc/file/social/sys/text）、x_scraper、novel（8 個實驗室作品）、`tests/security/`（SSRF、zip/路徑、ffmpeg 參數、輸出跳脫、隱私、授權閘門、面板、時光機祕密、其他強化）。
執行：`python -m pytest`。
