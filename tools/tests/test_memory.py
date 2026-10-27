import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
memory = load("memory_commit")
catalog = load("memory_catalog")


class MemoryCommitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in ("AGENTS.md", "memory/写入机制.md", "memory/索引.md"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original\n")
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.fields = dict(id="example",title="示例",category="偏好",
                           status="active",origin="user_requirement",tags=["示例"],summary="测"*120,
                           created="2026-10-15",updated="2026-10-15")
        entry = "---\n" + "".join(f"{k}: {json.dumps(v,ensure_ascii=False)}\n" for k,v in self.fields.items()) + "---\n正文\n"
        (self.stage / "entry.md").write_text(entry)
        (self.stage / "index.md").write_text(catalog.render_index([dict(self.fields,path="偏好/示例.md")]))
        self.packet = {"judgment":{"accepted":True,"origin":"user_requirement","reason":"明确要求","evidence":["用户原话"]},
            "rules":{name:memory.sha((self.root/name).read_bytes()) for name in ("AGENTS.md","memory/写入机制.md")},
            "changes":[{"target":"memory/偏好/示例.md","expected_sha256":None,"draft":"entry.md","draft_sha256":memory.sha((self.stage/"entry.md").read_bytes())},
                {"target":"memory/索引.md","expected_sha256":memory.sha((self.root/"memory/索引.md").read_bytes()),"draft":"index.md","draft_sha256":memory.sha((self.stage/"index.md").read_bytes())}]}
        self.path = self.stage / "packet.json"

    def commit(self, dry=False):
        self.path.write_text(json.dumps(self.packet))
        return memory.commit(self.path,self.root,dry)

    def test_exact_copy_dry_run_and_idempotency(self):
        self.assertEqual(self.commit(True)["status"],"validated")
        self.assertFalse((self.root/"memory/偏好/示例.md").exists())
        self.assertEqual(self.commit()["status"],"applied")
        self.assertEqual((self.root/"memory/偏好/示例.md").read_bytes(),(self.stage/"entry.md").read_bytes())
        self.assertEqual(self.commit()["status"],"already_applied")

    def test_stale_target_and_changed_draft_do_not_write(self):
        (self.root/"memory/索引.md").write_text("concurrent edit")
        with self.assertRaises(ValueError):
            self.commit()
        self.assertFalse((self.root/"memory/偏好/示例.md").exists())
        (self.stage/"entry.md").write_text("changed")
        with self.assertRaises(ValueError):
            self.commit()

    def test_path_traversal_and_missing_judgment(self):
        self.packet["changes"][0]["target"]="memory/偏好/../写入机制.md"
        with self.assertRaises(ValueError):
            self.commit()
        self.packet["judgment"]["accepted"]=False
        with self.assertRaises(ValueError):
            self.commit()

    def test_write_error_rolls_back(self):
        original = memory.replace
        def fail_index(path,data):
            if path==self.root/"memory/索引.md":
                raise OSError("simulated disk error")
            original(path,data)
        with patch.object(memory,"replace",side_effect=fail_index):
            with self.assertRaises(OSError):
                self.commit()
        self.assertFalse((self.root/"memory/偏好/示例.md").exists())
        self.assertEqual((self.root/"memory/索引.md").read_text(),"original\n")

    def test_migration_preserves_id_origin_and_is_idempotent(self):
        source=self.root/"memory/条目/旧示例.md"
        source.parent.mkdir()
        source.write_text("---\nid: example\norigin: conversation_experience\n---\n原正文\n")
        # Migration authority and the historical memory's origin are distinct.
        self.fields["origin"]="conversation_experience"
        draft="---\n"+"".join(f"{k}: {json.dumps(v,ensure_ascii=False)}\n" for k,v in self.fields.items())+"---\n原正文\n"
        (self.stage/"entry.md").write_text(draft)
        (self.stage/"index.md").write_text(catalog.render_index([dict(self.fields,path="偏好/示例.md")]))
        for change in self.packet["changes"]:
            change["draft_sha256"]=memory.sha((self.stage/change["draft"]).read_bytes())
        self.packet["changes"][0].update(move_from="memory/条目/旧示例.md",source_sha256=memory.sha(source.read_bytes()))
        self.assertEqual(self.commit(True)["status"],"validated")
        self.assertTrue(source.exists())
        source.write_text(source.read_text()+"并发更新")
        with self.assertRaises(ValueError):
            self.commit()
        source.write_text(source.read_text().removesuffix("并发更新"))
        self.assertEqual(self.commit()["status"],"applied")
        self.assertFalse(source.exists())
        self.assertEqual(self.commit()["status"],"already_applied")

    def test_migration_receipt_failure_restores_source_and_index(self):
        source=self.root/"memory/条目/旧示例.md"
        source.parent.mkdir()
        original_source="---\nid: example\norigin: user_requirement\n---\n原文\n"
        source.write_text(original_source)
        self.packet["changes"][0].update(move_from="memory/条目/旧示例.md",source_sha256=memory.sha(source.read_bytes()))
        original=memory.replace
        def fail_receipt(path,data):
            if path.suffix==".json":
                raise OSError("receipt failure")
            original(path,data)
        with patch.object(memory,"replace",side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.commit()
        self.assertEqual(source.read_text(),original_source)
        self.assertEqual((self.root/"memory/索引.md").read_text(),"original\n")
        self.assertFalse((self.root/"memory/偏好/示例.md").exists())

    def test_rejects_index_drift_and_duplicate_id(self):
        (self.stage/"index.md").write_text("stale index")
        self.packet["changes"][1]["draft_sha256"]=memory.sha((self.stage/"index.md").read_bytes())
        with self.assertRaisesRegex(ValueError,"索引"):
            self.commit()
        other=self.root/"memory/经历/重复.md"
        other.parent.mkdir()
        other_fields=dict(self.fields,category="经历")
        other.write_text("---\n"+"".join(f"{k}: {json.dumps(v,ensure_ascii=False)}\n" for k,v in other_fields.items())+"---\n正文")
        with self.assertRaisesRegex(ValueError,"重复记忆 id"):
            self.commit()

    def test_metadata_bounds_categories_and_frontmatter_only(self):
        for size in (100,150):
            catalog.validate_entry(Path("memory/偏好/示例.md"),dict(self.fields,summary="字"*size))
        for size in (99,151):
            with self.assertRaises(ValueError):
                catalog.validate_entry(Path("memory/偏好/示例.md"),dict(self.fields,summary="字"*size))
        for category in catalog.CATEGORIES:
            catalog.validate_entry(Path("memory")/category/"示例.md",dict(self.fields,category=category))
        with self.assertRaises(ValueError):
            catalog.validate_entry(Path("memory/经历/示例.md"),self.fields)
        class HeaderOnly:
            def readline(self):
                return "---\n"
            def __iter__(self):
                yield "id: example\n"
                yield "---\n"
                raise AssertionError("read body")
        self.assertEqual(catalog.read_metadata(HeaderOnly()),{"id":"example"})

    def test_optional_scope_validation_and_index(self):
        item = dict(self.fields, path="偏好/示例.md")
        index = catalog.render_index([item])
        self.assertNotIn(item["title"], index)
        self.assertNotIn(item["summary"], index)
        self.assertIn("[偏好/](偏好/)", index)
        self.assertIn("什么时候读取", index)
        scoped = dict(item, scope="仅限Obsidian客户端")
        catalog.validate_entry(Path("memory/偏好/示例.md"), scoped)
        self.assertNotIn(scoped["scope"], catalog.render_index([scoped]))
        self.assertEqual(catalog.search_entries([scoped], "obsidian")["results"][0]["scope"], scoped["scope"])
        for invalid in ("", "  ", [], 1, None):
            with self.subTest(scope=invalid), self.assertRaisesRegex(ValueError, "scope"):
                catalog.validate_entry(Path("memory/偏好/示例.md"), dict(self.fields, scope=invalid))

    def test_metadata_search_scope_status_ranking_and_truncation(self):
        items = [
            dict(self.fields, id="z-both", title="项目介绍", tags=["商城"], path="偏好/示例.md"),
            dict(self.fields, id="a-one", title="商城", path="偏好/另一条.md"),
            dict(self.fields, id="retired", title="商城 项目介绍", status="retired", path="偏好/旧条目.md"),
        ]
        result = catalog.search_entries(items, "商城 项目介绍 商城", limit=1)
        self.assertEqual((result["total"], result["returned"], result["truncated"]), (2, 1, True))
        self.assertEqual(result["results"][0]["id"], "z-both")
        self.assertEqual(result["results"][0]["matched_fields"], {"title": ["项目介绍"], "tags": ["商城"]})
        self.assertEqual(catalog.search_entries(items, status="retired")["results"][0]["id"], "retired")
        self.assertEqual(catalog.search_entries(items, status="all")["total"], 3)
        self.assertEqual(catalog.search_entries(items, "未收录")["results"], [])

    def test_category_selection_and_body_are_not_search_evidence(self):
        for category, title in (("偏好", "简历写法"), ("项目", "商城项目")):
            fields = dict(self.fields, id=category, category=category, title=title)
            fields["id"] = "preference" if category == "偏好" else "project"
            path = self.root / "memory" / category / "示例.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fields.items()) + "---\n正文专有词\n")
        selected = catalog.entries(self.root, categories=["项目"])
        self.assertEqual([item["id"] for item in selected], ["project"])
        self.assertEqual(catalog.search_entries(selected, "正文专有词")["total"], 0)
        self.assertEqual(len(catalog.entries(self.root, categories=["偏好", "项目"])), 2)
        with self.assertRaisesRegex(ValueError, "未知记忆类别"):
            catalog.entries(self.root, categories=["../其他"])



if __name__ == "__main__":
    unittest.main()
