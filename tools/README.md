# 工具入口

基础安装和可选依赖见 [README](../README.md)。工具处理确定性操作；模型理解与语义判断由 skills/ 承接。

| 工具 | 输入与输出 | 说明 |
|---|---|---|
| raw_wiki_ingest.py | raw、证据与页面计划 → 分块、来源与完成校验 | [接口](raw_wiki_ingest.md) |
| wiki_lookup.py | 关键词 → Wiki 候选与匹配依据 | [检索流程](wiki_retrieval.md) |
| wiki_relations.py | 页面计划 → 关系候选与链接检查 | [接口](raw_wiki_ingest.md) |
| wiki_map_sync.py | 实际页面、地图基线和决定 → 覆盖校验 | `--help` |
| batch_locate_quotes.py | source 页 → 引文定位 | `--help` |
| memory_catalog.py、memory_commit.py | 元数据查询、经确认的变更 → 索引与提交回执 | [查询](memory_catalog.md)、[写入](../memory/写入Workflow.md) |
| learning_state.py | 学习状态与版本 → 状态、证据和总览 | [接口](learning_state.md) |
| paddleocr_parse.py | PDF / 图片 → 机器 Markdown、图片与版面记录 | [接口](paddleocr_parse.md) |
| wechat_capture.py | 微信 URL → 正文、HTML 与图片 | [接口](wechat_capture.md) |
| local_audio_transcribe.py、bilibili_transcribe.py | 音频 / 视频 URL → 完整机器稿 | [音频](local_audio_transcribe.md)、[B站](bilibili_transcribe.md) |
| audio_backend.py | 本地音频处理、模型安装与环境检查 | `--help` |
