# 本地音频转写

安装命令见 [README](../README.md#按需安装资料处理工具)。项目自带 `audio_backend.py`，不依赖作者的其他项目。

```bash
python tools/local_audio_transcribe.py '/绝对路径/录音.mp3' --title '我的录音'
tools/.audio-runtime/venv/bin/python tools/audio_backend.py doctor
```

入口自动复用 `tools/.audio-runtime/venv/`。FFmpeg 由 `imageio-ffmpeg` 包提供，也可使用 PATH 中的安装；音频归一化为 16 kHz 单声道，按 30 秒切段，由 SenseVoiceSmall 在本机 CPU 识别。

默认模型目录为 `~/.cache/ai-learning/SenseVoiceSmall`，可通过 `--model-path` 或环境变量 `AI_LEARNING_SENSEVOICE_MODEL` 指定其他目录。使用已有模型时不需要重新下载。

完整机器转写保存到 `raw/Audio_transcript/`，段落标注分块起点秒数；不覆盖已有稿件，不自动入库。识别失败或结果为空时不保存正式稿。机器输出未经逐句听校，不等于原始录音已核验。
