#!/usr/bin/env python3
"""Local SenseVoice audio backend shared by AI-Learning transcription tools."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

CONTRACT = "ai-learning.audio@1"
MODEL_ID = "iic/SenseVoiceSmall"
BV_URL = re.compile(r"^https://(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})(?:[/?#].*)?$")
B23_URL = re.compile(r"^https://b23\.tv/[0-9A-Za-z]+(?:[/?#].*)?$")
CONTROL_TOKEN = re.compile(r"<\|[^|]+\|>")


class CompanionFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def model_path(value: str | None) -> Path:
    configured = value or os.environ.get("AI_LEARNING_SENSEVOICE_MODEL")
    return Path(configured).expanduser() if configured else Path.home() / ".cache" / "ai-learning" / "SenseVoiceSmall"


def public_url(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    playlist_keys = {"p", "page", "playlist", "list"}
    if playlist_keys.intersection(parse_qs(parsed.query)):
        raise CompanionFailure("PLAYLIST_NOT_SUPPORTED")
    if BV_URL.fullmatch(candidate) or B23_URL.fullmatch(candidate):
        return candidate
    raise CompanionFailure("SOURCE_NOT_PUBLIC")


def emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def health(path: Path) -> dict[str, object]:
    return {
        "contract": CONTRACT,
        "python": sys.version_info[:2] >= (3, 11),
        "ytDlp": importlib.util.find_spec("yt_dlp") is not None or shutil.which("yt-dlp") is not None,
        "ffmpeg": importlib.util.find_spec("imageio_ffmpeg") is not None or shutil.which("ffmpeg") is not None,
        "funasr": importlib.util.find_spec("funasr") is not None,
        "model": path.is_dir() and (path / "model.pt").is_file(),
        "modelId": MODEL_ID,
    }


def setup_model(path: Path) -> None:
    if (path / "model.pt").is_file():
        emit({"ok": True, "contract": CONTRACT, "modelId": MODEL_ID, "cached": True})
        return
    try:
        from modelscope import snapshot_download
    except ImportError as exc:
        raise CompanionFailure("MODELSCOPE_UNAVAILABLE") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="sensevoice-download-", dir=path.parent))
    try:
        snapshot_download(MODEL_ID, local_dir=str(temporary))
        if not (temporary / "model.pt").is_file():
            raise CompanionFailure("MODEL_DOWNLOAD_INCOMPLETE")
        if path.exists():
            raise CompanionFailure("MODEL_PATH_NOT_EMPTY")
        temporary.replace(path)
    except CompanionFailure:
        raise
    except Exception as exc:
        raise CompanionFailure("MODEL_DOWNLOAD_FAILED") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    emit({"ok": True, "contract": CONTRACT, "modelId": MODEL_ID, "cached": False})


async def run_bounded(argv: list[str], timeout: float) -> tuple[bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise CompanionFailure("PROCESS_TIMEOUT") from exc
    if len(stdout) > 1_048_576 or len(stderr) > 1_048_576:
        raise CompanionFailure("PROCESS_OUTPUT_LIMIT")
    if process.returncode != 0:
        raise CompanionFailure("AUDIO_UNAVAILABLE")
    return stdout, stderr


async def download(url: str, directory: Path) -> Path:
    output = str(directory / "public-audio.%(ext)s")
    executable = shutil.which("yt-dlp")
    argv = ([executable] if executable else [sys.executable, "-m", "yt_dlp"]) + [
        "--no-playlist", "--no-warnings", "--no-progress", "-f", "bestaudio/best", "-o", output, url
    ]
    await run_bounded(argv, 120)
    files = [item for item in directory.iterdir() if item.is_file()]
    if len(files) != 1 or files[0].stat().st_size == 0:
        raise CompanionFailure("AUDIO_UNAVAILABLE")
    return files[0]


def ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except ImportError:
        executable = shutil.which("ffmpeg")
        if executable is None:
            raise CompanionFailure("FFMPEG_UNAVAILABLE")
        return executable


def normalize(audio: Path, wav_path: Path) -> None:
    try:
        result = subprocess.run(
            [ffmpeg_executable(), "-y", "-i", str(audio), "-ac", "1", "-ar", "16000", str(wav_path)],
            shell=False, capture_output=True, timeout=120, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CompanionFailure("PROCESS_TIMEOUT") from exc
    if len(result.stdout) > 1_048_576 or len(result.stderr) > 1_048_576:
        raise CompanionFailure("PROCESS_OUTPUT_LIMIT")
    if result.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size == 0:
        raise CompanionFailure("AUDIO_UNAVAILABLE")


def split_wav(source: Path, directory: Path) -> list[Path]:
    segments: list[Path] = []
    with wave.open(str(source), "rb") as reader:
        rate = reader.getframerate()
        frames_per_segment = rate * 30
        params = reader.getparams()
        index = 0
        while True:
            frames = reader.readframes(frames_per_segment)
            if not frames:
                break
            target = directory / f"segment-{index:05d}.wav"
            with wave.open(str(target), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(frames)
            segments.append(target)
            index += 1
    return segments


def load_model(path: Path) -> Any:
    if not (path / "model.pt").is_file():
        raise CompanionFailure("MODEL_UNAVAILABLE")
    try:
        from funasr import AutoModel

        return AutoModel(model=str(path), trust_remote_code=False, device="cpu", disable_update=True)
    except Exception as exc:
        raise CompanionFailure("MODEL_LOAD_FAILED") from exc


def infer(model: Any, segment: Path) -> str:
    try:
        result = model.generate(input=str(segment), cache={}, language="zh", use_itn=True, batch_size_s=60)
        text = "\n\n".join(str(item.get("text", "")).strip() for item in result).strip()
        return CONTROL_TOKEN.sub("", text).strip()
    except Exception as exc:
        raise CompanionFailure("TRANSCRIPTION_FAILED") from exc


async def transcribe(url: str, path: Path) -> dict[str, object]:
    candidate = public_url(url)
    state = health(path)
    if not all(state[key] for key in ("python", "ytDlp", "ffmpeg", "funasr", "model")):
        raise CompanionFailure("LOCAL_DEPENDENCY_UNAVAILABLE")
    with tempfile.TemporaryDirectory(prefix="ai-learning-bili-") as raw_directory:
        directory = Path(raw_directory)
        audio = await download(candidate, directory)
        wav_path = directory / "sensevoice-input.wav"
        await asyncio.to_thread(normalize, audio, wav_path)
        segments = await asyncio.to_thread(split_wav, wav_path, directory)
        model = await asyncio.to_thread(load_model, path)
        parts: list[str] = []
        for batch_start in range(0, len(segments), 10):
            for segment in segments[batch_start:batch_start + 10]:
                parts.append(await asyncio.wait_for(asyncio.to_thread(infer, model, segment), timeout=30))
        text = "\n\n".join(part for part in parts if part).strip()
        if not text:
            raise CompanionFailure("TRANSCRIPTION_EMPTY")
        return {"ok": True, "contract": CONTRACT, "adapter": "bilibili_public@1", "text": text, "segmentCount": len(segments)}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--model-path")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("setup")
    transcribe_command = commands.add_parser("transcribe")
    transcribe_command.add_argument("url")
    return root


def main() -> None:
    args = parser().parse_args()
    path = model_path(args.model_path)
    try:
        if args.command == "doctor":
            state = health(path)
            ready = all(state[key] for key in ("python", "ffmpeg", "funasr", "model"))
            emit({"ok": ready, **state})
            if not ready:
                raise SystemExit(1)
        elif args.command == "setup":
            setup_model(path)
        else:
            emit(asyncio.run(transcribe(args.url, path)))
    except CompanionFailure as exc:
        emit({"ok": False, "contract": CONTRACT, "error": {"code": exc.code}})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
