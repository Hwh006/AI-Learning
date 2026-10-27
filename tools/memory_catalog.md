# 记忆元数据查询与目录导航工具

`memory_catalog.py` 使用 Python 3 标准库，扫描项目 `memory/` 七类目录下的直接 Markdown 文件，只解析开头的 frontmatter，闭合后停止读取，不查询正文或管理文件。

## 查询

```bash
python3 tools/memory_catalog.py search '分销商城 项目介绍 面试口述' --category 项目 --category 偏好
python3 tools/memory_catalog.py search --category 任务
python3 tools/memory_catalog.py search '简历' --json
```

| 参数 | 含义 |
|---|---|
| `search [query]` | 关键词以空格分隔；省略 query 时浏览所选范围的元数据 |
| `--category 类别` | 七类目录名之一，可重复；省略时扫描七类目录 |
| `--status active\|retired\|all` | 默认只返回 `active`；可查询停用条目或全部状态 |
| `--limit N` | 返回上限，正整数，默认 8 |
| `--json` | 输出结构化结果；默认输出文本 |

查询匹配 `id`、`title`、`tags`、`summary` 和可选 `scope`，忽略大小写，按子串匹配；至少命中一个关键词即可进入候选。按覆盖的不同关键词数降序排列，同分按稳定 id 排列。省略关键词时按 id 排列。不自动中文分词、推导同义词或进行向量检索；排序不等于语义相关性。

结果包含 `query`、`categories`、`total`、`returned`、`truncated`、`results`。每条结果保留文件头部元数据和 `matched_fields`，`path` 相对于 `memory/`。`total` 是当前范围与状态过滤后的全部命中数；`truncated=true` 表示仍有候选未返回，可以增大上限或细化查询。零命中返回空结果、退出码 0，不证明正文或其他目录没有相关记忆。

工具扫描所选目录的元数据后再筛选，只输出返回范围内的候选。`scope` 只参与词面匹配和结果展示，是否适用仍由读取方判断。工具执行接口不负责类别选择、语义取舍或正文应用；处理规则见 [读取机制](../memory/读取机制.md)。

## 生成与检查

```bash
python3 tools/memory_catalog.py
python3 tools/memory_catalog.py --check
```

无参数时校验全部条目，向标准输出生成目录导航，不写文件。生成内容包含七类目录的用途、调用时机、有效/停用数量与入口链接，不包含具体条目的标题、标签、摘要和正文。

`--check` 校验必需字段、摘要长度、分类与路径、稳定 id 唯一性、状态、来源和日期，并核对现有 `memory/索引.md` 是否与生成结果一致。`--check` 不能与 `search` 同时使用。完整校验覆盖七类目录，查询仅校验扫描范围。

元数据错误或索引不一致时返回 JSON 错误及退出码 1；非法命令参数由 argparse 报错并返回退出码 2。工具不调用模型、不修改文件，也不证明记忆事实的真实性或当前有效性。
