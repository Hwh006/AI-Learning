# AI Learning：可追溯的 Agent Wiki 模板

把自己提供的 Markdown 素材整理成可回读、可查重、可校验的知识库。包含页型规范、七个 Wiki Skills、原文分块与证据台账、元数据检索、跨页关联检查及测试。

这是从个人学习项目提取的通用版本。原始知识内容、简历、个人记忆、客户项目记录和旧 Git 历史不随仓库发布。内置素材完全虚构。

## 快速开始

需要 Python 3.10 或更高版本。以下命令从仓库根目录运行：

```bash
python3 -m venv .workflow-venv
# macOS / Linux
source .workflow-venv/bin/activate
# Windows PowerShell 使用 .workflow-venv\Scripts\Activate.ps1
python -m pip install -r requirements-ingest.txt
python tools/wiki_lookup.py search '来源追溯'
python skills/wiki-source-page/scripts/verify_source_page.py --page examples/wiki-demo-source.md
python tools/raw_wiki_ingest.py prepare raw/examples/wiki-demo.md --model-id demo-run
python tools/raw_wiki_ingest.py inspect raw/examples/wiki-demo.md
python -m unittest discover -s tests -v
```

首次运行 tokenizer 可能下载其编码文件，需要网络。普通元数据检索和 source 校验不需要 API Key，也不下载模型权重。

## 让 Agent 使用

在能读取本地文件并运行 Python 的 Agent 客户端中打开仓库，先读 `AGENTS.md` 与 `index.md`。把自己的 Markdown 放进 `raw/`，明确指定文件并要求入库。Agent 按 `tools/raw_wiki_ingest.md` 逐单元取证、规划、撰写和校验。

`prepare` 只是准备步骤，不等于已完成入库。模型提取与语义判断仍由你的 Agent 完成。此仓库不绑定模型供应商，也不提供自动模型调用服务。

## 目录

| 位置 | 用途 |
|---|---|
| `index.md` | 九类 Wiki 页型的职责与边界 |
| `skills/wiki-*/` | 七个页型的写作合同与校验器 |
| `tools/raw_wiki_ingest.py` | 分块、位置、提取台账、页面计划与完成核验 |
| `tools/wiki_lookup.py` | 按标题、标签、别名和导语定位候选 |
| `tools/wiki_relations.py` | 关联候选与 Agent 决定的检查 |
| `tools/batch_locate_quotes.py` | 批量定位摘录 |
| `raw/examples/`、`examples/wiki-demo-source.md` | 可运行的虚构 source 示例，放在 Wiki 外 |
| `review.md` | 使用过程中保留冲突与待核验项 |
| `tests/` | 原文分块、证据和跨页关系的回归测试 |

`wiki/` 保留以下完整目录结构，各目录仅放 `.gitkeep`，不携带知识内容：

```text
wiki/
├── assets/
├── comparisons/
├── concepts/
├── entities/
├── map/
├── notes/
│   └── _history/
├── queries/
├── solutions/
├── sources/
└── synthesis/
```

Git 不跟踪空目录，因此用 `.gitkeep` 保留结构；该文件不参与知识检索。query 和 map 没有独立校验器。校验器主要检查格式、路径、引用和结构；语义正确性需要回读原文核对。

## 本地资料与公开边界

`.gitignore` 默认忽略新增 raw、wiki、memory、个人文档、数据库、模型、运行台账、凭据和客户端状态，仅保留随仓库提供的演示素材。这有助于避免误提交，不能检查已经跟踪的文件或清除历史。

发布清单及排除理由见 `PUBLIC_EXPORT.md`。个人面试工具、需要外部转写后端的入口、针对既有采集台账的维护工具与独立学习画布未纳入此次通用 Wiki 发行版。

## 使用许可

当前仓库未指定开源许可证；公开可见与获得再分发许可是不同事项。若需要发布衍生发行版，请先与仓库作者明确许可。
