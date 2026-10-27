# 学习状态工具

项目入口 tools/learning_state.py 调用 Skill 自带的 scripts/learning_state.py。唯一实现随 Skill 一起分发；复制整个 wiki-learning 文件夹即可在其他工作目录使用，不需要本项目 tools/。

工具校验模型整理的候选状态，以版本检查和原子替换保存 learning/<domain>/<topic>/state.json，保留已有用户回答，重建 learning/index.md。工具不生成题目或判断答案的专业正确性，也不写 Wiki、个人 memory 或学习手册。

完整字段、状态和节点契约见 [状态定义](../skills/wiki-learning/references/state-machine.md)，样板见 [state.json](../skills/wiki-learning/assets/state.json)。

## 项目内调用

```bash
python3 tools/learning_state.py validate /tmp/learning-candidate.json
python3 tools/learning_state.py save /tmp/learning-candidate.json --expected-revision 0
python3 tools/learning_state.py index
```

## 独立调用

直接执行 Skill 目录中的 scripts/learning_state.py，参数完全相同；使用 --root 指定学习工作目录。独立入口缺省使用当前工作目录，项目兼容入口缺省使用本项目根目录。

依赖 Python 3.10+ 标准库，不需要 PyYAML 或其他第三方包。文件锁按 POSIX／Windows 环境选择实现；平台行为仍以实际验证为准。

## 数据与检查

- 新记录使用 schema_version=2，Map 可为空；旧 schema_version=1 与 wiki_refs 记录仍可读取，旧原答不为升级而改写。
- source_refs 支持存在的本地文件、http／https URL、inline:编号以及明确标注的 knowledge:主题。URL 只检查形式；内联原文必须存在于 source_notes；实际读取、真实性和专业正确性由 Agent 核对。
- 首次 revision=0，以未学状态建档，可含第一道待答题但不能含虚构历史。后续从真实状态读取版本作为 expected-revision，成功保存后加一。
- 检查状态、当前节点、回答证据、整轮测验、掌握条件及待答问题的一致性；不以进度文字或用户说“懂了”代替真实回答。
- 已有原答、内联原文不能被删除或改写；修正材料用新来源 ID，新的理解或评价用新记录。改变未答题要保留明确原因。
- 状态目标限定在学习工作目录的 learning/ 下，资料可以是用户明确提供的外部文件路径；工具不修改资料。

## 结果与失败

成功返回实际状态路径、revision 与 index 更新结果，不在 stdout 打印题目答案。退出码 0 为成功，2 为无效输入、引用错误、版本冲突或文件操作错误。

旧版本覆盖被拒绝时，先重新读状态再合并，不循环强制重试。状态已保存但总览失败时返回 index=failed；修复后重建总览，不声称全部产物完成。

没有 Python 时按 Skill 的手工保存协议核对、保存并回读；没有文件系统时输出恢复包，不宣称磁盘持久化。
