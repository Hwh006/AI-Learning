#!/usr/bin/env python3
"""Run the existing Bilibili transcriber and retain complete text in raw."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT / "raw" / "Audio_transcript"
BACKEND = PROJECT / "tools" / "audio_backend.py"


def save_transcript(result, url, title):
    text = result.get("text")
    if result.get("ok") is not True or not isinstance(text, str) or not text.strip():
        raise ValueError("转写未完整成功，不保存为正式文字稿")
    if result.get("partial"):
        raise ValueError("部分转写不能保存为完整文字稿")
    title = " ".join(title.split()) or "B站视频转写"
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title)[:100]
    now = datetime.now().astimezone()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{now:%Y%m%d-%H%M%S-%f}-{filename}.md"
    body = (
        f"# {title}\n\n"
        f"- 来源：{url}\n"
        f"- 转写时间：{now.isoformat(timespec='seconds')}\n"
        "- 稿件类型：完整机器转写，未经逐句听校，可能包含识别错误。\n\n"
        "## 转写正文\n\n" + text.strip() + "\n"
    )
    with path.open("x", encoding="utf-8") as stream:
        stream.write(body)
    return path


AUDIO_PYTHON = PROJECT / "tools" / ".audio-runtime" / "venv" / "bin" / "python"


def use_audio_environment():
    if AUDIO_PYTHON.is_file() and Path(sys.prefix).resolve() != AUDIO_PYTHON.parent.parent.resolve():
        os.execv(str(AUDIO_PYTHON), [str(AUDIO_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


def main():
    use_audio_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="公开 B 站视频或 b23.tv 分享链接")
    parser.add_argument("--title", default="B站视频转写")
    args = parser.parse_args()
    process = subprocess.run(
        [sys.executable, str(BACKEND),
         "transcribe", args.url],
        cwd=PROJECT, capture_output=True, text=True,
    )
    # Model libraries may print progress before the companion's final JSON line.
    lines = process.stdout.strip().splitlines()
    result = json.loads(lines[-1]) if lines else {}
    if process.returncode != 0:
        raise RuntimeError(f"转写失败，未保存文字稿：{result.get('error', '见工具输出')}")
    path = save_transcript(result, args.url, args.title)
    print(json.dumps({"ok": True, "transcript_path": str(path)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
