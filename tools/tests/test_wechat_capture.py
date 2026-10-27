"""Offline behavioral checks: real upstream parsing, no live page access."""

import asyncio
from argparse import Namespace
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "wechat_capture.py"
spec = importlib.util.spec_from_file_location("wechat_capture", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
sys.path.insert(0, str(tool.REPO))
import wechat_to_md

URL = "https://mp.weixin.qq.com/s/offline-fixture"
HTML = '<html><h1 id="activity-name">离线测试文章</h1><span id="js_name">测试作者</span><div id="js_content"><h2>机制说明</h2><p>文章的正文内容，不是验证页面。</p><div class="code-snippet__fix"><pre data-lang="python"><code>print("hello")</code></pre></div></div></html>'


class CaptureChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.html = self.root / "input.html"
        self.html.write_text(HTML)
        self.args = Namespace(url=URL, output=self.root / "out", html=self.html,
                              refresh=False, no_images=False, verbose=False)

    def tearDown(self):
        self.temp.cleanup()

    def run_capture(self):
        return asyncio.run(tool.capture(self.args))

    def test_real_parser_and_offline_html_bytes(self):
        self.html.write_bytes(HTML.replace(">", ">\r\n").encode())
        with patch.object(wechat_to_md, "fetch_page_html", new=AsyncMock()) as fetch, \
             patch.object(wechat_to_md, "download_all_images", new=AsyncMock()) as download:
            data = self.run_capture()
            fetch.assert_not_awaited()
            download.assert_not_awaited()
        self.assertEqual(data["status"], "complete")
        md = Path(data["markdown_path"]).read_text()
        self.assertRegex(md, r'```python\s+print\("hello"\)\s+```')
        self.assertIn("文章的正文内容", md)
        self.assertEqual(Path(data["html_path"]).read_bytes(), self.html.read_bytes())
        manifest = tool.verify_capture(Path(data["manifest_path"]), URL)
        self.assertEqual(manifest["author"], "测试作者")

    def test_cache_reuse_and_corruption_detection(self):
        first = self.run_capture()
        self.args.html = None
        with patch.object(wechat_to_md, "fetch_page_html", new=AsyncMock()) as fetch:
            cached = self.run_capture()
            fetch.assert_not_awaited()
        self.assertTrue(cached["cached"])
        self.assertEqual(first["markdown_path"], cached["markdown_path"])
        Path(first["markdown_path"]).write_text("损坏缓存")
        with self.assertRaisesRegex(ValueError, "缓存文件已变化"):
            self.run_capture()

    def test_refresh_preserves_previous_version(self):
        first = self.run_capture()
        self.args.refresh = True
        self.html.write_text(HTML.replace("文章的正文内容", "更新后的正文内容"))
        second = self.run_capture()
        self.assertNotEqual(first["markdown_path"], second["markdown_path"])
        self.assertIn("文章的正文内容", Path(first["markdown_path"]).read_text())
        self.assertIn("更新后的正文内容", Path(second["markdown_path"]).read_text())

    def test_verification_or_empty_page_produces_no_raw(self):
        for content, error in (("<h2>环境异常</h2><a id='js_verify'>去验证</a>", wechat_to_md.CaptchaError),
                               ("<h1 id='activity-name'>空文章</h1><div id='js_content'></div>", wechat_to_md.ParseError)):
            self.html.write_text(content)
            with self.assertRaises(error):
                self.run_capture()
            self.assertFalse(self.args.output.exists())

    def test_missing_images_are_partial_and_downloaded_images_are_hashed(self):
        html = HTML.replace("</div></html>", '<img data-src="https://example.com/fixture.png"></div></html>')
        self.html.write_text(html)
        offline = self.run_capture()
        self.assertEqual(offline["status"], "partial_images")
        self.assertEqual(offline["images_downloaded"], 0)
        self.args.html = None
        self.args.refresh = True

        async def download(urls, root):
            root.mkdir(parents=True)
            (root / "img_001.png").write_bytes(b"fixture-image")
            return {urls[0]: "images/img_001.png"}

        with patch.object(wechat_to_md, "fetch_page_html", new=AsyncMock(return_value=html)), \
             patch.object(wechat_to_md, "download_all_images", new=download):
            downloaded = self.run_capture()
        self.assertEqual(downloaded["status"], "complete")
        self.assertIn("images/img_001.png", Path(downloaded["markdown_path"]).read_text())
        manifest = json.loads(Path(downloaded["manifest_path"]).read_text())
        self.assertEqual(manifest["images"][0]["sha256"], tool.digest(Path(downloaded["markdown_path"]).parent / "images/img_001.png"))

    def test_url_restriction(self):
        self.assertTrue(tool.valid_url(URL))
        self.assertTrue(tool.valid_url("https://mp.weixin.qq.com/s?__biz=example&mid=1"))
        for url in ("http://mp.weixin.qq.com/s/a", "https://mp.weixin.qq.com.evil/s/a",
                    "https://mp.weixin.qq.com@evil/s/a", "https://mp.weixin.qq.com/", "https://mp.weixin.qq.com:invalid/s/a"):
            self.assertFalse(tool.valid_url(url), url)

    def test_cli_offline_returns_one_json_document(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), URL, "--html", str(self.html),
                               "--output", str(self.args.output)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["ok"])
        self.assertTrue(Path(data["markdown_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
