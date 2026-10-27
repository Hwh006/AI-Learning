# Raw Wiki 入库记录工具

`tools/raw_wiki_ingest.py` 是确定性 CLI：登记 raw 哈希、结构分块、精确位置、逐字证据、页面计划和校验状态。它不调用模型、不写知识页，也不判定知识点边界或关系语义。需要模型理解与 Wiki 编排的任务由 [raw-wiki-ingest Skill](../skills/raw-wiki-ingest/SKILL.md) 执行。

## 环境与输入

从项目根目录运行，使用 `.workflow-venv/bin/python`；依赖 `requirements-ingest.txt` 中的真实 tokenizer。原文必须是 `raw/` 内现有 UTF-8 Markdown，原文文件只读；运行记录写入 `outputs/context-engineering/ingest-runs/`。环境缺失时：

```bash
python3 -m venv .workflow-venv
.workflow-venv/bin/python -m pip install -r requirements-ingest.txt
```

`prepare` / 运行加载入口拒绝 `raw_role` 为 `ocr_output`、`ocr_page`、`aggregate`、`index` 或 `ingest_allowed: false` 的输入；`section` 必须带来源、标题链、定位、结构与正文审核标记。可通过相邻 PaddleOCR manifest 识别旧版 `document.md`。原有 source 取证和摘录定位仍可回查这些文件。角色字段与章节 raw 格式见 [长文档 Skill 的产物合同](../skills/long-document-extraction/references/structured-raw.md)。

## 子命令

以下 `SOURCE` 是一份 raw 路径；所有参数的精确类型也可用各命令的 `--help` 查询。

| 命令 | 用途与主要参数 | 主要结果 |
|---|---|---|
| `prepare SOURCE --model-id ID` | 哈希登记与分块；可选 `--encoding`、`--chunk-tokens`、`--model-tokens` | 运行目录、结构模式、单元数、source 登记状态 |
| `inspect SOURCE [--unit-id ID]` | 检查覆盖或回读指定单元 | 原文范围、状态、覆盖错误与待处理项 |
| `outline SOURCE` | 列出结构及已记录知识条目分布 | 标题路径与索引 |
| `next SOURCE` | 返回一个尚未记录的单元及提交契约 | `unit_id`、`heading_path`、`text` |
| `record SOURCE --unit-id ID --json-file FILE` | 校验并保存模型的单元提取；可选 `--replace` | 引文位置与记录结果 |
| `find SOURCE --query TEXT [--include-raw]` | 在提取索引中定位主题 | 匹配条目及可选原文 |
| `context SOURCE --unit-id ID [--adjacent N]` | 精确回读已计划的证据；相邻数 0–4 | 单元原文与位置 |
| `record-plan SOURCE --json-file FILE` | 校验七类页型决定、证据单元和关联台账 | `page-plan.json` 路径 |
| `verify-plan SOURCE` | 核验当前计划、真实文件、链接与保护状态 | 完成状态、错误、待核关系 |
| `verify-source PAGE` | 校验 source 必填字段、原文哈希、摘录 | 通过或失败 |
| `locate-quote SOURCE --quote-file FILE` | 连续原话逐字定位 | 所有命中位置，含章节或行段范围 |

调用示例：

```bash
.workflow-venv/bin/python tools/raw_wiki_ingest.py prepare 'raw/材料/章节.md' --model-id '本次运行唯一标识'
.workflow-venv/bin/python tools/raw_wiki_ingest.py next 'raw/材料/章节.md'
.workflow-venv/bin/python tools/raw_wiki_ingest.py record 'raw/材料/章节.md' --unit-id '返回ID' --json-file /tmp/unit.json
.workflow-venv/bin/python tools/raw_wiki_ingest.py record-plan 'raw/材料/章节.md' --json-file /tmp/page-plan.json
.workflow-venv/bin/python tools/raw_wiki_ingest.py verify-plan 'raw/材料/章节.md'
```

## 单元提取 JSON

`record` 的输入形状：

```json
{
  "unit_id": "从 next 原样复制",
  "summary": "这一个单元的导航摘要",
  "items": [
    {
      "type": "concept",
      "topic": "具体主题",
      "statement": "原文作者的说法或可定位的知识线索",
      "quotes": ["当前单元内连续且逐字一致的原文片段"],
      "conditions": [],
      "examples": [],
      "uncertainties": []
    }
  ]
}
```

条目 `type` 枚举为 `concept`、`method`、`project_design`、`comparison`、`process`、`rule`、`case`、`entity_mention`、`uncertainty`。每个引文必须是当前单元内唯一命中的逐字原文，脚本计算字符、字节与行号位置。ID、类型、引文或 JSON 不合格时拒绝记录。所有单元记录完成且无待核超限单元后，状态进入 `ready`。

## 页面计划 JSON

下面只展示一个页面项；实际契约要求 `source`、`concept`、`solution`、`comparison`、`entity`、`synthesis`、`note` 均有决定，允许同类多个页面项。

```json
{
  "pages": [{
    "page_type": "concept",
    "target_path": "wiki/concepts/示例评测.md",
    "title": "示例评测",
    "keywords": ["评测", "人工复核"],
    "sources": ["sources/示例原文.md"],
    "action": "create",
    "reason": "原文支撑独立方法概念，查重无同义页",
    "evidence_requirements": [
      {"question": "评测机制是什么？", "unit_ids": ["..."], "coverage": "covered"}
    ],
    "relations": [{
      "candidate_path": "wiki/notes/示例方法.md",
      "decision": "related",
      "relation": "概念到执行步骤",
      "reason": "读者理解评测维度后可继续查看实际复核步骤",
      "evidence": ["候选正文步骤4；本轮原文单元..."],
      "placements": [{
        "from_path": "wiki/concepts/示例评测.md",
        "to_path": "wiki/notes/示例方法.md",
        "section": "怎么用"
      }]
    }]
  }]
}
```

- `action`：`create | merge | skip | review`；每项需要具体 `reason`。已登记的 create 写盘后可以保留原动作继续同一计划；首次登记时目标已存在则需 merge/review。
- `evidence_requirements`：问题、真实 `unit_ids`、`coverage: covered | partial | not_found | conflict`；covered/partial 需要证据单元。
- create/merge 项需要标题与 1–6 个关键词，可带别名、summary 和 sources。source 路径可写 `wiki/sources/xxx.md` 或 `sources/xxx.md`。
- `relations`：候选路径、`decision: related | unrelated | pending`、关系、原因、证据和 placements。强候选必须有决定；不关联/待核可以为空 placements，但仍须关系说明与原因。
- `placements`：真实 `from_path`、`to_path`、精确 Markdown 标题 `section`；导语使用 `开篇`，元数据使用 `frontmatter`，可选 `target_anchor`。

关联候选与单独核验工具：

```bash
python3 tools/wiki_relations.py candidates --plan /tmp/page-plan.json --output /tmp/relation-candidates.json
python3 tools/wiki_relations.py verify --plan /tmp/page-plan.json --output /tmp/relation-check.json
```

候选器读取标题、别名、标签、导语、共同来源和现有链接，含本轮计划页面。完整名/别名、当前来源范围内的直接引用或共同来源且主题命中会构成强候选；仅标签/来源相同通常为弱候选。强候选全部保留，弱候选默认每页最多 8 个，map 仅作导航。具体选择理由由 Skill 中的 Agent 填写。

## 输出、缓存与限制

运行目录包含 `manifest.json`、`units.jsonl`、`summaries.jsonl` 与 `page-plan.json`；指针、原文完整哈希、模型标识、解析配置和 tokenizer 版本决定复用范围。原文或摘要哈希改变时旧计划失效，需要更新登记。隐私扫描只输出候选数量，不回显候选敏感值。

结构分块仅识别 Markdown `#` 至 `######`，忽略 fenced code 内标题；正文编号、列表、表格和时间标记不成为结构标题。无标题或单节过长时按真实 token 数切分，默认上限 20,000；单句超限保留定位并标 `needs_review`。该操作不生成章节 raw。

stdout 为 JSON；非法参数、无效证据、过期计划或运行错误非零退出，结果状态 `failed` / `needs_review` 也退出 1。`verify-plan` 的 page_count 是计划项数，含 skip/review，不是实际新建 Wiki 数；关系待核单列。它检查强候选决定、链接章节/目标/锚点和 verified 页面保护，不能证明知识分类完整、引用支持论述或阅读关系合理。页型自身校验由对应 Skill 的校验工具完成。
