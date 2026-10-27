# Skills

本目录是公开版 Skill 的唯一维护来源。Agent 先读根目录 AGENTS.md，再按 intents/ 的执行条件加载需要的 SKILL.md。Skill 包含模型判断、编排与验收；确定性接口见 tools/。

- Wiki：source、entity、concept、comparison、solution、synthesis、note 七个页型，加 wiki-map-page 与 wiki-generation-map-sync。
- 入库：raw-wiki-ingest；先提取、查重、写页、校验，再更新地图。
- 资料处理：long-document-extraction、feishu-document-extraction、wechat-article-capture、douyin-fast-transcribe。
- 学习：wiki-learning，含状态脚本、教学规则和模板。
- 写作：knowledge-article-writing，只分发方法与外部来源链接。

从仓库根运行命令。支持自动发现 Skills 的客户端需按自身文档接入；也可直接指定某个 SKILL.md，不假定所有客户端都会扫描本目录。
