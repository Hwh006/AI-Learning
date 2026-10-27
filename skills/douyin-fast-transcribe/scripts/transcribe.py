#!/usr/bin/env python3
"""Douyin audio-to-text pipeline. Browser acquisition stays in the host."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def model_path():
    return Path(os.environ.get("DOUYIN_SENSEVOICE_MODEL", os.environ.get("AI_LEARNING_SENSEVOICE_MODEL", "~/.cache/ai-learning/SenseVoiceSmall"))).expanduser()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def video_id(text):
    match = re.search(r"https://(?:v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com)/[^\s<>\])]+", text)
    if not match:
        raise ValueError("未找到支持的抖音 HTTPS 分享链接")
    url = match.group(0)
    opener = urllib.request.build_opener(NoRedirect())
    for _ in range(4):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"v.douyin.com", "www.douyin.com", "www.iesdouyin.com"}:
            raise ValueError("分享链接跳转到不支持的站点")
        found = re.search(r"/(?:share/)?video/(\d+)(?:/|$)", parsed.path)
        if found:
            return found.group(1)
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        try:
            with opener.open(req, timeout=20) as response:
                location = response.headers.get("Location")
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise
            location = exc.headers.get("Location")
        if not location:
            break
        url = urllib.parse.urljoin(url, location)
    raise ValueError("未从分享链接解析到视频 ID")


def completed(directory, ident):
    try:
        result = read(directory / "result.json")
        if result["status"] == "completed" and result["video_id"] == ident and all((directory / name).is_file() for name in ("transcript.md", "transcript.txt", "transcript.raw.json")):
            return result
    except (OSError, ValueError, KeyError):
        pass
    return None


def begin(args):
    started = args.started_at if args.started_at is not None else time.time()
    if started > time.time() + 1 or started <= 0:
        raise ValueError("started-at 必须是过去的 Unix 秒时间戳")
    ident = video_id(args.input)
    root = Path(args.output_root).expanduser().resolve() / ident
    root.mkdir(parents=True, exist_ok=True)
    cached = None
    if (root / "latest.json").exists() and not args.refresh:
        prior = root / read(root / "latest.json")["run"]
        if completed(prior, ident):
            cached = prior
    run = root / ("run-" + uuid.uuid4().hex[:12])
    run.mkdir()
    job = {"video_id": ident, "source": "https://www.douyin.com/video/" + ident,
           "started_at": started, "cached": cached is not None,
           "output": str(cached or run), "root": str(root)}
    save(run / "job.json", job)
    emit({"job": str(run / "job.json"), "browser_url": job["source"], "cached": job["cached"]})


def paragraphs(rows):
    text = "".join(row["text"].strip() for row in rows)
    chunks, current = [], ""
    for sentence in re.findall(r"[^。！？!?]+[。！？!?]*|[。！？!?]+", text):
        current += sentence
        if len(current) >= 180:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return "\n\n".join(chunks).strip()


def transcribe(args):
    job = read(args.job)
    p = Path(job["output"])
    if completed(p, job["video_id"]):
        emit({"status": "cached", "output": str(p)})
        return
    start = time.monotonic()
    if not (model_path() / "model.pt").is_file():
        raise ValueError("本地 SenseVoiceSmall 模型缺失；设置 DOUYIN_SENSEVOICE_MODEL 后重试")
    audio = p / "audio.m4a"
    if args.audio_file:
        supplied = Path(args.audio_file).expanduser().resolve()
        if supplied != audio:
            shutil.copyfile(supplied, audio)
    else:
        url = urllib.parse.urlsplit(args.audio_url)
        if url.scheme != "https" or not url.hostname:
            raise ValueError("媒体地址必须是浏览器实际发现的 HTTPS 地址")
        request = urllib.request.Request(args.audio_url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"})
        with urllib.request.urlopen(request, timeout=60) as response, audio.open("wb") as target:
            shutil.copyfileobj(response, target)
    downloaded = time.monotonic()
    wav = p / "audio.tmp.wav"
    try:
        import imageio_ffmpeg
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True, capture_output=True, timeout=120)
        converted = time.monotonic()
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["MODELSCOPE_OFFLINE"] = "1"
        import numpy as np
        import soundfile as sf
        from funasr import AutoModel
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
        x, sr = sf.read(wav, dtype="float32")
        if not len(x) or sr != 16000 or x.ndim != 1:
            raise ValueError("转换后的音频为空或格式不正确")
        model = AutoModel(model=str(model_path()), device="cpu", ncpu=4, disable_update=True, disable_pbar=True)
        rows, offset = [], 0
        checkpoint = p / "transcript.raw.json"
        if (args.audio_file and supplied == audio and checkpoint.is_file()
                and audio.stat().st_mtime_ns <= checkpoint.stat().st_mtime_ns):
            rows = read(checkpoint)
            if (not rows or rows[0]["start"] != 0
                    or any(a["end"] != b["start"] for a, b in zip(rows, rows[1:]))
                    or any(not isinstance(r["text"], str) or r["end"] <= r["start"] for r in rows)):
                raise ValueError("已有识别检查点无效；保留原文件，不自动覆盖")
            offset = round(rows[-1]["end"] * sr)
            if not 0 < offset <= len(x):
                raise ValueError("已有识别检查点超过当前音频范围")
            emit({"event": "resumed", "seconds_done": rows[-1]["end"], "segments": len(rows)})
        while offset < len(x):
            end = min(offset + 28 * sr, len(x))
            if end < len(x):
                window = int(.12 * sr)
                candidates = range(end - 2 * sr, min(end + 2 * sr, len(x)) - window, int(.02 * sr))
                end = min(candidates, key=lambda k: float(np.mean(x[k:k + window] ** 2))) + window // 2
            result = model.generate(input=x[offset:end], cache={}, language="zh", use_itn=True, batch_size=1)
            if not result or "text" not in result[0]:
                raise ValueError("识别器未返回文本结果；不生成完整稿")
            text = re.sub(r"<\|[^>]*\|>", "", result[0]["text"])
            text = rich_transcription_postprocess(text).strip()
            rows.append({"start": offset / sr, "end": end / sr, "text": text})
            save(p / "transcript.raw.json", rows)
            offset = end
            emit({"event": "progress", "seconds_done": round(end / sr, 1), "duration_seconds": round(len(x) / sr, 1)})
        if rows[0]["start"] != 0 or rows[-1]["end"] != len(x) / sr or any(a["end"] != b["start"] for a, b in zip(rows, rows[1:])):
            raise ValueError("音频覆盖不连续；不生成完整稿")
        body = paragraphs(rows)
        if not body:
            raise ValueError("未识别到可交付文本")
        title = " ".join((args.title or "抖音视频转写稿").split())
        duration = len(x) / sr
        minutes, seconds = divmod(round(duration), 60)
        header = f"# {title}\n\n来源：{job['source']}\n\n时长：{minutes}分{seconds}秒\n\n说明：完整音轨机器转写，自动分段；未逐句听校，保留口语和可能的识别误差。\n\n"
        (p / "transcript.txt").write_text(body + "\n", encoding="utf-8")
        (p / "transcript.md").write_text(header + body + "\n", encoding="utf-8")
        now = time.monotonic()
        timing = {"download_or_copy_seconds": round(downloaded - start, 2), "convert_seconds": round(converted - downloaded, 2),
                  "recognize_and_write_seconds": round(now - converted, 2), "pipeline_seconds": round(now - start, 2)}
        result = {"status": "completed", "video_id": job["video_id"], "duration_seconds": duration, "segments": len(rows), "timing": timing}
        save(p / "result.json", result)
        save(Path(job["root"]) / "latest.json", {"run": p.name})
        emit({**result, "markdown": str(p / "transcript.md"), "text": str(p / "transcript.txt")})
    finally:
        wav.unlink(missing_ok=True)


def finish(args):
    job = read(args.job)
    p = Path(job["output"])
    result = completed(p, job["video_id"])
    if not result:
        raise ValueError("未找到已完成的完整转写结果")
    delivery = {"status": "ready", "cached": job["cached"], "duration_seconds": result["duration_seconds"],
                "execution_to_ready_seconds": round(time.time() - job["started_at"], 2),
                "pipeline_seconds": None if job["cached"] else result["timing"]["pipeline_seconds"],
                "markdown": str(p / "transcript.md"), "text": str(p / "transcript.txt"),
                "timing_scope": "first_tool_execution_to_ready; excludes final reply rendering and pre-tool waiting"}
    save(Path(args.job).parent / "delivery.json", delivery)
    emit(delivery)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    first = sub.add_parser("begin")
    first.add_argument("--input", required=True)
    first.add_argument("--output-root", required=True)
    first.add_argument("--started-at", type=float)
    first.add_argument("--refresh", action="store_true")
    worker = sub.add_parser("transcribe")
    worker.add_argument("--job", required=True)
    inputs = worker.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--audio-url")
    inputs.add_argument("--audio-file")
    worker.add_argument("--title")
    final = sub.add_parser("finish")
    final.add_argument("--job", required=True)
    sub.add_parser("doctor")
    args = parser.parse_args()
    if args.command == "doctor":
        modules = {name: importlib.util.find_spec(name) is not None for name in ("funasr", "torch", "numpy", "soundfile", "imageio_ffmpeg")}
        ready = all(modules.values()) and (model_path() / "model.pt").is_file()
        emit({"ready": ready, "python": sys.executable, "modules": modules, "model": str(model_path()), "model_present": (model_path() / "model.pt").is_file()})
        return 0 if ready else 1
    {"begin": begin, "transcribe": transcribe, "finish": finish}[args.command](args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as exc:
        emit({"status": "failed", "error": "HTTP_ERROR", "http_status": exc.code, "hint": "403 时从当前页面刷新一次媒体地址"})
        sys.exit(1)
    except (ValueError, ModuleNotFoundError) as exc:
        emit({"status": "failed", "error": type(exc).__name__, "message": str(exc)})
        sys.exit(1)
    except Exception as exc:
        emit({"status": "failed", "error": type(exc).__name__, "hint": "检查网络、音频文件或本地依赖；未生成完整转写"})
        sys.exit(1)
