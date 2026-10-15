# tessdata

`img_no_watermark.py` 的 OCR 检测依赖 `chi_sim.traineddata`（简体中文 Tesseract 语言数据）。
仓库不内置该二进制文件（体积大且随版本更新），请按需自取：

- 系统包：`apt install tesseract-ocr-chi-sim`（脚本会自动在系统目录找到）
- 或手动下载 `chi_sim.traineddata` 放到本目录：
  <https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata>

脚本查找顺序：`skills/shared/tessdata/` → `TESSDATA_PREFIX` 环境变量 → 系统目录。
缺失时 OCR 文字检测自动降级（其余检测手段不受影响）。
