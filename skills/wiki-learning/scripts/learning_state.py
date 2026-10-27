#!/usr/bin/env python3
"""Validate and persist learning state; content grading belongs to the Agent."""

import argparse
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit


ROOT = Path.cwd()
NODES = {"diagnosis", "planning", "teaching", "understanding", "practice",
         "recall", "feedback", "consolidation", "recommendation"}
QUESTION_NODES = {"diagnosis", "understanding", "practice", "recall"}
UNIT_STATUSES = {"pending", "in_progress", "completed", "needs_review"}
SUPPORT = {"full", "guided", "coached", "independent"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def wiki_path(value, root, map_only=False):
    require(isinstance(value, str), "Wiki path must be a string")
    path = Path(value)
    base = root / "wiki" / "map" if map_only else root / "wiki"
    target = (root / path).resolve()
    require(not path.is_absolute() and ".." not in path.parts
            and target.is_relative_to(base.resolve())
            and target.suffix == ".md" and target.is_file(), f"Invalid Wiki path: {value}")
    return target


def source_path(value, root):
    require(isinstance(value, str), "Source path must be a string")
    path = Path(value)
    target = (root / path).resolve()
    require(target.is_file(), f"Source file does not exist: {value}")
    return target


def references(values, root, source_notes=None):
    require(isinstance(values, list) and bool(values), "At least one source reference is required")
    for value in values:
        require(nonempty(value), "Empty source reference")
        if value.startswith(("https://", "http://")):
            require(bool(urlsplit(value).netloc), "Invalid source URL")
        elif value.startswith("inline:"):
            require(nonempty((source_notes or {}).get(value[7:])), "Inline source needs saved original text")
        elif value.startswith("knowledge:"):
            require(nonempty(value[10:]), "General-knowledge reference needs a topic")
        else:
            source_path(value, root)


def source_refs(record):
    return record.get("source_refs", record.get("wiki_refs"))


def validate(data, root=ROOT):
    root = Path(root).resolve()
    require(isinstance(data, dict) and data.get("schema_version") in {1, 2},
            "Expected schema_version=1 or 2")
    require(type(data.get("revision")) is int and data["revision"] >= 0, "Invalid revision")
    for key in ("domain_slug", "topic_slug"):
        require(isinstance(data.get(key), str)
                and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", data[key]), f"Invalid {key}")
    require(nonempty(data.get("topic")), "Missing topic")
    source_notes = data.get("source_notes", {})
    require(isinstance(source_notes, dict) and all(nonempty(k) and nonempty(v)
            for k, v in source_notes.items()), "Invalid inline source notes")
    map_ref = data.get("map_ref")
    if map_ref is not None:
        require(isinstance(map_ref, dict), "Invalid map_ref")
        map_file = (wiki_path(map_ref.get("path"), root, map_only=True)
                    if data["schema_version"] == 1 else source_path(map_ref.get("path"), root))
        require(nonempty(map_ref.get("section")), "Missing Map section")
        headings = {m.group(1).strip().rstrip("#").strip()
                    for m in re.finditer(r"^#{1,6}\s+(.+)$", map_file.read_text(encoding="utf-8"), re.M)}
        require(map_ref["section"] in headings, "Map section does not exist")
    else:
        require(data["schema_version"] == 2, "Version 1 requires its original Map reference")

    units = data.get("units")
    require(isinstance(units, list), "units must be a list")
    unit_ids = [unit.get("id") for unit in units]
    require(all(nonempty(uid) for uid in unit_ids) and len(set(unit_ids)) == len(unit_ids),
            "Unit IDs must be nonempty and unique")
    unit_map = {unit["id"]: unit for unit in units}
    assessments = data.get("assessments")
    require(isinstance(assessments, list), "assessments must be a list")
    evidence = {}
    for item in assessments:
        require(isinstance(item, dict) and nonempty(item.get("id"))
                and item["id"] not in evidence, "Assessment IDs must be unique")
        require(item.get("node") in QUESTION_NODES, "Invalid assessment node")
        require(item.get("unit_id") in unit_map or
                (item["node"] == "diagnosis" and item.get("unit_id") is None),
                "Assessment points to an unknown unit")
        for key in ("question", "answer_key", "user_answer", "feedback"):
            require(nonempty(item.get(key)), f"Assessment needs actual {key}")
        require(item.get("result") in {"pass", "partial", "fail"}, "Invalid assessment result")
        references(source_refs(item), root, source_notes)
        evidence[item["id"]] = item

    def check_ids(ids, node, unit_id):
        require(isinstance(ids, list) and len(set(ids)) == len(ids), "Invalid check IDs")
        for check_id in ids:
            require(check_id in evidence and evidence[check_id]["node"] == node
                    and evidence[check_id].get("unit_id") == unit_id,
                    f"Check does not point to an actual {node} answer for {unit_id}")
        return [evidence[check_id] for check_id in ids]

    for unit in units:
        require(nonempty(unit.get("name")), "Missing unit name")
        references(source_refs(unit), root, source_notes)
        require(unit.get("status") in UNIT_STATUSES, "Invalid unit status")
        require(type(unit.get("mastered")) is bool, "mastered must be a boolean")
        require(unit.get("support_level") in SUPPORT, "Invalid support level")
        require(type(unit.get("taught")) is bool, "taught must be a boolean")
        require(isinstance(unit.get("teaching_note"), str), "Missing teaching_note")
        require(not unit["taught"] or nonempty(unit["teaching_note"]), "Teaching needs a record")
        require(type(unit.get("stuck_count")) is int and unit["stuck_count"] >= 0,
                "Invalid stuck_count")
        target = unit.get("recall_target")
        require(type(target) is int and target > 0, "Invalid recall_target")
        require(isinstance(unit.get("recall_note"), str), "Missing recall_note")
        require(target == 2 or nonempty(unit["recall_note"]),
                "Changing the default recall count needs a reason set before testing")
        dependencies = unit.get("prerequisite_ids")
        require(isinstance(dependencies, list) and len(set(dependencies)) == len(dependencies)
                and all(uid in unit_map and uid != unit["id"] for uid in dependencies),
                "Invalid prerequisite IDs")
        checks = unit.get("checks", {})
        results = []
        for node in ("understanding", "practice"):
            require(node in checks, f"Missing {node} check")
            ids = [] if checks[node] is None else [checks[node]]
            results.extend(check_ids(ids, node, unit["id"]))
        recall = check_ids(checks.get("recall"), "recall", unit["id"])
        # One round is explicit; do not select isolated correct answers from old rounds.
        rounds = {item.get("round_id") for item in recall}
        if recall:
            require(len(rounds) == 1 and all(nonempty(r) for r in rounds),
                    "Recall checks must belong to the same round")
            whole_round = [a["id"] for a in assessments if a["node"] == "recall"
                           and a.get("unit_id") == unit["id"] and a.get("round_id") in rounds]
            require(set(whole_round) == set(checks["recall"]), "Recall checks omit round answers")
        if unit["status"] == "completed":
            require(unit["taught"] and len(results) == 2 and len(recall) == target,
                    "Completion requires teaching, understanding, practice and a full recall round")
            require(any(a["result"] == "pass" for a in recall),
                    "All-wrong recall must be needs_review")
            require(results[0]["result"] != "fail", "Core understanding failure needs review")
        if len(recall) == target and all(a["result"] == "fail" for a in recall):
            require(unit["status"] == "needs_review", "All-wrong recall triggers needs_review")
        eligible = (unit["status"] == "completed" and unit["taught"]
                    and len(results) == 2 and len(recall) == target
                    and all(a["result"] == "pass" for a in results + recall))
        require(unit["mastered"] == eligible, "mastered disagrees with actual check evidence")
        if unit["status"] == "pending":
            require(not unit["taught"] and not results and not recall,
                    "A started unit cannot be pending")
        if unit["stuck_count"] >= 3:
            require(unit["status"] == "needs_review", "Three failed attempts trigger needs_review")
        weak_spots = unit.get("weak_spots")
        require(isinstance(weak_spots, list), "weak_spots must be a list")
        for spot in weak_spots:
            require(nonempty(spot.get("description"))
                    and spot.get("certainty") in {"confirmed", "possible"}
                    and spot.get("status") in {"open", "resolved"}, "Invalid weak spot")
            ids = spot.get("assessment_ids")
            require(isinstance(ids, list) and bool(ids)
                    and all(uid in evidence and evidence[uid].get("unit_id") == unit["id"]
                            for uid in ids), "Weak spots need actual unit answer evidence")

    def visit(uid, chain):
        require(uid not in chain, "Cyclic prerequisites")
        for dependency in unit_map[uid]["prerequisite_ids"]:
            visit(dependency, chain | {uid})

    for uid in unit_map:
        visit(uid, set())

    diagnosis = data.get("diagnosis", {})
    require(type(diagnosis.get("completed")) is bool, "Missing diagnosis status")
    require(isinstance(diagnosis.get("assessment_ids"), list), "Missing diagnosis evidence")
    for aid in diagnosis["assessment_ids"]:
        require(aid in evidence and evidence[aid]["node"] == "diagnosis", "Invalid diagnosis evidence")
    if diagnosis["completed"]:
        require(nonempty(diagnosis.get("summary")) and bool(diagnosis["assessment_ids"]),
                "Completed diagnosis needs actual answers and a summary")
    session = data.get("session", {})
    require(session.get("status") in {"active", "paused", "blocked", "finished"}, "Invalid run status")
    phase = session.get("phase")
    require(phase in NODES, "Invalid phase")
    require(session.get("active_unit_id") is None or session["active_unit_id"] in unit_map,
            "Unknown active unit")
    minutes = session.get("available_minutes")
    require(minutes is None or (type(minutes) is int and minutes > 0), "Invalid available minutes")
    require(isinstance(session.get("reason"), str), "Missing session reason")
    if session["status"] in {"paused", "blocked"}:
        require(nonempty(session["reason"]), "Paused/blocked state needs a reason")
    if phase != "diagnosis":
        require(diagnosis["completed"], "Finish diagnosis before entering another node")
    active = unit_map.get(session.get("active_unit_id"))
    if phase not in {"diagnosis", "planning", "recommendation"}:
        require(active is not None, "This node requires an active unit")
    if phase in {"understanding", "practice", "recall"}:
        require(active["taught"], "Teach before checking understanding")
    if phase in {"practice", "recall"}:
        ref = active["checks"]["understanding"]
        require(ref in evidence and evidence[ref]["result"] == "pass", "Resolve understanding first")
    if phase == "recall":
        ref = active["checks"]["practice"]
        require(ref in evidence and evidence[ref]["result"] == "pass", "Pass practice before recall")
    require(session.get("repair_node") is None or session["repair_node"] in QUESTION_NODES | {"teaching"},
            "Invalid repair_node")
    if phase == "feedback":
        require(session.get("repair_node") is not None, "Feedback needs a return node")
    pending = session.get("pending_question")
    if pending is not None:
        require(isinstance(pending, dict) and nonempty(pending.get("id"))
                and pending["id"] not in evidence, "Pending question is missing or already answered")
        require(phase in QUESTION_NODES and pending.get("node") == phase,
                "Pending question must match the current node")
        require(pending.get("unit_id") == session.get("active_unit_id"), "Pending question unit mismatch")
        for key in ("question", "answer_key"):
            require(nonempty(pending.get(key)), f"Pending question needs {key}")
        references(source_refs(pending), root, source_notes)
        if phase == "recall":
            require(nonempty(pending.get("round_id")), "Recall question needs round_id")
    if session["status"] == "finished":
        require(bool(units) and all(u["mastered"] for u in units) and pending is None
                and phase == "recommendation", "Finish only after all units are mastered")
    return data


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def state_lock(path):
    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def render_index(root):
    lines = ["# 学习总览", "", "由学习状态生成；state.json 是续学依据。", "",
             "| 专题 | 掌握／总数 | 待重学 | 当前节点 | 状态 |", "|---|---|---|---|---|"]
    for path in sorted((root / "learning").glob("*/*/state.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        validate(data, root)
        name = data["topic"].replace("|", "\\|").replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
        link = path.relative_to(root / "learning").as_posix()
        mastered = sum(u["mastered"] for u in data["units"])
        review = sum(u["status"] == "needs_review" for u in data["units"])
        lines.append(f"| [{name}]({link}) | {mastered}/{len(data['units'])} | {review} | "
                     f"{data['session']['phase']} | {data['session']['status']} |")
    return "\n".join(lines) + "\n"


def save(candidate, expected_revision, root=ROOT):
    root = Path(root).resolve()
    data = deepcopy(candidate)
    validate(data, root)
    require(data["revision"] == expected_revision, "Candidate revision differs from expected revision")
    learning = root / "learning"
    require(not learning.is_symlink(), "learning must be a project-local directory")
    learning.mkdir(parents=True, exist_ok=True)
    target = learning / data["domain_slug"] / data["topic_slug"] / "state.json"
    require(target.resolve().is_relative_to(learning.resolve()), "Learning target escapes the project")
    with state_lock(learning / ".state.lock"):
        old = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        require((old["revision"] if old else 0) == expected_revision, "Stale revision; reread current state")
        if old:
            require(data["assessments"][:len(old["assessments"])] == old["assessments"],
                    "Existing answers cannot be removed or rewritten; append new evidence")
            require(all(data.get("source_notes", {}).get(key) == text
                        for key, text in old.get("source_notes", {}).items()),
                    "Existing inline sources cannot be rewritten; add a new source ID")
            old_units = {u["id"] for u in old["units"]}
            require(old_units <= {u["id"] for u in data["units"]}, "Existing units cannot silently disappear")
            pending = old["session"]["pending_question"]
            if pending and data["session"]["pending_question"] != pending:
                answer = next((a for a in data["assessments"] if a["id"] == pending["id"]), None)
                if answer:
                    for key in ("unit_id", "node", "question", "answer_key", "round_id"):
                        require(answer.get(key) == pending.get(key), "Answer must match the saved question")
                    require(source_refs(answer) == source_refs(pending), "Answer source references changed")
                else:
                    require(nonempty(data["session"]["reason"]),
                            "Changing/skipping an unanswered question needs an explicit reason")
        else:
            require(not data["assessments"] and not data["diagnosis"]["completed"]
                    and all(u["status"] == "pending" for u in data["units"]),
                    "New state starts before diagnosis; do not fabricate prior progress")
        data["revision"] += 1
        data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        atomic_write(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        try:
            atomic_write(learning / "index.md", render_index(root))
            index_status = "updated"
        except (OSError, ValueError, KeyError, TypeError) as error:
            index_status = f"failed: {error}; state saved, rebuild index after repair"
        return {"path": str(target), "revision": data["revision"], "index": index_status}


def main(default_root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "save"):
        item = sub.add_parser(command)
        item.add_argument("candidate", type=Path)
        item.add_argument("--root", type=Path, default=default_root)
        if command == "save":
            item.add_argument("--expected-revision", type=int, required=True)
    item = sub.add_parser("index")
    item.add_argument("--root", type=Path, default=default_root)
    args = parser.parse_args()
    try:
        if args.command == "index":
            learning = args.root.resolve() / "learning"
            require(learning.is_dir() and not learning.is_symlink(), "No local learning directory")
            with state_lock(learning / ".state.lock"):
                atomic_write(learning / "index.md", render_index(args.root.resolve()))
            result = {"path": str(learning / "index.md"), "status": "rebuilt"}
        else:
            data = json.loads(args.candidate.read_text(encoding="utf-8"))
            if args.command == "save":
                result = save(data, args.expected_revision, args.root)
            else:
                validate(data, args.root)
                result = {"status": "valid", "revision": data["revision"]}
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(2, f"learning_state: {error}\n")


if __name__ == "__main__":
    main()
