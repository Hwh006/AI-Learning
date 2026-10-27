# AI-Learning：Agent 知识与学习工作空间

把文章、书籍、PDF 和音视频整理为可追溯的知识库，再用于问答、学习、复习和知识文章写作。项目提供工作规则、Skills 和本地工具，需要配合能读写文件、运行命令的 Agent 客户端使用。

## 需要安装什么

- Git、[uv](https://docs.astral.sh/uv/getting-started/installation/)；下面命令通过 uv 安装 Python 3.12 和项目依赖。
- 一个支持本地文件与终端的 Agent 客户端，并完成该客户端自己的模型配置。
- 基础检索、入库校验、记忆和学习状态管理只需 `tiktoken`，不下载大模型；采集、OCR、转写按需安装。
- 以下命令使用 macOS / Linux 的 shell。当前 OCR 默认配置按 macOS CPU 路径整理；Windows 安装未验证。

## 快速安装

尚未安装 uv 时，先运行官方安装命令：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

安装基础工作空间：

```bash
git clone https://github.com/Hwh006/AI-Learning.git
cd AI-Learning
uv venv --python 3.12 .workflow-venv
uv pip install --python .workflow-venv/bin/python -r requirements-ingest.txt --index-url https://pypi.org/simple
source .workflow-venv/bin/activate
python tools/memory_catalog.py --check
python skills/wiki-source-page/scripts/verify_source_page.py --page examples/wiki-demo-source.md
```

在 Agent 客户端中打开这个文件夹，让它读取 `AGENTS.md`。Skills 的自动发现位置因客户端而异，也可以在对话中直接指定 `skills/<名称>/SKILL.md`。

把自己的 Markdown 放入 `raw/`，例如要求：“按 raw-wiki-ingest Skill 将 raw/我的文章.md 入库，完成校验并同步知识地图。”也可以要求：“带我学习这篇文章，保存学习进度。”

## 按需安装资料处理工具

微信文章采集：安装固定版本的采集器、独立 Python 环境及 Camoufox 浏览器。

```bash
python tools/wechat_capture.py --setup
python tools/wechat_capture.py 'https://mp.weixin.qq.com/s/替换为文章ID'
```

PDF / 图片 OCR：安装 PaddleOCR 独立环境；识别模型在第一次解析时下载，之后本地复用。

```bash
python tools/paddleocr_parse.py --setup
python tools/paddleocr_parse.py '/绝对路径/文档.pdf'
```

音频转写：安装 FunASR、PyTorch、FFmpeg 配套包及 SenseVoiceSmall 模型。模型保存在用户缓存，不需要另一个私有项目，也不需要云端转写 API Key。

```bash
uv venv --python 3.12 tools/.audio-runtime/venv
uv pip install --python tools/.audio-runtime/venv/bin/python -r requirements-transcribe.txt --index-url https://pypi.org/simple
tools/.audio-runtime/venv/bin/python tools/audio_backend.py setup
python tools/local_audio_transcribe.py '/绝对路径/录音.mp3' --title '我的录音'
```

B 站视频复用上述转写环境：`python tools/bilibili_transcribe.py 'https://www.bilibili.com/video/替换为BV号'`。抖音转写还需由 Agent 的浏览器能力取得实际音轨，使用方式见 `skills/douyin-fast-transcribe/SKILL.md`。

工具会下载各自的依赖；完整环境和模型不随 Git 仓库分发。OCR 输出、机器转写不等于已校对正文；后续按长文档或入库 Skill 处理。

## 项目里有什么，能帮你做什么

| 内容 | 用途 |
|---|---|
| `AGENTS.md`、`intents/` | 让 Agent 按任务选择检索、学习、写作或入库流程 |
| `skills/` | 文章采集、长文档提取、图文校对、音视频转写、Wiki 生成、地图维护、学习与知识文章写作 |
| `tools/` | 执行检索、原文分块、引文定位、来源/链接校验、地图覆盖检查及状态保存 |
| `memory/` | 七类个人记忆的管理机制；按元数据查找，确认后保存使用者自己的要求与背景 |
| `raw/`、`wiki/`、`learning/` | 保存使用者自己的原文、知识页、学习手册、进度与讨论 |
| `examples/`、`tests/` | 虚构示例与核心流程的回归检查 |

仓库初始不含作者的课程原文、知识库、个人记忆或学习记录。发布范围见 [PUBLIC_EXPORT.md](PUBLIC_EXPORT.md)，外部依赖来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
