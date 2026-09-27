# 關於 `docs.doc` 與 PyMuPDF（AGPL-3.0）

`jiangkit.docs.doc_tools`（PDF 合併、分割、轉圖、OCR、Markdown/DOCX→PDF）本身以 **MIT** 授權，
但它在執行時 import **PyMuPDF**，而 PyMuPDF 採 **AGPL-3.0 或 Artifex 商業授權** 雙授權。

我們的隔離方式：

1. PyMuPDF 只列在可選 extra `[docs]`，**任何發行檔（wheel、sdist、Pro zip、完整封存）都不內含 PyMuPDF 本體**，由使用者自行 `pip install` 取得。
2. 只有免費模組 `docs.doc` 會 import PyMuPDF；**所有 Pro 模組都不 import**，因此 Pro 版的封閉授權不會與 AGPL 程式結合。
3. `docs.doc` 屬於免費層、原始碼公開（MIT 與 AGPL 相容）。

如果你想把 PDF 功能做成**付費功能**或打包成單一執行檔散布，請擇一：
- 向 Artifex 購買 PyMuPDF 商業授權；或
- 改用 BSD 授權的 `pypdf`（合併／分割／旋轉已可用），渲染與 OCR 改用其他非 copyleft 方案。
