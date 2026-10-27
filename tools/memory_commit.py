#!/usr/bin/env python3
"""Commit an agent-prepared memory packet without calling a model."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
from pathlib import Path
import tempfile

from memory_catalog import CATEGORIES, ORIGINS, entries, read_metadata, render_index, validate_entry

ROOT = Path(__file__).resolve().parents[1]


def entry_path(root, name, legacy=False):
    parts = Path(name).parts
    categories = (*CATEGORIES, "条目") if legacy else CATEGORIES
    if len(parts) != 3 or parts[0] != "memory" or parts[1] not in categories or not parts[2].endswith(".md"):
        raise ValueError("只允许提交七类 memory 条目和索引")
    path = inside(root, name)
    if path.parent != root / "memory" / parts[1]:
        raise ValueError("非法记忆目标")
    return path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inside(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("路径超出允许目录")
    return path


def replace(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(root):
    directory = root / "data" / "memory-commits"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield directory


def commit(packet_path, root=ROOT, dry_run=False):
    root = Path(root).resolve()
    packet_path = Path(packet_path).resolve()
    packet_bytes = packet_path.read_bytes()
    packet = json.loads(packet_bytes)
    decision = packet.get("judgment", {})
    if decision.get("accepted") is not True or not decision.get("reason") or not decision.get("evidence"):
        raise ValueError("缺少已通过的识别判断、原因或用户原话证据")
    if decision.get("origin") not in ORIGINS:
        raise ValueError("识别来源必须明确")
    rules = packet.get("rules", {})
    for name in ("AGENTS.md", "memory/写入机制.md"):
        if rules.get(name) != sha((root / name).read_bytes()):
            raise ValueError("规则已变化或缺少规则指纹，请重新核对判断")
    changes = packet.get("changes", [])
    if not changes or not any(x.get("target") == "memory/索引.md" for x in changes):
        raise ValueError("提交必须同时包含条目和索引")
    prepared = []
    seen = set()
    moves = {}
    for change in changes:
        name = change["target"]
        target = inside(root, name) if name == "memory/索引.md" else entry_path(root, name)
        if target in seen or (name == "memory/索引.md" and target != root / name):
            raise ValueError("重复目标或非法目标")
        seen.add(target)
        draft = inside(packet_path.parent, change["draft"])
        data = draft.read_bytes()
        if not data.strip() or change.get("draft_sha256") != sha(data):
            raise ValueError("整理稿为空或已变化")
        if name != "memory/索引.md":
            fields = read_metadata(io.StringIO(data.decode("utf-8")))
            validate_entry(target, fields)
            if change.get("move_from"):
                source = entry_path(root, change["move_from"], legacy=True)
                if source == target or source in moves or not change.get("source_sha256"):
                    raise ValueError("迁移来源重复、无效或缺少来源指纹")
                moves[source] = (target, change["source_sha256"], fields)
            elif fields["origin"] != decision["origin"]:
                raise ValueError("整理稿与识别来源不一致")
        elif change.get("move_from"):
            raise ValueError("索引不能作为迁移条目")
        prepared.append((target, data, change.get("expected_sha256")))
    if len(prepared) < 2:
        raise ValueError("必须包含至少一个条目")
    if set(moves) & seen:
        raise ValueError("迁移来源不能同时作为写入目标")
    key = sha(packet_bytes)
    with locked(root) as directory:
        receipt = directory / f"{key}.json"
        if receipt.exists():
            if (not all(path.exists() and sha(path.read_bytes()) == sha(data) for path, data, _ in prepared)
                    or any(source.exists() for source in moves)):
                raise ValueError("该提交已执行，但目标后来有变化；请生成新的整理包")
            return {"ok": True, "status": "already_applied", "receipt": str(receipt)}
        previous = {}
        for path, _, expected in prepared:
            old = path.read_bytes() if path.exists() else None
            if (sha(old) if old is not None else None) != expected:
                raise ValueError(f"目标在整理后发生变化：{path.relative_to(root)}")
            previous[path] = old
        for source, (_, expected, fields) in moves.items():
            old = source.read_bytes()
            if sha(old) != expected:
                raise ValueError("迁移来源在整理后发生变化")
            original = read_metadata(io.StringIO(old.decode("utf-8")))
            if any(original.get(key) != fields[key] for key in ("id", "origin")):
                raise ValueError("迁移不能改变稳定 id 或来源性质")
            previous[source] = old
        overrides = {path: data for path, data, _ in prepared}
        expected_index = render_index(entries(root, overrides, moves)).encode("utf-8")
        if overrides[root / "memory/索引.md"] != expected_index:
            raise ValueError("索引的目录导航与提交后的记忆分类数量不一致")
        if dry_run:
            return {"ok": True, "status": "validated", "files": len(prepared)}
        written = []
        try:
            for path, data, _ in prepared:
                replace(path, data)
                written.append(path)
            for source in moves:
                written.append(source)
                source.unlink()
            if not all(path.read_bytes() == data for path, data, _ in prepared):
                raise RuntimeError("回读不一致")
            record = {"packet_sha256": key, "applied_at": datetime.now(timezone.utc).isoformat(),
                      "files": [{"path": str(p.relative_to(root)), "sha256": sha(d)} for p, d, _ in prepared],
                      "moves": [{"from": str(p.relative_to(root)), "to": str(t.relative_to(root)), "source_sha256": h}
                                for p, (t, h, _) in moves.items()]}
            replace(receipt, (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode())
        except Exception:
            for path in reversed(written):
                old = previous[path]
                replace(path, old) if old is not None else path.unlink(missing_ok=True)
            raise
    return {"ok": True, "status": "applied", "receipt": str(receipt), "files": record["files"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = commit(args.packet, dry_run=args.dry_run)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
