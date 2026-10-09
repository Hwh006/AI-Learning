#!/usr/bin/env python3
"""Deterministic raw-to-Wiki ingest preparation and evidence ledger.

The model-facing steps are deliberately separate: this program never infers
facts from source text or writes formal Wiki pages.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import importlib.metadata
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tools import wiki_relations
except ModuleNotFoundError:
    import wiki_relations

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "outputs/context-engineering/ingest-runs"
CHUNKER_VERSION = "3"
SCHEMA_VERSION = "1"
PROMPT_VERSION = "1"
HEADING = re.compile(r"(?m)^#{1,6}\s+.+$")
TIMESTAMP = re.compile(r"(?m)^@[^\n]+?\s+\d{1,2}:\d{2}(?::\d{2})?\s*$")
SENTENCE_END = re.compile(r"[。！？!?；;]|\.(?=\s|$)")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")
    temp.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def source_path(argument: str) -> tuple[Path, str]:
    path = (ROOT / argument).resolve()
    raw_root = (ROOT / "raw").resolve()
    if not path.is_relative_to(raw_root) or not path.is_file() or path.suffix.lower() != ".md":
        raise ValueError("source must be an existing Markdown file inside raw/")
    return path, path.relative_to(ROOT).as_posix()


def tokenize(encoding_name: str):
    try:
        import tiktoken
    except ImportError as exc:
        raise RuntimeError("install requirements-ingest.txt; exact token counting is required") from exc
    return tiktoken.get_encoding(encoding_name)


def token_count(encoding: Any, content: str) -> int:
    return len(encoding.encode(content, disallowed_special=()))


def line_starts(content: str) -> list[int]:
    return [0] + [match.end() for match in re.finditer("\n", content)]


def locate(content: str, starts: list[int], start: int, end: int) -> dict[str, int]:
    return {
        "char_start": start,
        "char_end": end,
        "byte_start": len(content[:start].encode("utf-8")),
        "byte_end": len(content[:end].encode("utf-8")),
        "line_start": bisect.bisect_right(starts, start),
        "line_end": bisect.bisect_right(starts, max(start, end - 1)),
    }


def without_fenced_code(content: str) -> str:
    masked = []
    fence = None
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            candidate = marker.group(1)[0]
            if fence is None:
                fence = candidate
            elif fence == candidate:
                fence = None
            masked.append("".join("\n" if char == "\n" else " " for char in line))
        elif fence is not None:
            masked.append("".join("\n" if char == "\n" else " " for char in line))
        else:
            masked.append(line)
    return "".join(masked)


def structural_boundaries(content: str) -> tuple[str, list[tuple[int, str]], str]:
    scan = without_fenced_code(content)
    headings = [(match.start(), match.group().strip()) for match in HEADING.finditer(scan)]
    if headings:
        return "structured", headings, f"{len(headings)} Markdown headings"
    return "unstructured", [], "no Markdown headings"


def preferred_boundaries(content: str, start: int, end: int) -> list[int]:
    points = {end}
    for match in re.finditer(r"\n\s*\n|\n", content[start:end]):
        points.add(start + match.end())
    for match in SENTENCE_END.finditer(content, start, end):
        points.add(match.end())
    return sorted(point for point in points if start < point <= end)


def split_to_budget(content: str, start: int, end: int, encoding: Any, limit: int) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    points = preferred_boundaries(content, start, end)
    current = start
    while current < end:
        if token_count(encoding, content[current:end]) <= limit:
            pieces.append((current, end))
            break
        candidates = [point for point in points if point > current]
        low, high = 0, len(candidates) - 1
        chosen = None
        while low <= high:
            middle = (low + high) // 2
            point = candidates[middle]
            if token_count(encoding, content[current:point]) <= limit:
                chosen = point
                low = middle + 1
            else:
                high = middle - 1
        if chosen is None:
            # A single sentence can exceed the model budget. Keep its exact
            # locator and let the caller mark it for review rather than cut it.
            chosen = candidates[0]
        pieces.append((current, chosen))
        current = chosen
    return pieces


def sections(content: str, markers: list[tuple[int, str]]) -> list[tuple[int, int, list[str]]]:
    if not markers:
        return [(0, len(content), [])]
    result: list[tuple[int, int, list[str]]] = []
    heading_stack: list[str] = []
    if markers[0][0] > 0:
        result.append((0, markers[0][0], []))
    for index, (start, label) in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(content)
        if label.startswith("#"):
            depth = len(label) - len(label.lstrip("#"))
            heading_stack = heading_stack[: depth - 1] + [label.lstrip("# ")]
            path = heading_stack.copy()
        else:
            path = [label]
        result.append((start, end, path))
    return result


def make_units(content: str, relative: str, encoding: Any, unstructured_tokens: int, model_tokens: int):
    mode, markers, reason = structural_boundaries(content)
    spans: list[tuple[int, int, list[str], str]] = []
    if mode == "structured":
        for start, end, path in sections(content, markers):
            if start == end:
                continue
            for piece_start, piece_end in split_to_budget(content, start, end, encoding, model_tokens):
                spans.append((piece_start, piece_end, path, "structure"))
    else:
        for start, end in split_to_budget(content, 0, len(content), encoding, unstructured_tokens):
            spans.append((start, end, [], "token_budget"))
    starts = line_starts(content)
    source_id = sha256(relative.encode("utf-8"))[:16]
    units = []
    for index, (start, end, path, basis) in enumerate(spans, 1):
        piece = content[start:end]
        count = token_count(encoding, piece)
        unit_id = f"{source_id}-u{index:05d}-{sha256(piece.encode('utf-8'))[:8]}"
        units.append({
            "unit_id": unit_id,
            "ordinal": index,
            "unit_type": "section" if mode == "structured" else "text_block",
            "split_basis": basis,
            "heading_path": path,
            "locator": locate(content, starts, start, end),
            "unit_sha256": sha256(piece.encode("utf-8")),
            "token_count": count,
            "status": "needs_review" if count > (model_tokens if mode == "structured" else unstructured_tokens) else "queued",
            "error": "single structure/sentence exceeds token budget" if count > (model_tokens if mode == "structured" else unstructured_tokens) else None,
        })
    for index, unit in enumerate(units):
        unit["previous_unit_id"] = units[index - 1]["unit_id"] if index else None
        unit["next_unit_id"] = units[index + 1]["unit_id"] if index + 1 < len(units) else None
    return mode, reason, source_id, units


def check_coverage(content: str, units: list[dict[str, Any]]) -> list[str]:
    errors = []
    cursor = 0
    starts = line_starts(content)
    for unit in units:
        locator = unit["locator"]
        start, end = locator["char_start"], locator["char_end"]
        if start != cursor or end <= start or end > len(content):
            errors.append(f"gap/overlap at {unit['unit_id']}")
        piece = content[start:end]
        if sha256(piece.encode("utf-8")) != unit["unit_sha256"]:
            errors.append(f"hash mismatch at {unit['unit_id']}")
        if locator != locate(content, starts, start, end):
            errors.append(f"locator mismatch at {unit['unit_id']}")
        cursor = end
    if cursor != len(content):
        errors.append(f"uncovered tail from char {cursor}")
    return errors


def source_run_root(relative: str, digest: str) -> Path:
    return RUNS / sha256(relative.encode("utf-8"))[:16] / digest


def config_key(config: dict[str, Any]) -> str:
    return sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8"))[:16]


def registration(relative: str, digest: str) -> dict[str, Any]:
    matches = []
    for path in (ROOT / "wiki/sources").glob("*.md"):
        text = path.read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)
        if len(frontmatter) < 3 or frontmatter[0].strip():
            continue
        source = re.search(r"(?m)^source_path:\s*(.+?)\s*$", frontmatter[1])
        hashed = re.search(r"(?m)^sha256:\s*([a-f0-9]{64})\s*$", frontmatter[1])
        if source and source.group(1).strip('"\'') == relative:
            matches.append({"page": path.relative_to(ROOT).as_posix(),
                            "sha256": hashed.group(1) if hashed else None})
    if len(matches) > 1:
        return {"status": "needs_review", "reason": "multiple source pages share source_path", "matches": matches}
    if not matches:
        return {"status": "new_source", "matches": []}
    if matches[0]["sha256"] != digest:
        return {"status": "changed_source", "matches": matches}
    verification = verify_source(argparse.Namespace(page=matches[0]["page"]))
    if verification["status"] != "passed":
        return {"status": "needs_review", "reason": "existing source page fails current source rules",
                "verification_errors": verification["errors"], "matches": matches}
    return {"status": "same_hash", "matches": matches}


def privacy_counts(content: str) -> dict[str, int]:
    return {"emails": len(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", content)),
            "phone_candidates": len(re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", content))}


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    path, relative = source_path(args.source)
    raw = path.read_bytes()
    content = raw.decode("utf-8")
    digest = sha256(raw)
    encoding = tokenize(args.encoding)
    mode, reason, source_id, units = make_units(content, relative, encoding, args.chunk_tokens, args.model_tokens)
    errors = check_coverage(content, units)
    if errors:
        raise RuntimeError("; ".join(errors))
    config = {"chunker_version": CHUNKER_VERSION, "schema_version": SCHEMA_VERSION, "encoding": args.encoding,
              "tokenizer_package_version": importlib.metadata.version("tiktoken"),
              "prompt_version": PROMPT_VERSION, "model_id": args.model_id,
              "chunk_tokens": args.chunk_tokens, "model_tokens": args.model_tokens}
    source_root = source_run_root(relative, digest)
    directory = source_root / config_key(config)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        if old.get("config") == config and old.get("source_sha256") == digest:
            old["registration"] = registration(relative, digest)
            write_json(manifest_path, old)
            write_json(source_root / "current.json", {"config_key": config_key(config)})
            return {"result": "reused", "run_dir": str(directory), "unit_count": old["unit_count"],
                    "mode": old["mode"], "registration": old["registration"]}
        raise RuntimeError("run directory has a conflicting manifest")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source_id": source_id, "source_path": relative, "source_sha256": digest,
        "mode": mode, "classification_reason": reason, "config": config,
        "registration": registration(relative, digest), "privacy_candidates": privacy_counts(content),
        "unit_count": len(units), "queued_count": sum(unit["status"] == "queued" for unit in units),
        "needs_review_count": sum(unit["status"] == "needs_review" for unit in units),
        "status": "needs_review" if any(unit["status"] == "needs_review" for unit in units) else "mapped",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_jsonl(directory / "units.jsonl", units)
    write_json(manifest_path, manifest)
    write_json(source_root / "current.json", {"config_key": config_key(config)})
    return {"result": "created", "run_dir": str(directory), "unit_count": len(units), "mode": mode,
            "needs_review_count": manifest["needs_review_count"], "registration": manifest["registration"]}


def load_run(source: str) -> tuple[Path, str, Path, dict[str, Any], list[dict[str, Any]]]:
    path, relative = source_path(source)
    raw = path.read_bytes()
    digest = sha256(raw)
    source_root = source_run_root(relative, digest)
    pointer = source_root / "current.json"
    if not pointer.exists():
        raise RuntimeError("no current run for source hash; run prepare first")
    key = json.loads(pointer.read_text(encoding="utf-8"))["config_key"]
    if not re.fullmatch(r"[a-f0-9]{16}", key):
        raise RuntimeError("invalid run pointer")
    directory = source_root / key
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("no run for current source hash; run prepare first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    units = read_jsonl(directory / "units.jsonl")
    if manifest["source_sha256"] != digest or manifest["source_path"] != relative:
        raise RuntimeError("run/source mismatch")
    content = raw.decode("utf-8")
    if manifest["status"] != "ready" and units and not manifest.get("needs_review_count"):
        summaries = read_jsonl(directory / "summaries.jsonl")
        if len(summaries) == len(units) and not audit_run(content, manifest, units, summaries):
            # Recover if the summaries were atomically written before a crash
            # prevented the manifest update.
            manifest["summarized_count"] = len(summaries)
            manifest["status"] = "ready"
            write_json(manifest_path, manifest)
    return path, content, directory, manifest, units


def inspect(args: argparse.Namespace) -> dict[str, Any]:
    _, content, directory, manifest, units = load_run(args.source)
    summaries = read_jsonl(directory / "summaries.jsonl")
    errors = audit_run(content, manifest, units, summaries)
    if args.unit_id:
        unit = next((item for item in units if item["unit_id"] == args.unit_id), None)
        if unit is None:
            raise ValueError("unknown unit ID")
        locator = unit["locator"]
        return {"unit": unit, "text": content[locator["char_start"]:locator["char_end"]]}
    status = {item["unit_id"]: item for item in summaries}
    return {"run_dir": str(directory), "mode": manifest["mode"], "registration": manifest["registration"],
            "unit_count": len(units),
            "summary_count": len(summaries), "coverage_errors": errors,
            "pending_unit_ids": [unit["unit_id"] for unit in units if unit["unit_id"] not in status],
            "needs_review_unit_ids": [unit["unit_id"] for unit in units if unit["status"] == "needs_review"]}


ALLOWED_TYPES = {"concept", "method", "project_design", "comparison", "process", "rule", "case", "entity_mention", "uncertainty"}


def audit_run(content: str, manifest: dict[str, Any], units: list[dict[str, Any]],
              summaries: list[dict[str, Any]]) -> list[str]:
    errors = check_coverage(content, units)
    starts = line_starts(content)
    unit_by_id = {unit["unit_id"]: unit for unit in units}
    seen = set()
    for summary in summaries:
        unit_id = summary.get("unit_id")
        unit = unit_by_id.get(unit_id)
        if unit_id in seen:
            errors.append(f"duplicate summary at {unit_id}")
        seen.add(unit_id)
        if unit is None or summary.get("unit_sha256") != unit["unit_sha256"]:
            errors.append(f"stale or unknown summary at {unit_id}")
            continue
        if (summary.get("status") != "summarized" or summary.get("schema_version") != manifest["config"]["schema_version"]
                or summary.get("prompt_version") != manifest["config"]["prompt_version"]
                or summary.get("model_id") != manifest["config"]["model_id"]):
            errors.append(f"summary version/status mismatch at {unit_id}")
        if not isinstance(summary.get("items"), list):
            errors.append(f"invalid summary items at {unit_id}")
            continue
        for item in summary["items"]:
            if not isinstance(item, dict) or item.get("type") not in ALLOWED_TYPES:
                errors.append(f"invalid knowledge item at {unit_id}")
                continue
            if not isinstance(item.get("evidence"), list) or not item["evidence"]:
                errors.append(f"missing evidence at {unit_id}")
                continue
            for evidence in item["evidence"]:
                if not isinstance(evidence, dict):
                    errors.append(f"invalid evidence at {unit_id}")
                    continue
                quote = evidence.get("quote")
                position = evidence.get("locator", {})
                if not isinstance(quote, str) or not isinstance(position, dict):
                    errors.append(f"invalid evidence at {unit_id}")
                    continue
                start, end = position.get("char_start"), position.get("char_end")
                if not isinstance(start, int) or not isinstance(end, int) or not (
                    unit["locator"]["char_start"] <= start < end <= unit["locator"]["char_end"]
                    and content[start:end] == quote
                    and locate(content, starts, start, end) == position
                ):
                    errors.append(f"stale evidence at {unit_id}")
    if manifest["status"] == "ready" and (len(seen) != len(units) or manifest.get("needs_review_count")):
        errors.append("ready manifest does not cover all units")
    return errors


def require_ready(content: str, directory: Path, manifest: dict[str, Any],
                  units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if manifest["status"] != "ready":
        raise RuntimeError("summary index is incomplete; finish or review all units before Wiki planning")
    summaries = read_jsonl(directory / "summaries.jsonl")
    errors = audit_run(content, manifest, units, summaries)
    if errors:
        raise RuntimeError("summary index failed audit: " + "; ".join(errors[:5]))
    return summaries


def validate_summary(payload: dict[str, Any], unit: dict[str, Any], content: str) -> dict[str, Any]:
    if payload.get("unit_id") != unit["unit_id"]:
        raise ValueError("summary unit_id does not match selected unit")
    if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
        raise ValueError("summary must be nonempty text")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("items must be an array; use [] if no knowledge item appears")
    locator = unit["locator"]
    unit_text = content[locator["char_start"]:locator["char_end"]]
    normalized_items = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("type") not in ALLOWED_TYPES:
            raise ValueError(f"item {index}: invalid type")
        if not isinstance(item.get("topic"), str) or not item["topic"].strip():
            raise ValueError(f"item {index}: topic is required")
        if not isinstance(item.get("statement"), str) or not item["statement"].strip():
            raise ValueError(f"item {index}: statement is required")
        quotes = item.get("quotes")
        if not isinstance(quotes, list) or not quotes:
            raise ValueError(f"item {index}: at least one exact quote is required")
        evidence = []
        for quote in quotes:
            if not isinstance(quote, str) or len(quote.strip()) < 8:
                raise ValueError(f"item {index}: quote must contain at least 8 characters")
            offset = unit_text.find(quote)
            if offset < 0:
                raise ValueError(f"item {index}: quote not found verbatim in raw unit")
            if unit_text.find(quote, offset + 1) >= 0:
                raise ValueError(f"item {index}: quote is ambiguous within unit; choose a longer excerpt")
            absolute = locator["char_start"] + offset
            evidence.append({"quote": quote, "locator": locate(content, line_starts(content), absolute, absolute + len(quote))})
        normalized = {key: value for key, value in item.items() if key != "quotes"}
        normalized["evidence"] = evidence
        normalized_items.append(normalized)
    return {"unit_id": unit["unit_id"], "unit_sha256": unit["unit_sha256"],
            "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
            "summary": payload["summary"].strip(), "items": normalized_items,
            "status": "summarized", "recorded_at": datetime.now(timezone.utc).isoformat()}


def record(args: argparse.Namespace) -> dict[str, Any]:
    _, content, directory, manifest, units = load_run(args.source)
    unit = next((item for item in units if item["unit_id"] == args.unit_id), None)
    if unit is None:
        raise ValueError("unknown unit ID")
    if unit["status"] == "needs_review":
        raise ValueError("unit needs review before summary can be recorded")
    payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    normalized = validate_summary(payload, unit, content)
    normalized["model_id"] = manifest["config"]["model_id"]
    path = directory / "summaries.jsonl"
    old = read_jsonl(path)
    old_by_id = {item["unit_id"]: item for item in old}
    if args.unit_id in old_by_id and not args.replace:
        raise ValueError("summary already exists; use --replace for an explicit correction")
    old_by_id[args.unit_id] = normalized
    ordered = [old_by_id[item["unit_id"]] for item in units if item["unit_id"] in old_by_id]
    write_jsonl(path, ordered)
    manifest["summarized_count"] = len(ordered)
    manifest["status"] = "ready" if len(ordered) == len(units) and not manifest["needs_review_count"] else "summarizing"
    write_json(directory / "manifest.json", manifest)
    return {"recorded": args.unit_id, "summarized_count": len(ordered), "unit_count": len(units),
            "run_status": manifest["status"]}


def next_unit(args: argparse.Namespace) -> dict[str, Any]:
    _, content, directory, manifest, units = load_run(args.source)
    done = {item["unit_id"] for item in read_jsonl(directory / "summaries.jsonl")}
    for unit in units:
        if unit["unit_id"] not in done and unit["status"] != "needs_review":
            locator = unit["locator"]
            return {"source_path": manifest["source_path"], "source_sha256": manifest["source_sha256"],
                    "unit": unit, "text": content[locator["char_start"]:locator["char_end"]],
                    "output_schema": {"unit_id": unit["unit_id"], "summary": "one concise navigation summary",
                                      "items": [{"type": "concept|method|project_design|comparison|process|rule|case|entity_mention|uncertainty",
                                                 "topic": "topic", "statement": "source-attributed claim or clue",
                                                 "quotes": ["continuous verbatim excerpt of at least 8 characters"]}]}}
    return {"result": "no queued units", "run_status": manifest["status"]}


def outline(args: argparse.Namespace) -> dict[str, Any]:
    _, content, directory, manifest, units = load_run(args.source)
    summaries = {item["unit_id"]: item for item in read_jsonl(directory / "summaries.jsonl")}
    return {"source_path": manifest["source_path"], "source_sha256": manifest["source_sha256"],
            "classification_reason": manifest["classification_reason"], "mode": manifest["mode"],
            "units": [{"unit_id": unit["unit_id"], "heading_path": unit["heading_path"],
                       "locator": unit["locator"], "token_count": unit["token_count"],
                       "status": "summarized" if unit["unit_id"] in summaries else unit["status"],
                       "topics": [item["topic"] for item in summaries.get(unit["unit_id"], {}).get("items", [])]}
                      for unit in units]}


def find_evidence(args: argparse.Namespace) -> dict[str, Any]:
    _, content, directory, manifest, units = load_run(args.source)
    summaries = {item["unit_id"]: item for item in require_ready(content, directory, manifest, units)}
    terms = [term.casefold() for term in args.query.split() if term]
    matches = []
    for unit in units:
        summary = summaries.get(unit["unit_id"])
        if not summary:
            continue
        haystack = json.dumps({"heading_path": unit["heading_path"], "summary": summary["summary"],
                               "items": summary["items"]}, ensure_ascii=False).casefold()
        if terms and not all(term in haystack for term in terms):
            continue
        locator = unit["locator"]
        matches.append({"unit": unit, "summary": summary,
                        "raw_text": content[locator["char_start"]:locator["char_end"]] if args.include_raw else None})
    return {"source_path": manifest["source_path"], "source_sha256": manifest["source_sha256"],
            "query": args.query, "matches": matches}


PAGE_DIRECTORIES = {
    "source": "sources", "concept": "concepts", "comparison": "comparisons",
    "solution": "solutions", "entity": "entities", "synthesis": "synthesis",
    "note": "notes",
}
REQUIRED_INGEST_PAGE_TYPES = {
    "source", "concept", "solution", "comparison", "entity", "synthesis", "note",
}


def record_plan(args: argparse.Namespace) -> dict[str, Any]:
    _, content, directory, manifest, units = load_run(args.source)
    require_ready(content, directory, manifest, units)
    payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("plan needs nonempty pages array")
    previous_path = directory / "page-plan.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    continuing_creates = {p["target_path"] for p in previous.get("pages", [])
                          if p.get("action") == "create"} if previous.get("source_sha256") == manifest["source_sha256"] else set()
    known_ids = {unit["unit_id"] for unit in units}
    planned_types = {page.get("page_type") for page in pages if isinstance(page, dict)}
    missing_types = sorted(REQUIRED_INGEST_PAGE_TYPES - planned_types)
    if missing_types:
        raise ValueError("page plan must decide each ingest page type; missing: " + ", ".join(missing_types))
    if manifest["registration"]["status"] == "needs_review" and not any(
        page.get("page_type") == "source" and page.get("action") == "review" for page in pages
    ):
        raise ValueError("existing source page fails current rules; include a source review decision")
    for index, page in enumerate(pages):
        page_type = page.get("page_type")
        action = page.get("action")
        target = page.get("target_path")
        if page_type not in PAGE_DIRECTORIES or action not in {"create", "merge", "skip", "review"}:
            raise ValueError(f"page {index}: invalid page type or action")
        if not isinstance(page.get("reason"), str) or not page["reason"].strip():
            raise ValueError(f"page {index}: a reason is required for every planned action")
        if not isinstance(target, str) or not target.startswith(f"wiki/{PAGE_DIRECTORIES[page_type]}/") or not target.endswith(".md"):
            raise ValueError(f"page {index}: target path must be under the matching Wiki directory")
        resolved = (ROOT / target).resolve()
        if not resolved.is_relative_to((ROOT / "wiki").resolve()):
            raise ValueError(f"page {index}: target escapes Wiki")
        requirements = page.get("evidence_requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError(f"page {index}: evidence requirements are required")
        for requirement in requirements:
            if requirement.get("coverage") not in {"covered", "partial", "not_found", "conflict"}:
                raise ValueError(f"page {index}: invalid coverage state")
            ids = requirement.get("unit_ids")
            if not isinstance(ids, list) or not set(ids).issubset(known_ids):
                raise ValueError(f"page {index}: unknown unit ID in evidence requirement")
            if requirement["coverage"] in {"covered", "partial"} and not ids:
                raise ValueError(f"page {index}: covered evidence needs at least one unit ID")
            if not isinstance(requirement.get("question"), str) or not requirement["question"].strip():
                raise ValueError(f"page {index}: evidence question is required")
        if action == "create" and resolved.exists() and target not in continuing_creates:
            raise ValueError(f"page {index}: target already exists; use merge or review")
        if action == "merge" and not resolved.exists():
            raise ValueError(f"page {index}: merge target does not exist")
    scan = wiki_relations.retain_strong(wiki_relations.candidates(payload, ROOT),
                                       previous.get("relation_candidates", {}))
    relation_errors = wiki_relations.validate_decisions(payload, scan, ROOT)
    if relation_errors:
        raise ValueError("relation plan invalid: " + "; ".join(relation_errors))
    protected = {**wiki_relations.baselines(payload, ROOT), **previous.get("verified_baselines", {})}
    for path, digest in protected.items():
        if not (ROOT / path).is_file() or sha256((ROOT / path).read_bytes()) != digest:
            raise ValueError(f"verified page changed: {path}")
    plan = {"source_path": manifest["source_path"], "source_sha256": manifest["source_sha256"],
            "summary_sha256": sha256((directory / "summaries.jsonl").read_bytes()),
            "schema_version": SCHEMA_VERSION, "pages": pages,
            "relations_version": wiki_relations.VERSION, "relation_candidates": scan,
            "verified_baselines": protected,
            "recorded_at": datetime.now(timezone.utc).isoformat()}
    write_json(directory / "page-plan.json", plan)
    return {"recorded": str(directory / "page-plan.json"), "page_count": len(pages)}


def verify_plan(args: argparse.Namespace) -> dict[str, Any]:
    _, content, directory, manifest, units = load_run(args.source)
    require_ready(content, directory, manifest, units)
    path = directory / "page-plan.json"
    if not path.exists():
        raise RuntimeError("no page plan recorded")
    plan = json.loads(path.read_text(encoding="utf-8"))
    expected_summary_hash = sha256((directory / "summaries.jsonl").read_bytes())
    if (plan.get("source_sha256") != manifest["source_sha256"]
            or plan.get("summary_sha256") != expected_summary_hash):
        raise RuntimeError("page plan is stale; record it again")
    errors = []
    checked = []
    pages = plan.get("pages", [])
    planned_types = {page.get("page_type") for page in pages if isinstance(page, dict)}
    missing_types = sorted(REQUIRED_INGEST_PAGE_TYPES - planned_types)
    if missing_types:
        errors.append("page plan omits ingest page types: " + ", ".join(missing_types))
    for index, page in enumerate(pages):
        page_type, action, target = page.get("page_type"), page.get("action"), page.get("target_path")
        if page_type not in PAGE_DIRECTORIES or action not in {"create", "merge", "skip", "review"}:
            errors.append(f"page {index}: invalid type/action")
            continue
        if (not isinstance(target, str)
                or not target.startswith(f"wiki/{PAGE_DIRECTORIES[page_type]}/")
                or not target.endswith(".md")):
            errors.append(f"page {index}: target is outside the expected Wiki page directory")
            continue
        if not isinstance(page.get("reason"), str) or not page["reason"].strip():
            errors.append(f"page {index}: missing action reason")
        resolved = (ROOT / target).resolve()
        if not resolved.is_relative_to((ROOT / "wiki").resolve()):
            errors.append(f"page {index}: target escapes Wiki")
            continue
        exists = resolved.is_file()
        if action in {"create", "merge"} and not exists:
            errors.append(f"page {index}: planned {action} output is missing: {target}")
        reason = page.get("reason")
        has_reason = isinstance(reason, str) and bool(reason.strip())
        if action == "skip" and not has_reason:
            errors.append(f"page {index}: skip needs a concrete reason")
        if action == "review" and not has_reason:
            errors.append(f"page {index}: review needs a concrete blocking reason")
        checked.append({"page_type": page_type, "action": action, "target_path": target,
                        "exists": exists, "reason": page.get("reason")})
    if not plan.get("pages"):
        errors.append("page plan contains no page decisions")
    relations = None
    if plan.get("relations_version") != wiki_relations.VERSION:
        errors.append("legacy page plan lacks relationship decisions; run candidates and record-plan again")
    else:
        relations = wiki_relations.verify(plan, ROOT)
        errors.extend(relations["errors"])
    pending = bool(relations and relations["pending_relations"])
    return {"status": "failed" if errors else "needs_review" if pending else "passed",
            "complete": not errors and not pending, "page_count": len(checked),
            "errors": errors, "pages": checked, "relations": relations,
            "note": "Run page-type validators and Agent review of relationship evidence and reading paths."}


def context(args: argparse.Namespace) -> dict[str, Any]:
    _, content, directory, manifest, units = load_run(args.source)
    require_ready(content, directory, manifest, units)
    if not (directory / "page-plan.json").exists():
        raise RuntimeError("complete summaries and record a page plan before loading Wiki context")
    plan = json.loads((directory / "page-plan.json").read_text(encoding="utf-8"))
    if (plan["source_sha256"] != manifest["source_sha256"]
            or plan.get("summary_sha256") != sha256((directory / "summaries.jsonl").read_bytes())):
        raise RuntimeError("page plan is stale")
    index = next((i for i, unit in enumerate(units) if unit["unit_id"] == args.unit_id), None)
    if index is None:
        raise ValueError("unknown unit ID")
    planned_ids = {unit_id for page in plan["pages"] for requirement in page["evidence_requirements"]
                   for unit_id in requirement["unit_ids"]}
    if args.unit_id not in planned_ids:
        raise ValueError("unit ID is not in the saved evidence plan")
    chosen = units[max(0, index - args.adjacent):min(len(units), index + args.adjacent + 1)]
    return {"source_path": manifest["source_path"], "source_sha256": manifest["source_sha256"],
            "units": [{"unit": unit,
                       "raw_text": content[unit["locator"]["char_start"]:unit["locator"]["char_end"]]}
                      for unit in chosen]}


def verify_source(args: argparse.Namespace) -> dict[str, Any]:
    page = (ROOT / args.page).resolve()
    if not page.is_file() or not page.is_relative_to((ROOT / "wiki/sources").resolve()):
        raise ValueError("page must be an existing file in wiki/sources/")
    text = page.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3 or parts[0].strip():
        raise ValueError("source page needs YAML frontmatter")
    frontmatter = parts[1]
    fields = {}
    for key in ("type", "title", "source_path", "sha256", "material_type", "reliability", "ingested_at", "status", "tags"):
        match = re.search(rf"(?m)^{key}:\s*(.*?)\s*$", frontmatter)
        if match:
            fields[key] = match.group(1).strip('"\'')
    errors = [f"missing {key}" for key in ("type", "title", "source_path", "sha256", "material_type",
                                               "reliability", "ingested_at", "status", "tags") if not fields.get(key)]
    if fields.get("type") != "source":
        errors.append("type must be source")
    first_h1 = re.search(r"(?m)^#\s+(.+?)\s*$", parts[2])
    if fields.get("title") and (not first_h1 or first_h1.group(1) != fields["title"]):
        errors.append("H1 must match frontmatter title")
    if fields.get("tags") == "[]":
        errors.append("tags must include at least one topic")
    raw_content = ""
    if fields.get("source_path"):
        try:
            raw_path, _ = source_path(fields["source_path"])
            raw = raw_path.read_bytes()
            raw_content = raw.decode("utf-8")
            if fields.get("sha256") != sha256(raw):
                errors.append("source SHA-256 mismatch")
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(str(exc))
    quotes = []
    normalized_raw = re.sub(r"\s+", "", raw_content)
    for line in parts[2].splitlines():
        if re.match(r"^\s*(?:-\s*)?「", line):
            quote_text = line.split("〔", 1)[0]
            line_quotes = re.findall(r"「(.+?)」", quote_text)
            if line_quotes:
                if "〔" not in line or "〕" not in line:
                    errors.append("evidence quote lacks locator")
                for quote in line_quotes:
                    quotes.append(quote)
                    if raw_content and re.sub(r"\s+", "", quote) not in normalized_raw:
                        errors.append(f"quote not verbatim: {quote[:24]}")
    if not quotes:
        errors.append("no source evidence quotes found")
    return {"page": page.relative_to(ROOT).as_posix(), "quote_count": len(quotes),
            "errors": errors, "status": "passed" if not errors else "failed"}


def locate_quote(args: argparse.Namespace) -> dict[str, Any]:
    path, relative = source_path(args.source)
    content = path.read_text(encoding="utf-8")
    quote = Path(args.quote_file).read_text(encoding="utf-8").rstrip("\r\n")
    if len(quote) < 8:
        raise ValueError("quote must contain at least 8 characters")
    starts = line_starts(content)
    markers = [(match.start(), match.group().strip()) for match in TIMESTAMP.finditer(content)]
    headings = [(match.start(), match.group().strip()) for match in HEADING.finditer(content)]
    matches = []
    offset = 0
    while (offset := content.find(quote, offset)) >= 0:
        line_number = bisect.bisect_right(starts, offset)
        nonempty = [line for line in content.splitlines()[:line_number] if line.strip()]
        paragraph = len(nonempty)
        first = nonempty[-1].strip()[:10] if nonempty else ""
        time_marker = next((label for position, label in reversed(markers) if position <= offset), None)
        heading = next((label for position, label in reversed(headings) if position <= offset), None)
        matches.append({"locator": locate(content, starts, offset, offset + len(quote)),
                        "paragraph_label": f"第 {paragraph} 段「{first}…」",
                        "timestamp_label": time_marker, "heading_label": heading})
        offset += len(quote)
    return {"source_path": relative, "quote": quote, "matches": matches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="classify, chunk and create a source run")
    prep.add_argument("source")
    prep.add_argument("--encoding", default="o200k_base")
    prep.add_argument("--chunk-tokens", type=int, default=20000)
    prep.add_argument("--model-tokens", type=int, default=20000)
    prep.add_argument("--model-id", required=True, help="model/runtime version or unique run identity for summary cache invalidation")
    view = sub.add_parser("inspect", help="check coverage or read an exact unit")
    view.add_argument("source")
    view.add_argument("--unit-id")
    nxt = sub.add_parser("next", help="get the next unit and model output schema")
    nxt.add_argument("source")
    out = sub.add_parser("outline", help="list all structural units and indexed topic distribution")
    out.add_argument("source")
    rec = sub.add_parser("record", help="validate and record a model summary JSON")
    rec.add_argument("source")
    rec.add_argument("--unit-id", required=True)
    rec.add_argument("--json-file", required=True)
    rec.add_argument("--replace", action="store_true")
    find = sub.add_parser("find", help="find indexed knowledge items and optionally reload raw")
    find.add_argument("source")
    find.add_argument("--query", required=True)
    find.add_argument("--include-raw", action="store_true")
    plan = sub.add_parser("record-plan", help="validate and save the model's page/evidence plan")
    plan.add_argument("source")
    plan.add_argument("--json-file", required=True)
    plan_check = sub.add_parser("verify-plan", help="check planned Wiki outputs exist or have explicit skip/review reasons")
    plan_check.add_argument("source")
    ctx = sub.add_parser("context", help="reload planned raw unit and adjacent context")
    ctx.add_argument("source")
    ctx.add_argument("--unit-id", required=True)
    ctx.add_argument("--adjacent", type=int, default=1)
    verify = sub.add_parser("verify-source", help="check a formal source page against raw evidence")
    verify.add_argument("page")
    quote = sub.add_parser("locate-quote", help="locate an exact quote for a source page")
    quote.add_argument("source")
    quote.add_argument("--quote-file", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            if args.chunk_tokens < 1 or args.model_tokens < 1:
                raise ValueError("token limits must be positive")
            result = prepare(args)
        elif args.command == "inspect":
            result = inspect(args)
        elif args.command == "next":
            result = next_unit(args)
        elif args.command == "outline":
            result = outline(args)
        elif args.command == "record":
            result = record(args)
        elif args.command == "find":
            result = find_evidence(args)
        elif args.command == "record-plan":
            result = record_plan(args)
        elif args.command == "verify-plan":
            result = verify_plan(args)
        elif args.command == "context":
            if args.adjacent < 0 or args.adjacent > 4:
                raise ValueError("adjacent must be between 0 and 4")
            result = context(args)
        elif args.command == "verify-source":
            result = verify_source(args)
        else:
            result = locate_quote(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("status") in {"failed", "needs_review"} else 0
    except (ValueError, RuntimeError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
