---
name: wechat-article-capture
description: 使用本地 wechat-article-for-ai（Camoufox）采集微信公众号文章正文、HTML 和图片。当用户提供 mp.weixin.qq.com 文章链接并要求解析、阅读、总结、提取或生成 Wiki 时优先使用；其他网站不使用此采集器。
---

# 微信文章采集

本地入口：`tools/wechat_capture.py`。脚本固定上游版本、自动使用独立 Python 环境，stdout 返回 JSON，stderr 输出采集日志；无需每次克隆或临时安装。

## 调用

对于获准访问的微信文章，先用该工具采集，不先用通用网页工具试探：

```bash
python3 tools/wechat_capture.py 'https://mp.weixin.qq.com/s/ARTICLE_ID'
```

输出默认保存至 `raw/articles/wechat/<URL哈希>/<采集版本>/`：`article.md`、`original.html`、`capture.json`、`images/`。跟随 JSON 返回的绝对路径，不猜目录名。

- `ok: true` 且 `status: complete`：回读正文；涉及图片的结论必须查看实际图片。嵌入音视频仅保留引用，不代表已转写。
- `status: partial_images`：正文已保存，但图片未全部本地化。查看 manifest 确认缺失项；说明边界，不称图文完整。
- 重复链接默认复用哈希匹配的本地原文。用户要求刷新时用 `--refresh`，产生新版本，保留旧原文。
- `verification_required` / `network_error` / `parse_error`：报告实际状态，未取得正文就不生成知识页。验证码不自动重试，不擅自切换浏览器完成验证。
- 当前会话已有平台或浏览器策略明确禁止目标访问时，不以该工具、其他浏览器或间接方式绕过。封装或安装本工具本身不代表目标文章访问已获准。

环境缺失时执行 `--setup`；检查环境执行 `--doctor`。运行环境持久保留在 `tools/.wechat-runtime/`，不要作为本次临时文件清理。只在环境检查失败时修复，不为每个链接重新安装。

## 后续交付

只要求解析、阅读或总结：根据原文在对话中交付，采集成功不自动写 Wiki。

用户要求生成 Wiki / 入库：在 AI_Learning 中继续执行 [原文入库 Workflow](../../tools/raw_wiki_ingest.md)，将返回的 `markdown_path` 作为 raw；按页型查重、写入和校验，最后列出每个实际新建或合并页面的可点击绝对路径。

用户提供本地 HTML 时可用 `--html '/绝对路径/文章.html'`，仍需提供来源 URL 作为标识；此模式完全离线，保留远程图片引用。其他参数和离线转换方法见 [工具说明](../../tools/wechat_capture.md)。
