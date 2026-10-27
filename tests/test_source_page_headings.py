import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/wiki-source-page/scripts/verify_source_page.py'
SPEC = importlib.util.spec_from_file_location('verify_source_page', SCRIPT)
SOURCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE)


class SourcePageHeadingTest(unittest.TestCase):
    def test_code_comment_does_not_end_the_owning_section(self):
        raw = '# 文档\n## 执行\n```python\n# 创建任务目录结构\nmkdir task\n```\n后续执行\n## 结束\n其他\n'
        self.assertEqual(SOURCE.header_lines(raw), [(1, '文档', 0), (2, '执行', 1), (2, '结束', 7)])
        self.assertEqual(SOURCE.match_sec_path(raw, '执行'), (True, '执行', (1, 7)))
        self.assertEqual(SOURCE.section_path_at(raw, 5), ['执行'])
        self.assertNotIn('后续执行', '\n'.join(raw.splitlines()[7:]))

    def test_only_matching_fence_type_and_length_close_code(self):
        raw = '## A\n````text\n```\n## inside\n~~~\n## still inside\n````\n## B\n  ~~~python\n# comment\n  ~~~\n## C\n'
        self.assertEqual(SOURCE.header_lines(raw), [(2, 'A', 0), (2, 'B', 7), (2, 'C', 11)])


if __name__ == '__main__':
    unittest.main()
