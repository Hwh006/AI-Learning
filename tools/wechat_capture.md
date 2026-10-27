# 微信文章采集工具

封装上游 [bzd6661/wechat-article-for-ai](https://github.com/bzd6661/wechat-article-for-ai)，固定 commit `69de9e413cca3fe6b770c40a4dec204afd5b2b3c`。采集、解析、转换及图片下载均复用其实现；本地适配层负责固定环境、验证正文、版本化保存、缓存校验和 JSON 结果。

```bash
# 检查持久环境，不访问网页
python3 tools/wechat_capture.py --doctor

# 初次安装或恢复环境；不会重置不同版本的现有代码
python3 tools/wechat_capture.py --setup

# 采集一篇获准访问的文章，默认下载图片
python3 tools/wechat_capture.py 'https://mp.weixin.qq.com/s/ARTICLE_ID'

# 用户要求重新采集：新建版本，保留旧文件
python3 tools/wechat_capture.py 'https://mp.weixin.qq.com/s/ARTICLE_ID' --refresh

# 完全离线解析用户提供的 HTML，URL 仅作来源标识
python3 tools/wechat_capture.py 'https://mp.weixin.qq.com/s/ARTICLE_ID' --html '/绝对路径/文章.html'
```

`--output` 指定输出根目录；默认 `raw/articles/wechat/`。`--no-images` 保留远程图片链接；`--verbose` 增加日志。stdout 是一份 JSON，stderr 是日志。成功 exit 0；无正文、环境缺失或 URL 无效时非零退出。`partial_images` 也为 exit 0，但只能表示正文采集成功；调用者须单独检查图片状态。

保存结构为 `<输出根目录>/<URL SHA-256前20位>/<UTC采集时间>/`。`article.md` 是完整转换稿，`original.html` 是未经改写的页面 HTML，`capture.json` 记录来源、上游版本、正文/HTML哈希及逐图结果，`images/` 是本地图片。默认复用最新完整性校验通过的缓存；损坏缓存会报错，不静默覆盖。失败时清理未完成的 staging；不会把验证页保存为正文。

持久源码和独立虚拟环境位于 `tools/.wechat-runtime/`（Git 忽略），依赖版本保存在 `tools/wechat-requirements.lock.txt`。这是 CLI 工具封装，Skill 经 Codex 技能目录发现；没有修改 MCP 配置。上游虽自带 MCP Server，本次不需要额外服务即可调用。

环境检查仅验证依赖和浏览器文件，不证明某个链接当前能够访问。验证码返回 `verification_required`，网络重试由上游负责（最多3次），不会自动解验证码。已有访问策略阻断的目标不以此工具绕过。采集完成不自动生成 Wiki；入库另走项目 Workflow。
