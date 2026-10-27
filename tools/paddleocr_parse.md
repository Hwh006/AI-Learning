# PaddleOCR PDF 与图片解析工具

上游为 [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)，源码固定在 `v3.7.0` / `b03f46425e8ff4442b268ce449e3eef758146cd4`。本地 CLI 调用官方发布包的 PP-StructureV3，完成版面检测、文字识别和图片区域裁剪，生成 Markdown、独立图片与版面 JSON。

## 使用

在项目根目录执行：

```bash
# 检查环境和模型缓存；不识别文件
python3 tools/paddleocr_parse.py --doctor

# 解析整个 PDF，机器输出默认放在 outputs/paddleocr/
python3 tools/paddleocr_parse.py '/绝对路径/文档.pdf'

# 只解析指定页，页码从 1 开始
python3 tools/paddleocr_parse.py '/绝对路径/文档.pdf' --pages 1 3

# 图片也可解析；自定义输出位置
python3 tools/paddleocr_parse.py '/绝对路径/扫描页.png' --output outputs/pdf-review

# 初次安装或恢复固定版本环境，需要 uv、Git 与网络
python3 tools/paddleocr_parse.py --setup
```

脚本自动使用 `tools/.paddleocr-runtime/venv/` 中的 Python 3.12，无需手动激活环境。识别在本地 CPU 上完成，不需要 API Key。上游源码在该运行目录的 `repo/`，模型在 `cache/official_models/`；整个运行目录被 Git 忽略。安装依赖固定在 `tools/paddleocr-requirements.lock.txt`，包含 PaddleOCR 3.7.0、PaddlePaddle 3.3.1、PaddleX 3.7.2。`--setup` 恢复源码和包，模型在第一次实际解析时下载并持久复用。

stdout 为 JSON 结果，stderr 为日志；成功返回 0，失败返回非零。每次运行使用新的目录，不覆盖原文或旧结果；失败清理本次未完成目录。返回的 `next_skill` 指向 [长文档提取 Skill](../skills/long-document-extraction/SKILL.md)，由模型判断是否继续结构化处理。图文校对、章节拆分和逐篇入库规则由该 Skill 维护。

## 输出

```text
outputs/paddleocr/<文件名>-<源文件哈希前12位>-<UTC时间>/
  document.md                 # 合并正文，使用相对路径引用独立图片
  manifest.json               # 原文件路径、SHA-256、版本、页码、图片清单
  pages/page-0001/
    page.md                   # 本页正文与图片引用
    layout.json               # 官方完整版面数据：区域类别、坐标、OCR 等
    imgs/                     # 本页裁剪出的图片
```

逐页单独存图，避免不同页的同名图片相互覆盖。PDF 按 144 DPI 渲染；`layout.json` 中坐标对应渲染后页面的像素坐标。`manifest.json` 的图片清单提供路径、尺寸与 SHA-256；区域坐标通过对应页的 `layout.json` 回查。结果为未经人工校对的机器识别，`human_verified` 为 `false`。

新输出的 `document.md` 标记 `raw_role: ocr_output`，逐页 `page.md` 标记 `raw_role: ocr_page`，都带 `ingest_allowed: false`。它们用于取证和结构化整理，不直接传给 Wiki 入库工具；解析 manifest 同时记录角色和 `next_skill`。旧版 `raw/pdf_paddleocr/` 输出仍可读取，其结构化处理也使用对应 Skill。

## 当前运行配置与边界

- Apple Silicon CPU 推理，4 线程，关闭 MKL-DNN。
- 版面模型：`PP-DocLayout_plus-L`；文字模型：`PP-OCRv5_mobile_det`、`PP-OCRv5_mobile_rec`，支持中英文。
- 导出的图片是检测区域的页面裁剪图，不保证等于 PDF 内嵌原图文件。图片检测可能漏检或框选不完整。
- 当前聚焦正文和图片导出：未启用表格结构、公式、印章或图表语义识别，也未启用方向分类和图像矫正。不能把本工具输出当成完整表格/公式还原。
- 支持 PDF、PNG、JPG、JPEG、WebP、BMP。默认 CPU 配置偏重可运行和模型体积；小字、复杂排版或高精度需求需要另行评测。
- `--doctor` 只核对环境、CPU 张量运算和模型缓存，不代表识别质量。模型默认从官方 BOS 源下载；可用 `PADDLE_PDX_MODEL_SOURCE`、`PADDLE_PDX_CACHE_HOME` 覆盖来源与缓存位置。

官方能力与调用依据：[PP-StructureV3 教程](https://www.paddleocr.ai/main/version3.x/pipeline_usage/PP-StructureV3.html)。
