# 外部依赖与方法来源

- PaddleOCR：<https://github.com/PaddlePaddle/PaddleOCR>，安装器固定源码提交 b03f46425e8ff4442b268ce449e3eef758146cd4；包版本见 tools/paddleocr-requirements.lock.txt。运行时下载的第三方源码保留其上游许可证。
- 微信采集器：<https://github.com/bzd6661/wechat-article-for-ai>，固定提交 69de9e413cca3fe6b770c40a4dec204afd5b2b3c；运行依赖见 tools/wechat-requirements.lock.txt，上游源码和许可证由安装器下载。
- Camoufox：<https://github.com/daijro/camoufox>，用于浏览器采集；浏览器文件由它的安装命令下载。
- FunASR：<https://github.com/modelscope/FunASR>；SenseVoice：<https://github.com/FunAudioLLM/SenseVoice>，模型标识 iic/SenseVoiceSmall，由使用者按需下载并遵守模型附带许可。
- PyTorch、torchaudio、imageio-ffmpeg、yt-dlp 等音频依赖单独安装，未将其源码或二进制纳入仓库。
- wiki-learning 的教学参考来源记录在 skills/wiki-learning/NOTICE.md。
- 知识文章写作方法的参考文章链接记录在 skills/knowledge-article-writing/references/source-index.md；不分发文章全文或图片。方法研究参考女娲项目 <https://github.com/alchaincyf/nuwa-skill>，保留 Skill 内署名。

依赖包、模型和浏览器不因被本项目调用而变为本项目自有内容。各自许可证和版本以安装所得上游文件为准。
