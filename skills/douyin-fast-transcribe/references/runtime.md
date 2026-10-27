# 运行环境

按项目 README 的音频转写命令安装。`scripts/run.sh` 优先使用 `DOUYIN_TRANSCRIBE_PYTHON`，否则使用项目内 `tools/.audio-runtime/venv/bin/python`，不存在时回退到 PATH 的 `python3`。

模型默认位于 `~/.cache/ai-learning/SenseVoiceSmall`；可设置 `AI_LEARNING_SENSEVOICE_MODEL`，或者优先级更高的 `DOUYIN_SENSEVOICE_MODEL`。运行 `bash skills/douyin-fast-transcribe/scripts/run.sh doctor` 检查缺失项。

运行时只在识别阶段导入 FunASR、PyTorch、NumPy、SoundFile 和 imageio-ffmpeg。使用 CPU、4 个线程和约 28 秒的低音量切分点，连续覆盖音频。浏览器取得音轨由宿主 Agent 完成；不自带浏览器会话，也不假定有作者的登录状态。
