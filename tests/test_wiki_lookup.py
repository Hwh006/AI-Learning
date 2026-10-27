import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import wiki_lookup as w


class WikiLookupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, title, tags, body='', extra='', typ='concept'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\ntype: {typ}\ntitle: {title}\ntags: [{tags}]\n'
                        f'status: draft\n{extra}---\n# {title}\n\n{body}\n', encoding='utf-8')
        return name

    def test_core_coverage_beats_many_expansion_matches(self):
        both = self.write('wiki/concepts/整体.md', 'RAG 智能问答', 'RAG, 智能问答')
        self.write('wiki/concepts/局部.md', 'RAG 知识库 构建 评测 优化', 'RAG, 知识库, 构建, 评测, 优化')
        result = w.rank_pages(list(w.pages(self.root)), 'RAG 智能问答 知识库 构建 评测 优化', ['RAG', '智能问答'])
        self.assertEqual(result[0]['path'], both)
        self.assertEqual(result[0]['core_missing'], [])
        self.assertEqual(result[1]['core_missing'], ['智能问答'])

    def test_only_title_and_tags_recall_candidates(self):
        self.write('wiki/concepts/无关.md', '其他知识', '其他', '> RAG 导语\n\n## 正文\nRAG 正文',
                   'summary: RAG 摘要\naliases: [RAG]\n')
        self.assertEqual(w.rank_pages(list(w.pages(self.root)), 'RAG'), [])

    def test_focused_query_is_not_widened_by_core_keywords(self):
        self.write('wiki/concepts/RAG.md', 'RAG知识库', 'RAG, 知识库')
        qa = self.write('wiki/concepts/问答.md', '智能问答', '智能问答')
        result = w.rank_pages(list(w.pages(self.root)), '智能问答', ['RAG', '智能问答'])
        self.assertEqual([p['path'] for p in result], [qa])
        self.assertEqual(result[0]['core_missing'], ['RAG'])

    def test_acronym_is_not_a_substring_of_unrelated_word(self):
        storage = self.write('wiki/concepts/storage.md', 'Storage', 'storage')
        rag = self.write('wiki/concepts/rag.md', 'rag知识库', 'rag')
        result = w.rank_pages(list(w.pages(self.root)), 'RAG rag', ['rag', 'RAG'])
        self.assertEqual([p['path'] for p in result], [rag])
        self.assertNotIn(storage, [p['path'] for p in result])
        self.assertEqual(result[0]['core_matches'], ['rag'])

    def test_short_tag_does_not_match_a_long_unrelated_query(self):
        self.write('wiki/concepts/问答.md', '问答', '问答')
        self.assertEqual(w.rank_pages(list(w.pages(self.root)), '智能问答平台开发'), [])

    def test_duplicate_tags_do_not_inflate_score(self):
        one = {'title': 'RAG', 'tags': ['RAG']}
        many = {'title': 'RAG', 'tags': ['RAG', 'rag', 'RAG']}
        self.assertEqual(w.score(one, 'RAG'), w.score(many, 'RAG rag'))

    def test_hidden_history_and_outside_wiki_are_not_candidates(self):
        for name, extra in [('wiki/_history/旧.md', ''), ('wiki/concepts/隐藏.md', 'searchable: false\n'),
                            ('docs/资料.md', '')]:
            self.write(name, 'RAG', 'RAG', extra=extra)
        source = self.write('wiki/sources/课程.md', 'RAG', 'RAG', typ='source')
        self.assertEqual([p['path'] for p in w.rank_pages(list(w.pages(self.root)), 'RAG')], [source])

    def test_related_pages_are_filtered_and_keep_origin_sections(self):
        related = self.write('wiki/concepts/评测.md', 'RAG评测', 'RAG, 评测', extra='aliases: [验收]\n')
        self.write('wiki/concepts/无关.md', '物流', '物流')
        self.write('wiki/concepts/隐藏.md', 'RAG隐藏', 'RAG', extra='searchable: false\n')
        origin = self.write('wiki/concepts/入口.md', 'RAG入口', 'RAG',
                            '## 正文\n[[验收]] [[物流]] [[隐藏]] [[入口]]\n'
                            '[raw](../../raw/材料.md) [外部](https://example.com)\n'
                            '```md\n[[无关示例]]\n```\n<!-- [[不存在]] -->\n'
                            '## 相关\n[评测](评测.md#指标)\n')
        records = list(w.pages(self.root))
        linked = w.linked_records(origin, records, self.root)
        ranked = w.rank_pages(linked, 'RAG 评测', ['RAG'])
        self.assertEqual([p['path'] for p in ranked], [related])
        self.assertEqual(ranked[0]['linked_from'], ['正文', '相关'])
        with self.assertRaises(ValueError):
            w.linked_records('../raw/材料.md', records, self.root)

    def test_cli_json_and_empty_search(self):
        self.write('wiki/map/导航.md', 'RAG导航', 'RAG', typ='map')
        records = list(w.pages(self.root))
        for query, count in [('RAG', 1), ('完全无关', 0)]:
            output = io.StringIO()
            with patch.object(w, 'pages', return_value=iter(records)), \
                    patch('sys.argv', ['wiki_lookup.py', 'search', query, '--json']), \
                    contextlib.redirect_stdout(output):
                w.main()
            result = json.loads(output.getvalue())
            self.assertEqual(len(result), count)
            if result:
                self.assertTrue(result[0]['navigation_only'])
                self.assertNotIn('summary', result[0])


if __name__ == '__main__':
    unittest.main()
