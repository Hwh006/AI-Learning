---
name: douyin-fast-transcribe
description: 将抖音分享链接、视频页面或已获取的本地音频快速转成完整中文文字稿。适用于抖音视频转文字、获取视频文稿、文章转写稿等请求；复用浏览器当前会话，只下载音轨，用固定脚本完成识别、自动分段和交付，记录完整执行时间，避免反复找工具和额外精校。
agent_created: true
---

# 抖音快速转写

执行“解析链接 → 浏览器获取已加载音轨 → 一次运行脚本 → 立即交付”。默认产出完整机器转写稿，保留口语内容。将“文章转写稿”理解为自动分段的完整文字稿；仅在明确要求润色、精校、摘要或逐句字幕时追加对应工作。

## 执行流程

1. 在首次工具执行时记录 Unix 时间。调用 `scripts/run.sh begin --input '完整分享文本或链接' --output-root '绝对输出目录' --started-at '记录的时间'`。默认输出到当前项目的 `outputs/transcripts/douyin-transcripts`。采用结构化参数或可靠的 shell 转义，不直接拼接不可信分享文本。
   - 读取返回的 `job`、`browser_url` 和 `cached`。缓存命中时直接进入第 4 步。
   - 不重复搜索 Skill、扫描其他仓库或尝试已经失败的匿名 yt-dlp 接口。
2. 使用已连接、已登录的 Chrome 打开 `browser_url`。按浏览器工具当前文档读取已加载资源，只取与目标视频匹配的音轨。具体路径见 [浏览器获取音轨](references/browser-audio.md)。复用当前句柄，不导出 Cookie、不读取浏览器密码库。
   - 找到 `media-audio-*` 后立即继续，不截图、不读取评论、不核对作者、不下载视频画面。
   - 无独立音轨时允许使用页面当前视频的可下载媒体地址；说明该次需下载音视频容器，由 FFmpeg 提取音频。
3. 一次运行 `scripts/run.sh transcribe --job 'job.json 绝对路径' --audio-url '页面实际发现的媒体 URL' --title '视频标题'`。已有本地音频时改用 `--audio-file '绝对路径'`。
   - 脚本完成下载、16 kHz 单声道转换、SenseVoice 识别、连续覆盖校验、自动分段和 Markdown/TXT 输出。
   - 默认保留原始识别 JSON 和轻量音频缓存，删除临时 WAV。不逐段让语言模型改写，不自动套用 humanizer、面试答题或课程检索 Skill。纯转写不等同于回答视频中的知识问题。
   - 使用现有任务等待机制等待进程，不每秒轮询。超过约 60 秒时给出一次实际进度，不承诺固定完成时间。
4. 成功后直接运行 `scripts/run.sh finish --job 'job.json 绝对路径'`，读取返回的文件路径和计时。立即交付 Markdown/TXT 链接；可在同一工具批次打开 Markdown。不再读完整正文做一轮润色或额外审查。

## 输出与计时

- 默认回复：“已完成约 X 分钟视频的完整机器转写：[文字稿](绝对路径) · [TXT](绝对路径)。本次执行至准备交付共 Y 秒，其中下载、转换和识别流水线 Z 秒；未逐句听校。”
- 将 `execution_to_ready_seconds` 作为主时间，`pipeline_seconds` 作为分项。缓存命中时使用本次执行时间并注明缓存复用，不把历史流水线耗时当成本次耗时。
- 明确计时从首次工具执行开始，到准备交付为止；不声称包含消息排队、首次工具前等待或最终 UI 渲染。缺少开始时间时不得编造总耗时。
- 保留原始表述，不将标题或简介当转写内容，不添加原视频没有的建议、案例或猜测性专有名词修正。快速稿可能包含同音字和英文识别错误。
- 只有完整音频识别结束、覆盖连续且文件存在时才报告完成；失败或部分转写不当作完整稿交付。

## 故障与环境

- 首次部署或依赖报错时才运行 `scripts/run.sh doctor`，正常任务不反复检查环境。配置方式见 [运行环境](references/runtime.md)。
- 媒体返回 403 时，从当前页面刷新资源地址一次，不循环试接口、安装多个下载器或调取第三方解析服务。
- 页面需要登录或验证码时，按当前浏览器工具规则处理必要的用户操作；保留交接页面并说明实际阻塞，不绕过访问限制。
- 连续两次获取音轨失败时停止探索，说明失败环节并请求本地视频或音频。不把耗时归咎于尚未运行的模型。
- 不自动安装模型、不使用收费云 API、不上传音频到外部转写服务。改变处理方式前说明具体变更。

## 命令示例

```bash
bash /绝对路径/douyin-fast-transcribe/scripts/run.sh begin \
  --input 'https://v.douyin.com/示例/' --output-root /绝对路径/outputs/transcripts/douyin-transcripts
bash /绝对路径/douyin-fast-transcribe/scripts/run.sh transcribe \
  --job /上一步返回/job.json --audio-file /绝对路径/audio.m4a --title '视频标题'
bash /绝对路径/douyin-fast-transcribe/scripts/run.sh finish --job /上一步返回/job.json
```
