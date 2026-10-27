# B 站视频转写

先按 [README](../README.md#按需安装资料处理工具) 安装音频环境与模型，然后执行：

```bash
python tools/bilibili_transcribe.py 'https://www.bilibili.com/video/替换为BV号' --title '视频标题'
```

工具调用项目自带 `audio_backend.py`，自动复用 `tools/.audio-runtime/venv/`，通过 yt-dlp 获取单个公开视频的音频，再使用本机 FFmpeg 与 SenseVoiceSmall 识别。不依赖作者的其他仓库；不处理合集和分P参数。

完整成功且正文非空时才保存到 `raw/Audio_transcript/`。网络、下载或识别失败时非零退出；不把部分结果当成完整稿。机器稿未经逐句听校，获取成功不等于已入库。
