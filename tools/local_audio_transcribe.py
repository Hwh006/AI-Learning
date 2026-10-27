#!/usr/bin/env python3
"""Transcribe a local audio file and keep the complete text in raw/Audio_transcript/."""

import argparse
import importlib.util
import json
import os
from datetime import datetime
from pathlib import Path
import re
import sys
import tempfile
import time
import wave

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT / "raw" / "Audio_transcript"
COMPANION = PROJECT / "tools" / "audio_backend.py"
SEGMENT_SECONDS = 30


def companion_module():
    if not COMPANION.is_file():
        raise RuntimeError(f"识别模块不存在：{COMPANION}")
    spec = importlib.util.spec_from_file_location("knowledge_wiki_bili_companion", COMPANION)
    if spec is None or spec.loader is None:
        raise RuntimeError("识别模块无法加载")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def human_duration(seconds):
    total = int(round(seconds))
    return f"{total // 60} 分 {total % 60} 秒（{total} 秒）"


def transcribe(audio, mode):
    companion = companion_module()
    model_path = companion.model_path(mode)
    state = companion.health(model_path)
    missing = [key for key in ("python", "ffmpeg", "funasr", "model") if not state.get(key)]
    if missing:
        raise RuntimeError("本地识别依赖不可用：" + "、".join(missing))
    with tempfile.TemporaryDirectory(prefix="local-audio-transcribe-") as raw_directory:
        directory = Path(raw_directory)
        wav_path = directory / "sensevoice-input.wav"
        mark = time.monotonic()
        companion.normalize(audio, wav_path)
        with wave.open(str(wav_path), "rb") as reader:
            duration = reader.getnframes() / float(reader.getframerate())
        segments = companion.split_wav(wav_path, directory)
        print(f"阶段耗时 归一化与切段 {time.monotonic() - mark:.0f}s", file=sys.stderr, flush=True)
        mark = time.monotonic()
        model = companion.load_model(model_path)
        print(f"阶段耗时 导入 funasr 与加载模型 {time.monotonic() - mark:.0f}s", file=sys.stderr, flush=True)
        mark = time.monotonic()
        parts = []
        for index, segment in enumerate(segments):
            text = companion.infer(model, segment)
            if text:
                parts.append(f"[{index * SEGMENT_SECONDS}] {text}")
            print(f"进度 {index + 1}/{len(segments)}", file=sys.stderr, flush=True)
        print(f"阶段耗时 识别 {len(segments)} 段 {time.monotonic() - mark:.0f}s", file=sys.stderr, flush=True)
    if not parts:
        raise RuntimeError("转写结果为空，不保存为文字稿")
    return parts, duration, len(segments)


def save_transcript(audio, title, parts, duration, segment_count):
    now = datetime.now().astimezone()
    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", "-".join(title.split()))[:100]
    filename = safe_title or "本地音频转写"
    path = OUTPUT_DIR / f"{now:%Y%m%d-%H%M%S}-{filename}.md"
    body = "\n".join([
        f"# {title.strip()}",
        "",
        f"- 来源：本地音频 `{audio.name}`",
        f"- 原始路径：{audio}",
        f"- 文件大小：{audio.stat().st_size / 1024 / 1024:.1f} MB",
        f"- 音频时长：{human_duration(duration)}",
        f"- 转写时间：{now.isoformat(timespec='seconds')}",
        "- 稿件类型：完整机器转写（SenseVoiceSmall），未经逐句听校，可能包含识别错误；保留错词、断句与英文术语噪声。",
        f"- 时间标记：每段标注分块起点（秒），共 {segment_count} 段，每段 {SEGMENT_SECONDS} 秒。",
        "",
        "## 转写正文",
        "",
        "\n\n".join(parts),
        "",
    ])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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
    parser.add_argument("audio", help="本地音频文件路径")
    parser.add_argument("--title", help="文字稿标题，默认取音频文件名")
    parser.add_argument("--model-path", help="SenseVoiceSmall 模型目录")
    args = parser.parse_args()
    audio = Path(args.audio).expanduser().resolve()
    if not audio.is_file():
        raise RuntimeError(f"音频文件不存在：{audio}")
    title = args.title or audio.stem
    parts, duration, segment_count = transcribe(audio, args.model_path)
    path = save_transcript(audio, title, parts, duration, segment_count)
    print(json.dumps({
        "ok": True,
        "transcript_path": str(path),
        "segments": segment_count,
        "durationSeconds": round(duration),
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
