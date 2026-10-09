import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/raw_wiki_ingest.py"
SPEC = importlib.util.spec_from_file_location("raw_wiki_ingest", MODULE_PATH)
assert SPEC and SPEC.loader
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


class RawWikiIngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoding = workflow.tokenize("o200k_base")

    def test_structured_document_uses_heading_boundaries(self):
        source = "# 甲\n第一节内容。\n## 乙\n第二节内容。\n# 丙\n第三节内容。\n"
        mode, _, _, units = workflow.make_units(source, "raw/example.md", self.encoding, 10, 100)
        self.assertEqual(mode, "structured")
        self.assertEqual([unit["heading_path"] for unit in units], [["甲"], ["甲", "乙"], ["丙"]])
        self.assertTrue(all(unit["split_basis"] == "structure" for unit in units))
        self.assertEqual(workflow.check_coverage(source, units), [])

    def test_time_markers_without_headings_follow_token_budget(self):
        source = "".join(f"@发言人 {minute:02d}:00\n这是第 {minute} 段内容。\n" for minute in range(30))
        mode, _, _, units = workflow.make_units(source, "raw/example.md", self.encoding, 100, 100)
        self.assertEqual(mode, "unstructured")
        self.assertGreater(len(units), 1)
        self.assertTrue(all(unit["token_count"] <= 100 for unit in units))
        self.assertLess(len(units), 30)
        self.assertEqual(workflow.check_coverage(source, units), [])

    def test_numbered_lists_and_tables_without_headings_use_token_budget(self):
        source = "1. 第一项\n2. 第二项\n3. 第三项\n| A | B |\n|---|---|\n| 1 | 2 |\n"
        self.assertEqual(workflow.structural_boundaries(source)[0], "unstructured")

    def test_unstructured_document_obeys_exact_token_budget(self):
        source = "这是一段没有标题的讨论。" * 4000
        mode, _, _, units = workflow.make_units(source, "raw/example.md", self.encoding, 20000, 20000)
        self.assertEqual(mode, "unstructured")
        self.assertGreater(len(units), 1)
        self.assertTrue(all(unit["token_count"] <= 20000 for unit in units))
        self.assertEqual(workflow.check_coverage(source, units), [])

    def test_code_fence_does_not_create_fake_structure(self):
        source = "普通讨论。\n```md\n# 假标题\n## 另一个假标题\n```\n继续讨论。\n"
        self.assertEqual(workflow.structural_boundaries(source)[0], "unstructured")

    def test_summary_rejects_non_verbatim_evidence(self):
        source = "# 甲\n原文说，取消订单需要人工确认。\n# 乙\n其他材料。\n"
        _, _, _, units = workflow.make_units(source, "raw/example.md", self.encoding, 5000, 12000)
        payload = {"unit_id": units[0]["unit_id"], "summary": "取消订单规则", "items": [
            {"type": "rule", "topic": "取消订单", "statement": "需要人工确认", "quotes": ["取消订单可以自动通过"]}
        ]}
        with self.assertRaisesRegex(ValueError, "not found verbatim"):
            workflow.validate_summary(payload, units[0], source)

    def test_run_moves_to_ready_only_after_all_valid_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            raw_path = root / "raw/example.md"
            raw_path.parent.mkdir()
            raw_path.write_text("# 目标\n业务先确认目标。\n# 流程\n审批通过后才能执行。\n", encoding="utf-8")
            (root / "wiki/sources").mkdir(parents=True)
            with patch.object(workflow, "ROOT", root), patch.object(workflow, "RUNS", root / "runs"):
                result = workflow.prepare(SimpleNamespace(source="raw/example.md", encoding="o200k_base",
                                                          chunk_tokens=5000, model_tokens=12000, model_id="test-model"))
                self.assertEqual(result["mode"], "structured")
                _, content, directory, manifest, units = workflow.load_run("raw/example.md")
                self.assertEqual(manifest["status"], "mapped")
                for unit in units:
                    text = content[unit["locator"]["char_start"]:unit["locator"]["char_end"]]
                    quote = "业务先确认目标。" if "目标" in text else "审批通过后才能执行。"
                    summary_file = root / "summary.json"
                    summary_file.write_text(json.dumps({"unit_id": unit["unit_id"], "summary": "导航摘要",
                        "items": [{"type": "rule", "topic": "业务规则", "statement": quote, "quotes": [quote]}]},
                        ensure_ascii=False), encoding="utf-8")
                    workflow.record(SimpleNamespace(source="raw/example.md", unit_id=unit["unit_id"],
                                                    json_file=str(summary_file), replace=False))
                _, _, _, manifest, _ = workflow.load_run("raw/example.md")
                self.assertEqual(manifest["status"], "ready")
                manifest["status"] = "summarizing"
                workflow.write_json(directory / "manifest.json", manifest)
                _, _, _, recovered, _ = workflow.load_run("raw/example.md")
                self.assertEqual(recovered["status"], "ready")
                plan_file = root / "plan.json"
                plan_file.write_text(json.dumps({"pages": [{"page_type": "source", "target_path": "wiki/sources/example.md",
                    "action": "create", "reason": "one material", "evidence_requirements": [
                        {"question": "目标是什么？", "unit_ids": [units[0]["unit_id"]], "coverage": "covered"}]}]}), encoding="utf-8")
                payload = json.loads(plan_file.read_text())
                payload["pages"][0].update(title="示例", keywords=["目标"], relations=[])
                for kind in sorted(workflow.REQUIRED_INGEST_PAGE_TYPES - {"source"}):
                    payload["pages"].append({"page_type": kind,
                        "target_path": f"wiki/{workflow.PAGE_DIRECTORIES[kind]}/候选.md",
                        "action": "skip", "reason": "该测试原文不支持独立知识页",
                        "evidence_requirements": [{"question": "是否有足够证据？", "unit_ids": [], "coverage": "not_found"}]})
                plan_file.write_text(json.dumps(payload), encoding="utf-8")
                workflow.record_plan(SimpleNamespace(source="raw/example.md", json_file=str(plan_file)))
                saved = json.loads((directory / "page-plan.json").read_text())
                self.assertEqual(saved["relations_version"], 1)
                self.assertIn("relation_candidates", saved)
                self.assertEqual(workflow.verify_plan(SimpleNamespace(source="raw/example.md"))["status"], "failed")
                recalled = workflow.context(SimpleNamespace(source="raw/example.md", unit_id=units[0]["unit_id"], adjacent=0))
                self.assertIn("业务先确认目标", recalled["units"][0]["raw_text"])
                source_page = root / "wiki/sources/example.md"
                source_page.write_text("---\ntype: source\ntitle: 示例\nsource_path: raw/example.md\nsha256: "
                    + manifest["source_sha256"] + "\nmaterial_type: 课程总结\nreliability: 结构化原文\n"
                    "ingested_at: 2026-09-24T00:00:00+08:00\nstatus: ingested\ntags: [AI产品]\n---\n"
                    "# 示例\n\n## 证据摘录\n- 「业务先确认目标。」〔§目标〕\n",
                    encoding="utf-8")
                self.assertEqual(workflow.verify_source(SimpleNamespace(page="wiki/sources/example.md"))["status"], "passed")
                self.assertEqual(workflow.verify_plan(SimpleNamespace(source="raw/example.md"))["status"], "passed")
                # Refresh after writing a registered create, without pretending it is a new merge.
                workflow.record_plan(SimpleNamespace(source="raw/example.md", json_file=str(plan_file)))
                candidate = root / "wiki/notes/示例方法.md"
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text("---\ntype: note\ntitle: 示例方法\nsources: [sources/example.md]\nstatus: draft\ntags: [目标]\n---\n# 示例方法\n\n## 步骤\n核对业务目标。\n")
                with self.assertRaisesRegex(ValueError, "undecided strong candidate"):
                    workflow.record_plan(SimpleNamespace(source="raw/example.md", json_file=str(plan_file)))
                payload["pages"][0]["relations"] = [{
                    "candidate_path": "wiki/notes/示例方法.md", "decision": "related",
                    "relation": "原文到执行方法", "reason": "读者需要从原文找到核对步骤",
                    "evidence": ["原文目标单元；示例方法步骤"],
                    "placements": [{"from_path": "wiki/sources/example.md", "to_path": "wiki/notes/示例方法.md", "section": "证据摘录"}]}]
                plan_file.write_text(json.dumps(payload), encoding="utf-8")
                workflow.record_plan(SimpleNamespace(source="raw/example.md", json_file=str(plan_file)))
                saved = json.loads((directory / "page-plan.json").read_text())
                self.assertEqual(saved["pages"][0]["relations"], payload["pages"][0]["relations"])
                self.assertTrue(any("not written" in e for e in workflow.verify_plan(SimpleNamespace(source="raw/example.md"))["errors"]))
                source_page.write_text(source_page.read_text() + "\n执行入口：[[示例方法]]\n")
                self.assertEqual(workflow.verify_plan(SimpleNamespace(source="raw/example.md"))["status"], "passed")
                saved.pop("relations_version")
                workflow.write_json(directory / "page-plan.json", saved)
                legacy = workflow.verify_plan(SimpleNamespace(source="raw/example.md"))
                self.assertFalse(legacy["complete"])
                self.assertTrue(any("legacy" in e for e in legacy["errors"]))
                workflow.record_plan(SimpleNamespace(source="raw/example.md", json_file=str(plan_file)))
                source_page.write_text(source_page.read_text(encoding="utf-8").replace(
                    "- 「业务先确认目标。」〔§目标〕", "- 「业务先确认目标。」；「这句不在原文里」〔§目标〕"), encoding="utf-8")
                self.assertEqual(workflow.verify_source(SimpleNamespace(page="wiki/sources/example.md"))["status"], "failed")
                reused = workflow.prepare(SimpleNamespace(source="raw/example.md", encoding="o200k_base",
                                                           chunk_tokens=5000, model_tokens=12000, model_id="test-model"))
                self.assertEqual(reused["registration"]["status"], "needs_review")
                summary_path = directory / "summaries.jsonl"
                summaries = workflow.read_jsonl(summary_path)
                summaries[0]["summary"] = "修订过的导航摘要"
                workflow.write_jsonl(summary_path, summaries)
                with self.assertRaisesRegex(RuntimeError, "page plan is stale"):
                    workflow.context(SimpleNamespace(source="raw/example.md", unit_id=units[0]["unit_id"], adjacent=0))
                summaries[0]["items"][0]["evidence"][0]["quote"] = "伪造的原文摘录"
                workflow.write_jsonl(summary_path, summaries)
                with self.assertRaisesRegex(RuntimeError, "failed audit"):
                    workflow.find_evidence(SimpleNamespace(source="raw/example.md", query="业务", include_raw=False))


if __name__ == "__main__":
    unittest.main()
