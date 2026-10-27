import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "paddleocr_parse", Path(__file__).resolve().parents[1] / "paddleocr_parse.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class Crop:
    width, height = 100, 80

    def __init__(self, content):
        self.content = content

    def save(self, target):
        target.write_bytes(self.content)


class PageResult:
    def __init__(self, content):
        self.markdown = {"markdown_texts": "![图](imgs/crop.png)",
                         "markdown_images": {"imgs/crop.png": Crop(content)}}

    def save_to_json(self, target):
        Path(target).write_text(json.dumps({"res": {"parsing_res_list": []}}))


class ExportTests(unittest.TestCase):
    def test_machine_document_preserves_provenance_and_cannot_be_ingested_directly(self):
        text = '<!-- Source page: 12 -->\n\n# 第一章\n正文。\n![图](pages/page-0012/imgs/crop.png)'
        rendered = tool.ocr_markdown(text, "ocr_output", source_file='书名 "测试".pdf',
                                     source_sha256="a" * 64)
        self.assertTrue(rendered.startswith('---\nraw_role: "ocr_output"\ningest_allowed: false\n'))
        self.assertIn('source_sha256: "' + "a" * 64 + '"', rendered)
        self.assertTrue(rendered.endswith(text))

    def test_same_crop_name_on_two_pages_preserves_both_and_resolves_links(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            for number, content in ((1, b"first image"), (2, b"second image")):
                text, info = tool.export_page(PageResult(content), stage, number)
                target = stage / info["images"][0]["path"]
                self.assertEqual(target.read_bytes(), content)
                self.assertIn(info["images"][0]["path"], text)
                local_md = (stage / info["markdown"]).read_text()
                self.assertIn('raw_role: "ocr_page"', local_md)
                self.assertIn('ingest_allowed: false', local_md)
                self.assertIn(f'source_page: {number}', local_md)
                self.assertIn("imgs/crop.png", local_md)
                self.assertEqual(info["images"][0]["sha256"], tool.digest(target))
            self.assertEqual(len(list(stage.rglob("crop.png"))), 2)

    def test_crop_cannot_escape_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in ("../outside.png", "/tmp/outside.png", "."):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    tool.safe_child(Path(directory), path)


if __name__ == "__main__":
    unittest.main()
