import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import shutil
from copy import deepcopy
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("learning_state", Path(__file__).resolve().parents[2] / "skills/wiki-learning/scripts/learning_state.py")
learning = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learning)


class LearningStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.map = self.root / "wiki/map/AI.md"
        self.map.parent.mkdir(parents=True)
        self.map.write_text("# AI\n\n## 1. RAG\n\n## 2. Agent\n")
        self.wiki = self.root / "wiki/concepts/RAG.md"
        self.wiki.parent.mkdir(parents=True)
        self.wiki.write_text("# RAG\n检索和生成是不同环节。\n")
        template = Path(__file__).resolve().parents[2] / "skills/wiki-learning/assets/state.json"
        self.state = json.loads(template.read_text())
        self.state.update(domain_slug="ai", topic_slug="rag", topic="RAG",
                          map_ref={"path": "wiki/map/AI.md", "section": "1. RAG"})
        self.unit = self.state["units"][0]
        self.unit.update(id="retrieval", name="检索与生成", source_refs=["wiki/concepts/RAG.md"])

    def answer(self, aid, node, result="pass", unit_id="retrieval", round_id=None):
        item = dict(id=aid, unit_id=unit_id, node=node, question="检索命中是否必然回答正确？",
                    answer_key="不必然，需要分别检查检索和生成", user_answer="不必然",
                    result=result, feedback="检索成功并不能保证生成正确。", source_refs=["wiki/concepts/RAG.md"])
        if round_id:
            item["round_id"] = round_id
        return item

    def diagnose(self, state):
        state["assessments"].append(self.answer("d1", "diagnosis", unit_id=None))
        state["diagnosis"] = dict(completed=True, summary="能区分检索与生成，继续学评测边界", assessment_ids=["d1"])
        state["session"]["phase"] = "planning"

    def completed(self):
        state = deepcopy(self.state)
        self.diagnose(state)
        unit = state["units"][0]
        unit.update(status="completed", mastered=True, taught=True, teaching_note="已讲解检索和生成的职责边界。")
        state["assessments"].extend([
            self.answer("u1", "understanding"), self.answer("p1", "practice"),
            self.answer("r1", "recall", round_id="round1"),
            self.answer("r2", "recall", round_id="round1")])
        unit["checks"] = dict(understanding="u1", practice="p1", recall=["r1", "r2"])
        state["session"].update(phase="consolidation", active_unit_id="retrieval")
        return state

    def read(self, slug="rag"):
        return json.loads((self.root / f"learning/ai/{slug}/state.json").read_text())

    def test_pause_and_resume_preserve_pending_question_and_answer(self):
        question = self.answer("d1", "diagnosis", unit_id=None)
        pending = {k: v for k, v in question.items() if k not in {"user_answer", "result", "feedback"}}
        self.state["session"]["pending_question"] = pending
        result = learning.save(self.state, 0, self.root)
        self.assertEqual(result["revision"], 1)
        state = self.read()
        state["session"].update(status="paused", reason="用户时间到了")
        learning.save(state, 1, self.root)
        state = self.read()
        self.assertEqual(state["session"]["pending_question"], pending)
        state["session"].update(status="active", reason="")
        learning.save(state, 2, self.root)
        state = self.read()
        self.diagnose(state)
        state["session"]["pending_question"] = None
        learning.save(state, 3, self.root)
        restored = self.read()
        self.assertEqual(restored["assessments"][0]["user_answer"], "不必然")
        self.assertEqual(restored["session"]["phase"], "planning")
        self.assertIsNone(restored["session"]["pending_question"])

    def test_mastery_needs_complete_actual_evidence(self):
        state = self.completed()
        learning.validate(state, self.root)
        for missing in ("user_answer", "answer_key", "feedback"):
            bad = deepcopy(state)
            bad["assessments"][-1][missing] = ""
            with self.assertRaises(ValueError):
                learning.validate(bad, self.root)
        state["units"][0]["checks"]["understanding"] = None
        with self.assertRaises(ValueError):
            learning.validate(state, self.root)

    def test_partial_and_failed_rounds_drive_different_statuses(self):
        state = self.completed()
        state["assessments"][-1]["result"] = "fail"
        with self.assertRaises(ValueError):
            learning.validate(state, self.root)
        state["units"][0]["mastered"] = False
        learning.validate(state, self.root)
        state["assessments"][-2]["result"] = "fail"
        with self.assertRaises(ValueError):
            learning.validate(state, self.root)
        state["units"][0]["status"] = "needs_review"
        state["units"][0]["stuck_count"] = 3
        state["session"].update(phase="feedback", repair_node="teaching")
        learning.validate(state, self.root)

    def test_cannot_mix_rounds_or_omit_a_failed_answer(self):
        state = self.completed()
        state["assessments"][-1]["round_id"] = "round2"
        with self.assertRaises(ValueError):
            learning.validate(state, self.root)
        state = self.completed()
        state["assessments"].append(self.answer("r3", "recall", result="fail", round_id="round1"))
        with self.assertRaises(ValueError):
            learning.validate(state, self.root)

    def test_explicit_question_count_is_checked_without_forcing_two_questions(self):
        state = self.completed()
        state["assessments"].pop()
        unit = state["units"][0]
        unit["checks"]["recall"] = ["r1"]
        with self.assertRaises(ValueError):
            learning.validate(state, self.root)
        unit.update(recall_target=1, recall_note="用户在测试前明确要求只做一道场景题")
        learning.validate(state, self.root)

    def test_stale_revision_and_removed_history_do_not_overwrite(self):
        learning.save(self.state, 0, self.root)
        old = self.read()
        current = deepcopy(old)
        self.diagnose(current)
        learning.save(current, 1, self.root)
        before = (self.root / "learning/ai/rag/state.json").read_bytes()
        with self.assertRaises(ValueError):
            learning.save(old, 1, self.root)
        current = self.read()
        current["assessments"][0]["user_answer"] = "改写了旧原答"
        with self.assertRaises(ValueError):
            learning.save(current, 2, self.root)
        self.assertEqual((self.root / "learning/ai/rag/state.json").read_bytes(), before)

    def test_node_gates_and_unanswered_question_cannot_silently_disappear(self):
        state = deepcopy(self.state)
        state["session"]["pending_question"] = {
            k: v for k, v in self.answer("d1", "diagnosis", unit_id=None).items()
            if k not in {"user_answer", "result", "feedback"}}
        learning.save(state, 0, self.root)
        state = self.read()
        state["session"]["pending_question"] = None
        with self.assertRaises(ValueError):
            learning.save(state, 1, self.root)
        state["session"]["reason"] = "用户明确跳过此题"
        learning.save(state, 1, self.root)
        state = self.completed()
        state["session"]["phase"] = "recall"
        state["assessments"][2]["result"] = "fail"
        state["units"][0]["status"] = "in_progress"
        state["units"][0]["mastered"] = False
        with self.assertRaises(ValueError):
            learning.validate(state, self.root)

    def test_topics_are_independent_and_index_uses_saved_states(self):
        learning.save(self.state, 0, self.root)
        other = deepcopy(self.state)
        other.update(topic_slug="agent", topic="Agent",
                     map_ref={"path": "wiki/map/AI.md", "section": "2. Agent"})
        learning.save(other, 0, self.root)
        self.assertEqual(self.read("rag")["topic"], "RAG")
        self.assertEqual(self.read("agent")["topic"], "Agent")
        index = (self.root / "learning/index.md").read_text()
        self.assertIn("ai/rag/state.json", index)
        self.assertIn("ai/agent/state.json", index)
        self.assertEqual(index.count("0/1"), 2)

    def test_atomic_write_failure_preserves_old_state_and_cleans_temporary(self):
        learning.save(self.state, 0, self.root)
        state = self.read()
        before = (self.root / "learning/ai/rag/state.json").read_bytes()
        with patch.object(learning.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                learning.save(state, 1, self.root)
        directory = self.root / "learning/ai/rag"
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ["state.json"])
        self.assertEqual((directory / "state.json").read_bytes(), before)

    def test_missing_sources_and_escaping_paths_are_rejected(self):
        for value in ("../outside.md", "/tmp/outside.md", "wiki/concepts/missing.md"):
            state = deepcopy(self.state)
            state["units"][0]["source_refs"] = [value]
            with self.assertRaises(ValueError):
                learning.validate(state, self.root)
        state = deepcopy(self.state)
        state["map_ref"]["section"] = "不存在的专题"
        with self.assertRaises(ValueError):
            learning.validate(state, self.root)

    def test_index_failure_reports_saved_state_for_later_rebuild(self):
        with patch.object(learning, "render_index", side_effect=ValueError("bad other topic")):
            result = learning.save(self.state, 0, self.root)
        self.assertTrue(result["index"].startswith("failed:"))
        self.assertEqual(self.read()["revision"], 1)
        index = learning.render_index(self.root)
        self.assertIn("ai/rag/state.json", index)

    def test_standalone_inline_learning_without_any_wiki_or_map(self):
        shutil.rmtree(self.root / "wiki")
        self.state["map_ref"] = None
        self.state["source_notes"] = {"material-1": "检索取得相关材料，生成仍需正确使用材料。"}
        self.state["units"][0]["source_refs"] = ["inline:material-1"]
        learning.save(self.state, 0, self.root)
        state = self.completed()
        state["revision"] = 1
        for answer in state["assessments"]:
            answer["source_refs"] = ["inline:material-1"]
        learning.save(state, 1, self.root)
        self.assertTrue(self.read()["units"][0]["mastered"])
        current = self.read()
        current["source_notes"]["material-1"] = "改写了原始材料"
        with self.assertRaises(ValueError):
            learning.save(current, 2, self.root)

    def test_sources_can_be_local_url_or_explicit_general_knowledge(self):
        self.state["map_ref"] = None
        for source in (str(self.wiki), "https://example.com/article", "knowledge:检索与生成"):
            self.state["units"][0]["source_refs"] = [source]
            learning.validate(self.state, self.root)
        self.state["units"][0]["source_refs"] = ["inline:missing"]
        with self.assertRaises(ValueError):
            learning.validate(self.state, self.root)

    def test_version_one_wiki_references_remain_readable(self):
        state = self.completed()
        state["schema_version"] = 1
        for record in state["units"] + state["assessments"]:
            record["wiki_refs"] = record.pop("source_refs")
        learning.validate(state, self.root)


if __name__ == "__main__":
    unittest.main()
