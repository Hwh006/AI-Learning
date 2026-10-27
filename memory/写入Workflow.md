# 记忆写入 Workflow：识别—整理—写入

## 入口：接收前置判断与整理结果

当前 Agent 先执行根目录 [AGENTS.md「前置识别与判断」](../AGENTS.md#前置识别与判断)，通过后按 [Memory 整理规则](写入机制.md) 选择七类目录之一、检索关联条目并形成拟保存内容，再按 [写入前确认](../AGENTS.md#写入前确认) 取得用户确认。本 Workflow 接收通过记录、用户确认依据与已整理内容，负责提交，不重新分析整段对话或再次生成正文。未通过、待澄清、未经用户确认或查重后无变化时，不进入提交；提交前核对最终内容与确认范围一致。

## 打包整理文件

在 `data/memory-staging/<唯一批次>/` 一次写好最终条目 Markdown、最终目录导航 Markdown 和 `packet.json`。这些是待提交文件，不参与有效记忆检索；正式导航在提交成功后反映分类数量。修订以当前正文为底稿，保留无关内容。

提交包如下；哈希由脚本读取实际文件计算，不填示例值。`expected_sha256` 为目标当前字节的 SHA-256，新增文件为 `null`；`draft_sha256` 为整理文件字节的 SHA-256。`rules` 分别记录前置判断与整理规则的版本。`judgment` 沿用 AGENTS.md 的通过记录，`reason` 写明范围与拟变更方向，`evidence` 保留对应原话或执行证据，并记录用户对具体拟变更内容的确认依据；不为打包重新判断一遍。

```json
{
  "judgment": {
    "accepted": true,
    "origin": "user_requirement",
    "reason": "用户明确要求后续同类任务沿用",
    "evidence": ["用户原话和当前对话定位", "用户确认的具体拟变更内容、范围及确认原话"]
  },
  "rules": {"AGENTS.md": "实际哈希", "memory/写入机制.md": "实际哈希"},
  "changes": [
    {"target": "memory/偏好/具体要求.md", "expected_sha256": null,
     "draft": "entry.md", "draft_sha256": "实际哈希"},
    {"target": "memory/索引.md", "expected_sha256": "实际旧索引哈希",
     "draft": "index.md", "draft_sha256": "实际哈希"}
  ]
}
```

`origin` 可为 `user_requirement`、`user_statement`、`conversation_experience`；新建条目的来源须与判断记录一致。全部必需字段、100–150 字摘要及分类目录由元数据工具校验。说明文档不经本提交脚本写入；条目目标限七类目录下的直接 Markdown 文件。

## 生成目录导航

已落盘条目可用 `python3 tools/memory_catalog.py` 读取 frontmatter 并输出目录导航草稿。导航只含目录用途、调用时机、分类数量和入口链接，不展开具体条目的元数据。尚未提交的变更，用同一模块生成提交后的导航：

```python
import sys
from pathlib import Path
sys.path.insert(0, "tools")
from memory_catalog import entries, render_index

root = Path.cwd().resolve()
# changes 为提交包中的最终变更，draft 为相对 packet 目录的路径。
overrides = {root / x["target"]: (stage / x["draft"]).read_bytes()
             for x in changes if x["target"] != "memory/索引.md"}
removed = [root / x["move_from"] for x in changes if x.get("move_from")]
(stage / "index.md").write_text(render_index(entries(root, overrides, removed)))
```

补齐导航的 `draft_sha256` 后打包。提交脚本按当前条目与本次变更校验全部元数据、唯一 id 和分类数量，再核对生成的导航；条目具体元数据在后续查询时从文件头部读取，不再复制到导航中。

## 迁移分支

目录调整或重新分类时，目标变更增加 `move_from` 和 `source_sha256`，指明旧路径及其当前字节指纹。例如旧的 `memory/条目/某记忆.md` 迁入 `memory/偏好/某记忆.md`。目标填真实旧版本指纹；新增目标为 `null`。来源必须存在，稳定 id 与来源性质保持不变，正文和链接在整理阶段完成核对。

脚本先验证来源、目标、元数据与最终索引，再写新文件和索引、移除旧文件，最后回读并保存回执。常规异常恢复已写文件与迁移来源；旧文件后来发生变化则拒绝提交。迁移回执记录旧路径、来源指纹和新路径。旧整理包不直接重放，路径与规则变更后按当前状态重新整理。

## 写入

```bash
python3 tools/memory_commit.py data/memory-staging/批次/packet.json --dry-run
python3 tools/memory_commit.py data/memory-staging/批次/packet.json
```

脚本不请求 LLM，只检查判断记录、规则哈希、目标旧版本、整理稿哈希、元数据、唯一 id、目录导航和允许路径，再原样写入并逐字节回读。多个提交通过文件锁串行；常规写入异常回滚已写文件；每个文件通过临时文件替换。同包重复执行返回已有回执，目标后来有变化时拒绝重放。提交后运行 `python3 tools/memory_catalog.py --check` 核对现有目录。

回执在 `data/memory-commits/`，只记录目标与哈希；整理包保留本轮证据，位于 Git 忽略的 `data/`。脚本不能证明语义判断正确，不保证断电时多文件整体原子性；异常中断先核对条目、索引和回执，再按当前版本重新整理。

## 删除分支

当前提交脚本仅支持新建、更新及停用。用户明确要求物理删除时，按整理规则确认删除对象，使用文件工具删除对应条目、刷新导航数量并移除本目录内重复摘录，再回读核对；不将删除伪装成脚本支持的提交。沿用前置判断与整理结果，不再次请求模型改写。不保留含被删除内容的本地整理包；只报告实际清除范围。

识别、整理和工具调用仍占用模型上下文；此处具体承诺是提交阶段无额外模型生成，不承诺整个 Workflow 零 token。
