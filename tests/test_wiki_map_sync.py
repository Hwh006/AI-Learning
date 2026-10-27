import tempfile
import unittest
from pathlib import Path

from tools import wiki_map_sync as sync


class MapSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.add("wiki/concepts/甲.md", "concept", "# 甲\n\n## 原理\n\n正文。")
        self.add("wiki/sources/材料.md", "source", "# 材料")
        self.map("[[wiki/concepts/甲|甲]]")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, path, kind, body, extra=""):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\ntype: {kind}\ntitle: {p.stem}\ntags: [测试]\n{extra}---\n\n{body}\n")

    def map(self, links, count=1):
        self.add("wiki/map/AI.md", "map", f"# AI\n\n## 1. 主题\n\n{links}", f"coverage_pages: {count}\n")

    def test_missing_page_requires_real_placement_and_decision(self):
        self.add("wiki/concepts/乙.md", "concept", "# 乙")
        before = sync.snapshot(self.root)
        self.assertEqual(before["missing"], ["wiki/concepts/乙.md"])
        self.assertEqual(sync.verify(self.root, before, [])["status"], "failed")
        self.map("[[wiki/concepts/甲|甲]]\n[[wiki/concepts/乙|乙]]", 2)
        decision = [{"page": "wiki/concepts/乙.md", "reason": "讲同一领域的主题机制。",
                     "placements": [{"map": "wiki/map/AI.md", "heading": "1. 主题"}]}]
        self.assertEqual(sync.verify(self.root, before, [])["status"], "failed")
        self.assertEqual(sync.verify(self.root, before, decision)["status"], "passed")
        decision[0]["placements"][0]["heading"] = "不存在的标题"
        self.assertEqual(sync.verify(self.root, before, decision)["status"], "failed")

    def test_bad_anchor_source_and_count_fail(self):
        before = sync.snapshot(self.root)
        self.map("[[wiki/concepts/甲#不存在|甲]]\n[[wiki/sources/材料|材料]]", 9)
        result = sync.verify(self.root, before, [])
        self.assertTrue(any("broken anchor" in x for x in result["errors"]))
        self.assertTrue(any("excluded target" in x for x in result["errors"]))
        self.assertTrue(any("coverage_pages mismatch" in x for x in result["errors"]))

    def test_old_entry_loss_or_changed_knowledge_fails(self):
        before = sync.snapshot(self.root)
        self.map("", 0)
        self.assertTrue(any("previous map entry lost" in x for x in sync.verify(self.root, before, [])["errors"]))
        self.map("[[wiki/concepts/甲|甲]]")
        (self.root / "wiki/concepts/甲.md").write_text("changed")
        self.assertFalse(sync.verify(self.root, before, [])["knowledge_pages_unchanged"])

    def test_source_history_and_nonsearchable_are_excluded(self):
        self.add("wiki/notes/_history/旧版.md", "note", "# 旧版")
        self.add("wiki/notes/停用.md", "note", "# 停用", "searchable: false\n")
        result = sync.snapshot(self.root, ["wiki/sources/材料.md", "wiki/notes/_history/旧版.md", "wiki/notes/停用.md"])
        self.assertEqual(len(result["excluded"]), 3)
        self.assertEqual(result["pages"], [])

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            sync.snapshot(self.root, ["wiki/../../outside.md"])
        with self.assertRaises(ValueError):
            sync.snapshot(self.root, ["wiki/concepts/不存在.md"])

    def test_cross_map_migration_requires_explicit_old_and_valid_new_entry(self):
        before = sync.snapshot(self.root)
        self.map("", 0)
        self.add("wiki/map/业务.md", "map", "# 业务\n\n## 1. 业务主题\n\n[[wiki/concepts/甲|甲]]", "coverage_pages: 1\n")
        decision = [{"page": "wiki/concepts/甲.md", "reason": "正文主旨属于业务主题。",
                     "placements": [{"map": "wiki/map/业务.md", "heading": "1. 业务主题"}]}]
        self.assertEqual(sync.verify(self.root, before, decision)["status"], "failed")
        decision[0]["previous_placements"] = before["pages"][0]["existing_placements"]
        self.assertEqual(sync.verify(self.root, before, decision)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
