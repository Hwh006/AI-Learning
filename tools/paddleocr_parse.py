#!/usr/bin/env python3
"""Local PP-StructureV3 PDF/image parser; Markdown, crops and provenance."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "tools" / ".paddleocr-runtime"
PYTHON = RUNTIME / "venv" / "bin" / "python"
UPSTREAM = "https://github.com/PaddlePaddle/PaddleOCR.git"
REVISION = "b03f46425e8ff4442b268ce449e3eef758146cd4"
MODELS = {
    "layout_detection_model_name": "PP-DocLayout_plus-L",
    "text_detection_model_name": "PP-OCRv5_mobile_det",
    "text_recognition_model_name": "PP-OCRv5_mobile_rec",
}


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ocr_markdown(text, role, **metadata):
    """Label machine output so it cannot be mistaken for an ingestible section."""
    fields = {"raw_role": role, "ingest_allowed": False, **metadata}
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}"
                       for key, value in fields.items())
    return f"---\n{header}\n---\n\n{text}"


def revision():
    return subprocess.check_output(
        ["git", "-C", str(RUNTIME / "repo"), "rev-parse", "HEAD"], text=True
    ).strip()


def setup():
    if not shutil.which("uv"):
        raise RuntimeError("安装需要 uv；请先安装 uv 后重试")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    repo = RUNTIME / "repo"
    if not repo.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "v3.7.0",
                        UPSTREAM, str(repo)], check=True, stdout=sys.stderr)
    if revision() != REVISION:
        raise RuntimeError("上游源码版本不匹配；不会自动重置现有源码")
    if not PYTHON.exists():
        subprocess.run(["uv", "venv", "--python", "3.12", str(RUNTIME / "venv")],
                       check=True, stdout=sys.stderr)
    subprocess.run(["uv", "pip", "sync", "--python", str(PYTHON),
                    str(PROJECT / "tools" / "paddleocr-requirements.lock.txt")],
                   check=True, stdout=sys.stderr)
    subprocess.run([str(PYTHON), str(Path(__file__).resolve()), "--doctor"], check=True)


def pipeline():
    from paddleocr import PPStructureV3

    # Apple Silicon CPU profile: keep layout and OCR, omit unrelated heavy models.
    return PPStructureV3(
        **MODELS, device="cpu", enable_mkldnn=False, cpu_threads=4,
        use_doc_orientation_classify=False, use_doc_unwarping=False,
        use_textline_orientation=False, use_table_recognition=False,
        use_formula_recognition=False, use_chart_recognition=False,
        use_region_detection=False, use_seal_recognition=False,
        markdown_ignore_labels=[],
    )


def safe_child(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"无效的图片路径：{name}")
    return path


def export_page(result, stage, number):
    """Separate page directories prevent identical crop names from colliding."""
    stage = stage.resolve()
    relative = Path("pages") / f"page-{number:04d}"
    page_dir = stage / relative
    page_dir.mkdir(parents=True)
    md = result.markdown
    text = md["markdown_texts"]
    combined = text
    images = []
    for name, image in md.get("markdown_images", {}).items():
        target = safe_child(page_dir, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        local = target.relative_to(stage).as_posix()
        combined = combined.replace(name, local)
        images.append({"path": local, "sha256": digest(target),
                       "width": image.width, "height": image.height})
    (page_dir / "page.md").write_text(ocr_markdown(
        text, "ocr_page", source_page=number, extraction_manifest="../../manifest.json"),
        encoding="utf-8")
    result.save_to_json(str(page_dir / "layout.json"))
    return combined, {"page": number, "markdown": str(relative / "page.md"),
                      "layout": str(relative / "layout.json"), "images": images}


def parse(args):
    import numpy as np
    import pypdfium2 as pdfium
    from PIL import Image

    source = args.input.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"输入文件不存在：{source}")
    is_pdf = source.suffix.lower() == ".pdf"
    if not is_pdf and source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        raise ValueError("支持 PDF、PNG、JPG、JPEG、WebP、BMP")
    pdf = pdfium.PdfDocument(source) if is_pdf else None
    total = len(pdf) if pdf is not None else 1
    selected = sorted(set(args.pages or range(1, total + 1)))
    if not selected or any(n < 1 or n > total for n in selected):
        if pdf is not None:
            pdf.close()
        raise ValueError(f"页码超出范围，文件共有 {total} 页；页码从 1 开始")
    sha = digest(source)
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = output / f"{source.stem}-{sha[:12]}-{stamp}"
    stage = Path(tempfile.mkdtemp(prefix=".paddleocr-", dir=output))
    try:
        engine = pipeline()
        texts, pages = [], []
        for number in selected:
            print(f"正在解析第 {number}/{total} 页", file=sys.stderr)
            if pdf is not None:
                page = pdf[number - 1]
                bitmap = page.render(scale=2.0)
                image = bitmap.to_pil().convert("RGB")
                bitmap.close()
                page.close()
            else:
                with Image.open(source) as original:
                    image = original.convert("RGB")
            # PaddleX NumPy inputs use OpenCV's BGR channel order.
            results = list(engine.predict(np.asarray(image)[:, :, ::-1].copy()))
            if len(results) != 1:
                raise RuntimeError(f"第 {number} 页解析返回 {len(results)} 个结果，预期为 1")
            text, info = export_page(results[0], stage, number)
            image.close()
            texts.append(f"<!-- Source page: {number} -->\n\n{text}")
            pages.append(info)
        (stage / "document.md").write_text(ocr_markdown(
            "\n\n---\n\n".join(texts), "ocr_output", source_file=str(source),
            source_sha256=sha, extraction_manifest="manifest.json",
            structure_review="pending", ocr_review="unreviewed"), encoding="utf-8")
        manifest = {
            "status": "complete", "source_path": str(source), "source_sha256": sha,
            "created_at": stamp, "total_source_pages": total, "processed_pages": selected,
            "upstream": UPSTREAM, "upstream_revision": revision(),
            "versions": {x: version(x) for x in ("paddleocr", "paddlepaddle", "paddlex")},
            "models": MODELS, "device": "cpu", "pdf_render_dpi": 144 if is_pdf else None,
            "table_recognition": False, "formula_recognition": False,
            "image_export": "page_region_crops", "human_verified": False,
            "raw_role": "ocr_output", "ingest_allowed": False,
            "next_skill": "skills/long-document-extraction/SKILL.md",
            "markdown": "document.md", "pages": pages,
            "image_count": sum(len(p["images"]) for p in pages),
        }
        if digest(source) != sha:
            raise RuntimeError("解析期间输入文件发生变化，未保存结果")
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        stage.rename(destination)
        return {"status": "complete", "output": str(destination),
                "markdown": str(destination / "document.md"),
                "manifest": str(destination / "manifest.json"),
                "processed_pages": selected, "image_count": manifest["image_count"],
                "ingest_allowed": False, "next_skill": "skills/long-document-extraction/SKILL.md"}
    finally:
        if pdf is not None:
            pdf.close()
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description="PaddleOCR PDF/图片解析，导出 Markdown 与独立图片")
    parser.add_argument("input", nargs="?", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--setup", action="store_true", help="安装/恢复固定版本运行环境")
    mode.add_argument("--doctor", action="store_true", help="检查源码、依赖和已下载模型")
    parser.add_argument("--pages", nargs="+", type=int, help="只解析指定页码（从1开始）")
    parser.add_argument("--output", type=Path, default=PROJECT / "outputs" / "paddleocr")
    args = parser.parse_args()
    try:
        if args.setup:
            setup()
            return
        if not PYTHON.exists():
            raise RuntimeError("运行环境未安装；先运行 python3 tools/paddleocr_parse.py --setup")
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(RUNTIME / "cache"))
        os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        if Path(sys.prefix).resolve() != (RUNTIME / "venv").resolve():
            os.execv(str(PYTHON), [str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
        if revision() != REVISION:
            raise RuntimeError("上游源码版本不匹配，请检查运行目录")
        if args.doctor:
            with redirect_stdout(sys.stderr):
                import paddle
                from paddleocr import PPStructureV3
                assert (paddle.to_tensor([2, 3]) + 1).numpy().tolist() == [3, 4]
            emit({"status": "ready", "upstream_revision": revision(),
                  "versions": {x: version(x) for x in ("paddleocr", "paddlepaddle", "paddlex")},
                  "models_cached": {name: (Path(os.environ["PADDLE_PDX_CACHE_HOME"]) /
                      "official_models" / name / "inference.json").is_file()
                      for name in MODELS.values()},
                  "note": "环境检查通过不等于某个文件的识别质量通过"})
        elif args.input is None:
            parser.error("请指定 PDF 或图片路径，或使用 --setup / --doctor")
        else:
            with redirect_stdout(sys.stderr):
                data = parse(args)
            emit(data)
    except Exception as error:
        emit({"status": "failed", "error": str(error)})
        sys.exit(1)


if __name__ == "__main__":
    main()
