#!/usr/bin/env python3
"""Persistent CLI adapter for bzd6661/wechat-article-for-ai; JSON on stdout."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "tools" / ".wechat-runtime"
REPO = RUNTIME / "repo"
PYTHON = RUNTIME / "venv" / "bin" / "python"
UPSTREAM = "https://github.com/bzd6661/wechat-article-for-ai.git"
REVISION = "69de9e413cca3fe6b770c40a4dec204afd5b2b3c"


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def revision():
    return subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
    ).strip()


def setup():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if not REPO.exists():
        subprocess.run(["git", "clone", UPSTREAM, str(REPO)], check=True, stdout=sys.stderr)
        subprocess.run(["git", "-C", str(REPO), "checkout", "--detach", REVISION],
                       check=True, stdout=sys.stderr)
    if revision() != REVISION:
        raise RuntimeError("上游版本与固定版本不一致，请检查运行目录；不会自动重置现有代码")
    if not PYTHON.exists():
        subprocess.run([sys.executable, "-m", "venv", str(RUNTIME / "venv")], check=True)
    lock = PROJECT / "tools" / "wechat-requirements.lock.txt"
    requirements = lock if lock.exists() else REPO / "requirements.txt"
    subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", str(requirements)],
                   check=True, stdout=sys.stderr)
    subprocess.run([str(PYTHON), "-m", "camoufox", "fetch"], check=True, stdout=sys.stderr)


def valid_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == "mp.weixin.qq.com"
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443)
                and (parsed.path == "/s" or parsed.path.startswith("/s/")))
    except ValueError:
        return False


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_capture(manifest_path, url):
    """Only reuse the requested URL's complete, unchanged capture."""
    data = json.loads(manifest_path.read_text())
    if data["source_url"] != url:
        raise ValueError("缓存来源链接不匹配")
    root = manifest_path.parent
    for name, field in (("article.md", "markdown_sha256"), ("original.html", "html_sha256")):
        if digest(root / name) != data[field]:
            raise ValueError(f"缓存文件已变化：{name}，请使用 --refresh 创建新版本")
    for item in data["images"]:
        if item.get("local_path") and digest(root / item["local_path"]) != item["sha256"]:
            raise ValueError("缓存图片已变化，请使用 --refresh 创建新版本")
    return data


async def capture(args):
    from bs4 import BeautifulSoup
    from wechat_to_md import (build_markdown, convert_html_to_markdown,
                             download_all_images, extract_metadata, process_content,
                             replace_image_urls, fetch_page_html, CaptchaError, ParseError)
    from wechat_to_md.utils import setup_logging

    setup_logging(args.verbose)
    output = args.output.expanduser().resolve()
    key = hashlib.sha256(args.url.encode()).hexdigest()[:20]
    article_root = output / key
    if article_root.exists() and not args.refresh and not args.html:
        manifests = sorted(article_root.glob("*/capture.json"), reverse=True)
        if manifests:
            data = verify_capture(manifests[0], args.url)
            return result(data, manifests[0].parent, cached=True)

    # --html is entirely offline: even remote images are left as references.
    html = args.html.read_bytes().decode("utf-8") if args.html else await fetch_page_html(args.url)
    soup = BeautifulSoup(html, "html.parser")
    if (soup.select_one("#js_verify, #verify_container, .verify_container")
            or (not soup.select_one("#js_content")
                and any(x in soup.get_text() for x in ("环境异常", "请完成安全验证", "操作频繁")))):
        raise CaptchaError("微信返回验证页，未取得正文")
    meta = extract_metadata(soup, html, url=args.url)
    parsed = process_content(soup)
    body = convert_html_to_markdown(parsed.content_html, parsed.code_blocks)
    if not meta.title or not body.strip():
        raise ParseError("未提取到文章标题或正文，不保存为正式原文")

    article_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".capture-", dir=article_root))
    try:
        url_map = {}
        if parsed.image_urls and not args.no_images and not args.html:
            url_map = await download_all_images(parsed.image_urls, stage / "images")
        body = replace_image_urls(body, url_map)
        (stage / "article.md").write_text(build_markdown(meta, body, parsed.media_references), encoding="utf-8")
        (stage / "original.html").write_text(html, encoding="utf-8")
        images = []
        for remote in parsed.image_urls:
            local = url_map.get(remote)
            images.append({"remote_url": remote, "local_path": local,
                           "sha256": digest(stage / local) if local else None})
        data = {
            "source_url": args.url, "title": meta.title, "author": meta.author,
            "publish_time": meta.publish_time,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "capture_mode": "offline-html" if args.html else "camoufox",
            "upstream": UPSTREAM, "revision": revision(),
            "markdown_sha256": digest(stage / "article.md"),
            "html_sha256": digest(stage / "original.html"),
            "body_char_count": len(body), "images": images,
            "images_complete": len(url_map) == len(parsed.image_urls),
        }
        (stage / "capture.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = article_root / stamp
        stage.rename(target)
        return result(data, target, cached=False)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def result(data, root, cached):
    return {"ok": True, "status": "complete" if data["images_complete"] else "partial_images",
            "cached": cached, "title": data["title"], "source_url": data["source_url"],
            "markdown_path": str(root / "article.md"), "html_path": str(root / "original.html"),
            "manifest_path": str(root / "capture.json"),
            "markdown_sha256": data["markdown_sha256"],
            "images_total": len(data["images"]),
            "images_downloaded": sum(bool(x["local_path"]) for x in data["images"])}


def doctor():
    import importlib.metadata
    from camoufox.multiversion import get_active_path
    from camoufox.pkgman import launch_path
    packages = {p: importlib.metadata.version(p) for p in
                ("camoufox", "markdownify", "beautifulsoup4", "httpx", "mcp")}
    active = get_active_path()
    browser_path = None
    if active:
        browser_path = Path(launch_path(active))
    browser_available = bool(browser_path and browser_path.is_file())
    return {"ok": browser_available and revision() == REVISION,
            "runtime": str(RUNTIME), "python": str(PYTHON), "revision": revision(),
            "packages": packages, "browser_path": str(browser_path) if browser_path else None,
            "browser_available": browser_available}


def main():
    parser = argparse.ArgumentParser(description="用固定版本的 wechat-article-for-ai 采集微信文章，返回 JSON")
    parser.add_argument("url", nargs="?", help="https://mp.weixin.qq.com/s/... 文章链接")
    parser.add_argument("--setup", action="store_true", help="安装或恢复持久运行环境")
    parser.add_argument("--doctor", action="store_true", help="检查依赖、上游版本及浏览器文件（不访问网页）")
    parser.add_argument("--output", type=Path, default=PROJECT / "raw" / "articles" / "wechat")
    parser.add_argument("--refresh", action="store_true", help="重新采集并保留旧版本")
    parser.add_argument("--no-images", action="store_true", help="仅保存正文，图片保留远程引用")
    parser.add_argument("--html", type=Path, help="离线转换已有 HTML，不访问网页或下载图片")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    try:
        if not args.setup and not args.doctor and (not args.url or not valid_url(args.url)):
            emit({"ok": False, "status": "invalid_url", "error": "仅支持 HTTPS 微信公众号文章链接 /s 或 /s/..."})
            return 2
        if args.setup:
            setup()
            os.execv(str(PYTHON), [str(PYTHON), str(Path(__file__).resolve()), "--doctor"])
        if not PYTHON.is_file() or not REPO.is_dir():
            emit({"ok": False, "status": "not_installed", "error": "先运行 python3 tools/wechat_capture.py --setup"})
            return 2
        if Path(sys.prefix).resolve() != PYTHON.parent.parent.resolve():
            os.execv(str(PYTHON), [str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
        if revision() != REVISION:
            raise RuntimeError("上游版本与固定版本不一致")
        sys.path.insert(0, str(REPO))
        with redirect_stdout(sys.stderr):
            data = doctor() if args.doctor else asyncio.run(capture(args))
        emit(data)
        return 0 if data["ok"] else 1
    except Exception as exc:
        kind = type(exc).__name__
        status = {"CaptchaError": "verification_required", "NetworkError": "network_error",
                  "ParseError": "parse_error"}.get(kind, "error")
        emit({"ok": False, "status": status, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
