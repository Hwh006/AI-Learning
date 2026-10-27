#!/usr/bin/env python3
"""Snapshot Wiki changes and verify Agent-authored Map placements; never classify or edit pages."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from tools import wiki_relations as wr
except ModuleNotFoundError:
    import wiki_relations as wr

ROOT = Path(__file__).resolve().parents[1]
TYPES = {"concept", "note", "solution", "comparison", "entity", "synthesis", "query"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan(root):
    catalog = wr.catalog(root)
    index = wr.name_index(catalog)
    eligible = {p: m for p, m in catalog.items() if m["type"] in TYPES}
    maps = {}
    placements = []
    errors = []
    heading_cache = {}

    def headings(path):
        if path not in heading_cache:
            heading_cache[path] = [re.sub(r"^#{1,6}\s+", "", line).rstrip(" #")
                                   for _, line in wr.visible_lines((root / path).read_text())
                                   if re.match(r"^#{1,6}\s+", line)]
        return heading_cache[path]

    for file in sorted((root / "wiki/map").glob("*.md")):
        path = file.relative_to(root).as_posix()
        meta = catalog.get(path, {})
        if meta.get("type") != "map":
            errors.append(f"not an active map: {path}")
        linked = set()
        for section, target, kind in wr.links(file.read_text()):
            dest, anchor, state = wr.resolve(target, kind, path, catalog, root, index)
            if state == "external":
                continue
            if state != "local" or not (root / dest).is_file():
                errors.append(f"broken link: {path}: {target}")
                continue
            if anchor:
                hs = headings(dest)
                slugs = {re.sub(r"[^\w\s-]", "", h.lower()).replace(" ", "-") for h in hs}
                if anchor not in hs and anchor not in slugs:
                    errors.append(f"broken anchor: {path}: {target}")
            if dest in eligible:
                linked.add(dest)
                placements.append({"page": dest, "map": path, "heading": section})
            elif dest in catalog and catalog[dest]["type"] == "map":
                continue
            else:
                errors.append(f"excluded target in map: {path}: {target}")
        maps[path] = {"sha256": digest(file), "pages": sorted(linked),
                      "headings": headings(path), "declared_count": meta.get("coverage_pages")}
    mapped = {x["page"] for x in placements}
    total = "wiki/map/知识总地图.md"
    for path, meta in maps.items():
        count = len(mapped) if path == total else len(meta["pages"])
        if str(meta["declared_count"]) != str(count):
            errors.append(f"coverage_pages mismatch: {path}: declared {meta['declared_count']}, actual {count}")
    return eligible, maps, placements, errors


def snapshot(root, requested=None):
    root = Path(root).resolve()
    eligible, maps, placements, errors = scan(root)
    requested = sorted(eligible) if requested is None else sorted(set(requested))
    pages, excluded = [], []
    for path in requested:
        # Explicit inputs must remain inside the Wiki, including symlink targets.
        normalized = wr.wiki_path(path, root)
        if not (root / normalized).is_file():
            raise ValueError(f"Wiki page does not exist: {path}")
        if normalized not in eligible:
            excluded.append({"page": path, "reason": "not a searchable supported knowledge page"})
            continue
        pages.append({"page": path, "title": eligible[path].get("title"),
                      "type": eligible[path]["type"], "sha256": digest(root / path),
                      "existing_placements": [x for x in placements if x["page"] == path]})
    return {"version": 1, "pages": pages, "excluded": excluded, "maps": maps,
            "knowledge_hashes": {p: digest(root / p) for p in eligible},
            "baseline_errors": errors,
            "missing": [x["page"] for x in pages if not x["existing_placements"]]}


def verify(root, before, decisions):
    root = Path(root).resolve()
    eligible, maps, placements, errors = scan(root)
    actual = {(x["page"], x["map"], x["heading"]) for x in placements}
    mapped = {x["page"] for x in placements}
    scope = {x["page"] for x in before["pages"]}
    old_placements = {(x["page"], p["map"], p["heading"])
                      for x in before["pages"] for p in x["existing_placements"]}
    missing = sorted(scope - mapped)
    errors += [f"scope page unmapped: {p}" for p in missing]
    for path, expected in before["knowledge_hashes"].items():
        if not (root / path).is_file() or digest(root / path) != expected:
            errors.append(f"knowledge changed during map sync: {path}")
    migrations = set()
    seen = set()
    for item in decisions:
        page = item.get("page")
        if page in seen or page not in scope:
            errors.append(f"duplicate or out-of-scope decision: {page}")
        seen.add(page)
        if not item.get("reason", "").strip() or not item.get("placements"):
            errors.append(f"missing semantic reason or placement: {page}")
        for place in item.get("placements", []):
            if (page, place.get("map"), place.get("heading")) not in actual:
                errors.append(f"planned section has no page link: {page}: {place}")
        if item.get("reason", "").strip() and item.get("placements") and all(
                (page, p.get("map"), p.get("heading")) in actual for p in item["placements"]):
            for previous in item.get("previous_placements", []):
                if (page, previous.get("map"), previous.get("heading")) in old_placements:
                    migrations.add((previous.get("map"), page))
                else:
                    errors.append(f"migration has no matching baseline entry: {page}: {previous}")
    for path, old in before["maps"].items():
        lost = set(old["pages"]) - set(maps.get(path, {}).get("pages", []))
        errors += [f"previous map entry lost: {path}: {p}" for p in sorted(lost)
                   if (path, p) not in migrations]
    errors += [f"missing placement decision: {p}" for p in before["missing"] if p not in seen]
    return {"status": "failed" if errors else "passed", "scope_pages": len(scope),
            "mapped_scope_pages": len(scope & mapped), "missing": missing,
            "global_eligible": len(eligible), "global_mapped": len(set(eligible) & mapped),
            "global_missing": sorted(set(eligible) - mapped),
            "map_counts": {p: len(mapped) if p == "wiki/map/知识总地图.md" else len(m["pages"])
                           for p, m in maps.items()},
            "semantic_decisions": len(decisions), "knowledge_pages_unchanged": not any(
                e.startswith("knowledge changed") for e in errors),
            "map_sha256": {p: m["sha256"] for p, m in maps.items()},
            "errors": errors,
            "note": "Checks coverage, links, anchors, counts and preservation; semantic placement still requires Agent review."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    take = sub.add_parser("snapshot", help="Capture eligible pages, map entries and immutable page hashes")
    take.add_argument("--pages-json", type=Path, help="JSON array of Wiki paths; omit for all searchable knowledge pages")
    take.add_argument("--out", type=Path, required=True)
    check = sub.add_parser("verify", help="Fail on missing placements, broken maps or knowledge-page changes")
    check.add_argument("--snapshot", type=Path, required=True)
    check.add_argument("--decisions", type=Path, required=True, help="JSON array of Agent-authored placement decisions")
    check.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "snapshot":
        requested = json.loads(args.pages_json.read_text()) if args.pages_json else None
        if requested is not None and (not isinstance(requested, list) or not all(isinstance(p, str) for p in requested)):
            parser.error("--pages-json must contain an array of Wiki paths")
        result = snapshot(ROOT, requested)
        summary = {"scope_pages": len(result["pages"]), "missing": len(result["missing"]), "excluded": len(result["excluded"])}
    else:
        result = verify(ROOT, json.loads(args.snapshot.read_text()), json.loads(args.decisions.read_text()))
        summary = {k: result[k] for k in ["status", "scope_pages", "mapped_scope_pages", "global_eligible", "global_mapped"]}
        summary["errors"] = result["errors"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False))
    return int(result.get("status") == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
