import json
import tempfile
import unittest
from pathlib import Path
from tools import wiki_relations as r


class WikiRelationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.concept = 'wiki/concepts/评测.md'
        self.note = 'wiki/notes/复核方法.md'
        self.write(self.concept, 'concept', '评测', '评测', '## 怎么用\n解释评测。')
        self.write(self.note, 'note', '复核方法', '评测', '## 步骤\n使用[[评测]]。')
        self.plan = {'pages': [{'target_path': self.concept, 'page_type': 'concept', 'title': '评测',
                               'keywords': ['评测'], 'action': 'merge', 'relations': []}]}

    def write(self, path, typ, title, tags, body, extra=''):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f'---\ntype: {typ}\ntitle: {title}\ntags: [{tags}]\nstatus: draft\n{extra}---\n# {title}\n\n> 简短导语。\n\n{body}\n')

    def decide(self, decision='related', target=None):
        target = target or self.note
        d = {'candidate_path': target, 'decision': decision, 'relation': '概念到方法步骤',
             'reason': '正文步骤使用该评测概念' if decision == 'related' else '仅共享标签，处理对象不同或证据待核',
             'evidence': ['候选正文步骤；原文单元1'], 'placements': []}
        if decision == 'related':
            d['placements'] = [{'from_path': self.concept, 'to_path': target, 'section': '怎么用'}]
        self.plan['pages'][0]['relations'].append(d)
        return d

    def test_reverse_link_exposes_missing_reader_entry(self):
        scan = r.candidates(self.plan, self.root)
        c = next(c for c in scan['pages'][0]['candidates'] if c['candidate_path'] == self.note)
        self.assertEqual(c['strength'], 'strong')
        self.assertIn('existing_link', c['reasons'])
        self.assertTrue(any('undecided' in e for e in r.verify(self.plan, self.root)['errors']))
        self.decide()
        self.assertTrue(any('not written' in e for e in r.verify(self.plan, self.root)['errors']))
        with (self.root / self.concept).open('a') as f:
            f.write('\n执行复核参见[[复核方法]]。\n')
        self.assertEqual(r.verify(self.plan, self.root)['status'], 'passed')

    def test_entity_name_substring_in_manual_title_is_not_identity(self):
        entity = 'wiki/entities/吉客云.md'
        self.write(entity, 'entity', '吉客云', '吉客云', '## 是什么\n业务软件。')
        manual = 'wiki/sources/吉客云用户手册-换单发货.md'
        self.write(manual, 'source', '吉客云用户手册-换单发货', '换单发货', '## 核心观点\n更换物流公司。',
                   'sources: [sources/吉客云用户手册-换单发货.md]\n')
        plan = {'pages': [{'target_path': entity, 'page_type': 'entity', 'title': '吉客云',
                           'keywords': ['换单发货'], 'sources': ['sources/未关联的来源.md'],
                           'action': 'merge', 'relations': []}]}
        scan = r.candidates(plan, self.root)
        candidate = next(c for c in scan['pages'][0]['candidates'] if c['candidate_path'] == manual)
        self.assertNotEqual(candidate['strength'], 'strong')

    def test_shared_tag_does_not_force_a_link(self):
        unrelated = 'wiki/notes/设备巡检.md'
        self.write(unrelated, 'note', '设备巡检', '评测', '## 步骤\n检查温度传感器。')
        c = next(c for c in r.candidates(self.plan, self.root)['pages'][0]['candidates'] if c['candidate_path'] == unrelated)
        self.assertEqual(c['strength'], 'weak')
        self.decide('unrelated')
        self.decide('unrelated', unrelated)
        self.assertEqual(r.verify(self.plan, self.root)['status'], 'passed')
        self.assertNotIn('设备巡检', (self.root / self.concept).read_text())

    def test_planned_pages_and_final_metadata_rescan(self):
        self.decide('unrelated')
        new = {'target_path': 'wiki/solutions/新方案.md', 'page_type': 'solution', 'title': '其他应用',
               'keywords': ['其他'], 'action': 'create', 'relations': []}
        self.plan['pages'].append(new)
        # A planned page can be found before it exists.
        new['title'] = '评测应用'
        scan = r.candidates(self.plan, self.root)
        self.assertTrue(any(c['candidate_path'] == new['target_path'] for c in scan['pages'][0]['candidates']))
        new['title'] = '其他应用'
        self.write(new['target_path'], 'solution', '评测应用', '其他', '## 流程\n做检查。')
        result = r.verify(self.plan, self.root)
        self.assertFalse(any('undecided strong' in e and '新方案' in e for e in result['errors']))

    def test_pending_is_not_complete_and_map_is_navigation_only(self):
        self.decide('pending')
        self.write('wiki/map/导航.md', 'map', '评测导航', '评测', '## 入口\n[[评测]]')
        scan = r.candidates(self.plan, self.root)
        c = next(c for c in scan['pages'][0]['candidates'] if c['page_type'] == 'map')
        self.assertEqual(c['strength'], 'weak')
        self.assertNotIn('existing_link', c['reasons'])
        result = r.verify(self.plan, self.root)
        self.assertEqual(result['status'], 'needs_review')
        self.assertFalse(result['complete'])

    def test_heading_anchor_dead_link_and_code_example(self):
        self.decide('unrelated')
        with (self.root / self.concept).open('a') as f:
            f.write('\n```md\n[[不存在的示例]]\n```\n[方法](../notes/复核方法.md#不存在)\n')
        result = r.verify(self.plan, self.root)
        self.assertTrue(any('missing anchor' in e for e in result['errors']))
        self.assertFalse(any('不存在的示例' in e for e in result['errors']))
        with (self.root / self.concept).open('a') as f:
            f.write('\n[[已删除的内容]]\n')
        self.assertTrue(any('dead/ambiguous' in e for e in r.verify(self.plan, self.root)['errors']))

    def test_verified_backlink_protection(self):
        f = self.root / self.note
        f.write_text(f.read_text().replace('status: draft', 'status: verified'))
        decision = self.decide()
        decision['placements'] = [{'from_path': self.note, 'to_path': self.concept, 'section': '步骤'}]
        self.plan['verified_baselines'] = r.baselines(self.plan, self.root)
        self.assertEqual(r.verify(self.plan, self.root)['status'], 'passed')
        f.write_text(f.read_text() + '\n改动\n')
        self.assertTrue(any('verified page changed' in e for e in r.verify(self.plan, self.root)['errors']))

    def test_wrong_section_cannot_be_satisfied_by_related_list(self):
        self.decide()
        with (self.root / self.concept).open('a') as f:
            f.write('\n## 相关\n[[复核方法]]\n')
        self.assertTrue(any('not written at section' in e for e in r.verify(self.plan, self.root)['errors']))

    def test_strong_candidates_are_never_hidden_by_limit(self):
        for i in range(10):
            self.write(f'wiki/notes/方法{i}.md', 'note', f'方法{i}', '其他', '## 步骤\n[[评测]]')
        scan = r.candidates(self.plan, self.root, limit=0)
        self.assertEqual(len(scan['pages'][0]['candidates']), 11)

    def test_intro_placement_and_alias_resolution(self):
        self.write(self.note, 'note', '复核方法', '评测', '## 步骤\n[[评测]]', 'aliases: [人工复核]\n')
        d = self.decide()
        d['placements'][0]['section'] = '开篇'
        f = self.root / self.concept
        f.write_text(f.read_text().replace('> 简短导语。', '> 使用[[人工复核]]。'))
        self.assertEqual(r.verify(self.plan, self.root)['status'], 'passed')

    def test_previous_strong_candidate_survives_metadata_change(self):
        old = r.candidates(self.plan, self.root)
        self.plan['relation_candidates'] = old
        self.write(self.note, 'note', '复核方法', '其他', '## 步骤\n不再直接引用。')
        scan = r.retain_strong(r.candidates(self.plan, self.root), old)
        self.assertTrue(any(c['candidate_path'] == self.note and c['strength'] == 'strong'
                            for c in scan['pages'][0]['candidates']))
        self.assertTrue(any('undecided strong' in e for e in r.verify(self.plan, self.root)['errors']))


if __name__ == '__main__':
    unittest.main()
