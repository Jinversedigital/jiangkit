# 網路連線清單 / Every network call

JiangKit **沒有任何遙測、分析或自動更新**。`import jiangkit` 時會預設設定
`GRADIO_ANALYTICS_ENABLED=False`、`HF_HUB_DISABLE_TELEMETRY=1`、`DO_NOT_TRACK=1`、`ANONYMIZED_TELEMETRY=False`（可被使用者自行覆寫）。
下表是程式碼中**所有**會連網的地方；除此之外的工具完全離線。

| 模組 | 何時連線 | 目的地 | 防護 |
|---|---|---|---|
| `web.web`（fetch / links / images / watch） | 使用者下指令時 | 使用者指定的 URL | `security/net.py`：只允許 http(s)、封鎖私有／loopback／link-local／CGNAT／metadata（169.254.169.254 等）IP、每次轉址重新驗證、實際連線的 socket peer 再驗一次（防 DNS rebinding）、忽略 proxy 環境變數、逾時 (5s, 20s)、頁面上限 10 MB、圖片上限 25 MB。`--allow-private` / `JIANGKIT_ALLOW_PRIVATE_NET=1` 可明確關閉（本機測試用）。 |
| `social.x_scraper`（login / scrape） | 使用者下指令時 | `https://x.com`（Playwright 瀏覽器） | 僅限個人使用；每次執行印出服務條款提醒；登入資料夾 `~/.local/share/jiangkit/x_profile`（0700）。 |
| `social.x_scraper download-media` | 使用者下指令時 | 預設只允許 `https://*.twimg.com`（pbs/video/abs/ton） | SSRF-safe session、不跟隨轉址、單檔 1 GiB 上限；`--any-host` 可放寬網域但仍封鎖內網 IP。 |
| `ai.face_consistency`、`media.best_frame`（YuNet / SFace；Pro 版，不在此開源 repo） | 第一次使用且快取中沒有模型時 | `https://github.com/opencv/opencv_zoo/...` | SHA-256 固定校驗、200 MB 上限、SSRF-safe；`JIANGKIT_OFFLINE=1` 完全禁止下載；快取於 `~/.cache/jiangkit/models`（`JIANGKIT_MODELS_DIR` 可改）。 |
| `ai.auto_tagger`（CLIP；Pro 版，不在此開源 repo） | 第一次使用且 HF 快取中沒有權重時 | `https://huggingface.co`（`laion/CLIP-ViT-B-32-laion2B-s34B-b79K`） | 固定 commit、只抓 safetensors；可設 `HF_HUB_OFFLINE=1`。 |
| `media.video autosub`（faster-whisper） | 第一次使用某模型時 | `https://huggingface.co`（`Systran/faster-whisper-*`） | CTranslate2 格式（非 pickle）；可設 `HF_HUB_OFFLINE=1` 或傳入本機模型路徑。 |
| `system.sys notify --channel webhook/telegram` | 使用者下指令時 | 使用者在環境變數設定的 webhook URL、`https://api.telegram.org` | URL/Token 只從環境變數讀取；只允許 http(s)；輸出時遮蔽 token。 |
| `panel`（Gradio） | 啟動面板時 | 只監聽 `127.0.0.1` | 分析功能關閉、`share=False`、不啟用 MCP/SSR；非 loopback 需 `--i-know-lan`。 |

## 網頁產品（另售，原始碼不在此 repo）

| 產品 | 連線 |
|---|---|
| Mockup Studio / Invoice Pro / Link-in-Bio Builder | **零網路請求**（純前端、資料存在瀏覽器 localStorage / IndexedDB）。授權碼以內嵌公鑰離線驗證。CSP `connect-src 'self'`。 |
| Link-in-Bio 匯出的頁面 | 只包含使用者自己填的外部連結；CSP `default-src 'none'`，頁面本身不發出任何請求（除了使用者設定的 https 大頭貼圖片）。 |
| Subtitle Studio | 後端只監聽 `127.0.0.1:8780`；只有 faster-whisper 第一次下載模型時連到 huggingface.co。 |
