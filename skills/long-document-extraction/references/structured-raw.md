# 结构化 Raw 产物合同

由 long-document-extraction Skill 在写元数据、结构清单或队列时读取。此处只定义产物与字段；处理判断与入库交接从 Skill 入口执行。

## 产物与处理单位

| 产物 | 用途 | `raw_role` | `ingest_allowed` |
|---|---|---|---|
| PaddleOCR `document.md` / `pages/*/page.md` | 机器识别与版面证据，页顺序不等于章节顺序 | `ocr_output` / `ocr_page` | `false` |
| 可选全文汇编 | 回查或对照；可以省略，不能替代已拆分 raw | `aggregate` | `false` |
| `目录.md` | 标题树、raw 清单、覆盖范围与待校项 | `index` | `false` |
| 章节 raw | 一个最小正式结构单元的完整正文及所属图片；入库的实际单位 | `section` | 校对与结构核对通过后为 `true` |

解析产物默认保存在 `outputs/paddleocr/`。正式 raw 建议放在 `raw/documents/<来源短名>/`；已有网页提取目录可继续用 `raw/articles/<来源短名>/`。同一来源只选一套权威章节 raw；不同时把全文汇编与其子集加入入库队列。

```text
outputs/paddleocr/<来源>-<哈希>-<解析时间>/
  document.md
  manifest.json
  pages/page-0001/{page.md,layout.json,imgs/...}
raw/documents/<来源短名>/
  目录.md
  structure.json
  01_第一章/
    01_00_章前正文.md       # 仅父章有独立正文时存在
    01_01_一级标题.md
    01_02_01_子标题.md
raw/assets/<来源短名>/
  figure-001.png
outputs/context-engineering/ocr-ingest/<来源ID>/
  ingest-queue.json         # 动态入库进度，不写回 raw 原文
```

Raw 文件按原书/原文层级保存证据；Wiki 按知识问题和页型组织。一个 raw 可以生成多篇不同页型的 Wiki；多份 raw 也可以逐次补充同一篇 concept 或 note。二者不要求一一对应。


## Raw 元数据与来源定位

每份 raw 写下列元数据。未知作者/日期留空或注明未提供；不要生成虚构值。下面是格式示例，不是本次材料事实。

同名标题可以出现在不同章节，`section_id` 须用完整父级路径和源文顺序区分；不能只以标题文本覆盖同名文件。

```yaml
---
raw_role: section
ingest_allowed: true
source_id: book-a-<源文件哈希前12位>
source_title: 示例书名
source_file: raw/source_files/book-a.pdf
source_sha256: <源文件完整64位SHA-256>
source_url: null
author: 未提供
extraction_method: paddleocr
extraction_manifest: outputs/paddleocr/<本次运行>/manifest.json
section_id: chapter-01-section-02-subsection-01
parent_section_id: chapter-01-section-02
heading_level: 3
heading_path: ["第一章", "1.2 标题", "1.2.1 子标题"]
source_pages: [12, 13]
source_locators: [{page: 12, blocks: [4, 5, 6]}, {page: 13, blocks: [0, 1]}]
structure_manifest: raw/documents/book-a/structure.json
split_basis: formal_heading
structure_review: confirmed
content_review: agent_reviewed
ocr_review: agent_reviewed
human_verified: false
extracted_at: "2026-10-20T00:00:00+08:00"
---
```

页码指源 PDF 的 1 起始页码；印刷页码另外记录，不能混用。`source_locators` 中的 block 必须对应本次 `layout.json` 中实际存在的区域；不支持块编号时改记原页面坐标或截图文件与区域，不猜编号。直接获取的长文本可记录原文件行号/字符范围，`extraction_method: text_extract`、`ocr_review: not_applicable`，用 `content_review` 标记正文复核；`extraction_manifest` 指向真实的文本提取与定位清单。多个 OCR 运行、补录表格和手动纠错的详细映射记录在 `structure.json`；每次补录都指回实际源页。

`source_sha256` 是提取输入源文件哈希；入库 source 页的 `sha256` 则是该份章节 Markdown 的哈希，两者用途不同。网页无原始文件时，`source_file` 指明实际截图/截图 PDF，并同时保留网页 URL 与截图清单。元数据的角色和审核状态用单行标量，列表建议用行内 YAML，便于入口检查。元数据只作定位和过程说明，不是作者原文，不能被提炼为来源中的业务事实。


## 入库队列字段

`structure.json` 和 `目录.md` 列出所有正式标题、对应 raw 或仅作容器的理由、页码/区域、图文校对状态。对章节 raw 分别计算完整文件哈希，按源文阅读顺序生成 `ingest-queue.json`；动态队列放在 `outputs/`，入库中不改 raw 原文的状态字段。

入队前确认元数据引用的 manifest、版面定位文件和图片都位于计划保留的位置。需要归档时先保存引用所需的证据并更新路径，再计算 raw 哈希；入库后不能为了清理临时目录而删除被引用文件，或无记录地改写已登记 raw。

```json
{
  "source_id": "book-a-<hash>",
  "structure_manifest": "raw/documents/book-a/structure.json",
  "items": [
    {
      "order": 1,
      "raw_path": "raw/documents/book-a/01_第一章/01_01_一级标题.md",
      "raw_sha256": "<该章节Markdown完整SHA-256>",
      "section_id": "chapter-01-section-01",
      "state": "pending",
      "run_dir": null,
      "wiki_pages": [],
      "unresolved": []
    }
  ]
}
```

同一队列不能同时包含汇编和其子集，也不能同时包含复制了子节正文的父章与子节。全量检查标题覆盖、非空正文与图片归属，每处正文有且仅有一个章节归属；结构、图片或缺页尚未解决的项目单列为 `needs_review`，不伪装成空章节或直接略过。正文相同但位置不同的原文重复仍需保留来源位置，不仅凭文本哈希删除。
