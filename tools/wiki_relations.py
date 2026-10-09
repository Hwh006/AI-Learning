#!/usr/bin/env python3
"""Find cross-page candidates and check Agent-authored relationship decisions.

Only metadata, link destinations and headings are returned. This tool never
infers semantic relationships or edits Wiki pages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from tools import wiki_lookup
except ModuleNotFoundError:
    import wiki_lookup

ROOT = Path(__file__).resolve().parents[1]
VERSION = 1
ACTIVE = {"create", "merge"}


def values(value):
    if isinstance(value, list):
        return value
    return [v.strip().strip("\"'") for v in str(value or "").strip("[]").split(",") if v.strip()]


def wiki_path(value, root):
    if not isinstance(value, str) or not value.startswith("wiki/") or not value.endswith(".md"):
        raise ValueError(f"expected wiki Markdown path: {value!r}")
    path = (root / value).resolve()
    if not path.is_relative_to((root / "wiki").resolve()):
        raise ValueError(f"path escapes Wiki: {value}")
    return path.relative_to(root.resolve()).as_posix()


def sources(page):
    result = set()
    for value in values(page.get("sources")):
        value = value.removeprefix("wiki/")
        result.add(value)
    if page.get("type") == "source":
        result.add(page["path"].removeprefix("wiki/"))
    return result


def visible_lines(text):
    """Skip frontmatter, fenced examples and comments; retain heading sections."""
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    fence = None
    section = "开篇"
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        heading = re.match(r"^#{2,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            section = heading[1]
        yield section, line


def links(text):
    for section, line in visible_lines(text):
        # Inline code examples are not reader links.
        line = re.sub(r"`+[^`]*`+", "", line)
        for match in re.finditer(r"(?<!!)\[\[([^\]]+)\]\]", line):
            target = match[1].split("|", 1)[0]
            yield section, target, "wiki"
        # Angle destinations allow spaces/parentheses; bare destinations permit
        # balanced one-level parentheses, common in Chinese page filenames.
        for match in re.finditer(r"(?<!!)\[(?!\[)[^\]]+\]\((<[^>]+>|(?:[^()\n]|\([^()]*\))+)\)", line):
            target = match[1].strip().strip("<>")
            yield section, target, "markdown"


def catalog(root):
    pages = {p["path"]: p for p in wiki_lookup.pages(root)}
    return pages


def name_index(pages):
    index = {}
    for p in pages.values():
        for name in {Path(p["path"]).stem, p.get("title"), *values(p.get("aliases"))}:
            if name:
                index.setdefault(name, set()).add(p["path"])
    return index


def resolve(target, kind, origin, pages, root, index=None):
    target = unquote(target)
    if urlsplit(target).scheme or target.startswith("//"):
        return None, "", "external"
    name, _, anchor = target.partition("#")
    if not name:
        return origin, anchor, "local"
    if kind == "markdown":
        path = Path(name) if name.startswith("/") else root / Path(origin).parent / name
        path = path.resolve()
        if not path.is_relative_to(root.resolve()):
            return str(path), anchor, "missing"
        return path.relative_to(root.resolve()).as_posix(), anchor, "local"
    name = name.removesuffix(".md")
    exact = name + ".md"
    for candidate in [exact, "wiki/" + exact, (Path(origin).parent / exact).as_posix()]:
        if candidate in pages:
            return candidate, anchor, "local"
    matches = list((index if index is not None else name_index(pages)).get(name, []))
    if len(matches) == 1:
        return matches[0], anchor, "local"
    return name, anchor, "ambiguous" if matches else "missing"


def graph(pages, root):
    edges = set()
    index = name_index(pages)
    for path, page in pages.items():
        if page.get("type") == "map" or not (root / path).is_file():
            continue
        for _, target, kind in links((root / path).read_text(encoding="utf-8")):
            dest, _, state = resolve(target, kind, path, pages, root, index)
            if state == "local" and dest in pages and pages[dest].get("type") != "map":
                edges.add((path, dest))
    return edges


def matches(term, text):
    term, text = wiki_lookup.norm(term), wiki_lookup.norm(text)
    return len(term) >= 2 and term in text


def candidates(plan, root=ROOT, final=False, limit=8):
    root = Path(root).resolve()
    pages = catalog(root)
    for item in plan.get("pages", []):
        if item.get("action") not in ACTIVE:
            continue
        path = wiki_path(item["target_path"], root)
        if final and path in pages:
            continue  # Final metadata is authoritative; don't hide changed topics.
        old = pages.get(path, {})
        pages[path] = {**old, "path": path, "type": item["page_type"],
                       "title": item.get("title", old.get("title", "")),
                       "aliases": values(item.get("aliases", old.get("aliases", []))),
                       "tags": values(item.get("keywords", old.get("tags", []))),
                       "summary": item.get("summary", old.get("summary", "")),
                       "sources": item.get("sources", old.get("sources", [])),
                       "planned": True}
    edges = graph(pages, root)
    # Merged hub pages may cite dozens of older sources. Strong candidates are
    # scoped to this ingest's source(s), rather than reopening every old edge.
    current_sources = {p["target_path"].removeprefix("wiki/") for p in plan.get("pages", [])
                       if p.get("page_type") == "source"}
    result = []
    for item in plan.get("pages", []):
        if item.get("action") not in ACTIVE:
            continue
        origin = pages[item["target_path"]]
        terms = [origin.get("title", ""), *values(origin.get("aliases")), *values(origin.get("tags"))]
        found = []
        for path, other in pages.items():
            if path == origin["path"]:
                continue
            reasons = []
            direct = (origin["path"], path) in edges or (path, origin["path"]) in edges
            if direct:
                reasons.append("existing_link")
            title_hit = any(matches(t, other.get("title", "")) or
                            any(matches(t, a) for a in values(other.get("aliases"))) for t in terms)
            if title_hit:
                reasons.append("title_or_alias")
            intro_hit = any(matches(t, other.get("summary", "")) for t in terms)
            if intro_hit:
                reasons.append("intro")
            common_tags = set(map(wiki_lookup.norm, values(origin.get("tags")))) & set(map(wiki_lookup.norm, values(other.get("tags"))))
            common_tags.discard("")
            if common_tags:
                reasons.append("shared_tags:" + ",".join(sorted(common_tags)))
            common_sources = sources(origin) & sources(other)
            if common_sources:
                reasons.append("shared_sources:" + ",".join(sorted(common_sources)))
            if not reasons:
                continue
            navigation = "map" in {origin.get("type"), other.get("type")}
            in_scope = common_sources & current_sources if current_sources else common_sources
            # Entity names such as "吉客云" appear as substrings in hundreds of manual titles.
            # Treat identity as a full normalized name/alias match; substring matches remain
            # useful as weak topic signals and are promoted only by current-source evidence.
            identity_names = {wiki_lookup.norm(v) for v in [origin.get("title", ""), *values(origin.get("aliases"))] if v}
            other_names = {wiki_lookup.norm(v) for v in [other.get("title", ""), *values(other.get("aliases"))] if v}
            identity_hit = bool(identity_names & other_names)
            strong = not navigation and (identity_hit or (direct and (in_scope or not current_sources))
                                         or (in_scope and (title_hit or intro_hit)))
            found.append({"candidate_path": path, "page_type": other.get("type"),
                          "title": other.get("title"), "summary": other.get("summary", "")[:120],
                          "strength": "strong" if strong else "weak", "navigation_only": navigation,
                          "reasons": reasons, "planned": bool(other.get("planned"))})
        found.sort(key=lambda c: (c["strength"] != "strong", -len(c["reasons"]), c["candidate_path"]))
        strong = [c for c in found if c["strength"] == "strong"]
        weak = [c for c in found if c["strength"] == "weak"]
        result.append({"target_path": origin["path"], "candidates": strong + weak[:limit],
                       "weak_omitted": max(0, len(weak) - limit)})
    return {"version": VERSION, "mode": "final" if final else "planning", "pages": result}


def retain_strong(scan, previous):
    by_page = {p["target_path"]: p for p in scan["pages"]}
    for old in previous.get("pages", []):
        page = by_page.get(old["target_path"])
        if not page:
            continue
        seen = {c["candidate_path"]: c for c in page["candidates"]}
        for candidate in old["candidates"]:
            if candidate["strength"] == "strong":
                current = seen.get(candidate["candidate_path"])
                if current:
                    current["strength"] = "strong"
                else:
                    page["candidates"].append(candidate)
    return scan


def validate_decisions(plan, scan, root=ROOT):
    errors = []
    required = {p["target_path"]: {c["candidate_path"] for c in p["candidates"] if c["strength"] == "strong"}
                for p in scan["pages"]}
    for page in plan.get("pages", []):
        if page.get("action") not in ACTIVE:
            continue
        origin = page["target_path"]
        if not isinstance(page.get("title"), str) or not page["title"].strip():
            errors.append(f"{origin}: title required")
        keywords = page.get("keywords")
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= 6 or not all(isinstance(k, str) and k.strip() for k in keywords):
            errors.append(f"{origin}: 1–6 keywords required")
        decisions = page.get("relations")
        if not isinstance(decisions, list):
            errors.append(f"{origin}: relations array required (may be empty)")
            decisions = []
        seen = set()
        for d in decisions:
            if not isinstance(d, dict):
                errors.append(f"{origin}: invalid relation record")
                continue
            target = d.get("candidate_path")
            try:
                wiki_path(target, Path(root))
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if target == origin or target in seen:
                errors.append(f"{origin}: duplicate/self relation {target}")
            seen.add(target)
            if d.get("decision") not in {"related", "unrelated", "pending"}:
                errors.append(f"{origin} -> {target}: invalid decision")
            for key in ["relation", "reason"]:
                if not isinstance(d.get(key), str) or not d[key].strip():
                    errors.append(f"{origin} -> {target}: {key} required")
            if not isinstance(d.get("evidence"), list) or not d["evidence"] or not all(isinstance(e, str) and e.strip() for e in d["evidence"]):
                errors.append(f"{origin} -> {target}: evidence locations required")
            placements = d.get("placements")
            if not isinstance(placements, list):
                errors.append(f"{origin} -> {target}: placements array required")
                continue
            if (d.get("decision") == "related") != bool(placements):
                errors.append(f"{origin} -> {target}: only related decisions must have placements")
            for link in placements:
                if not isinstance(link, dict) or {link.get("from_path"), link.get("to_path")} != {origin, target}:
                    errors.append(f"{origin} -> {target}: placement must connect this pair")
                    continue
                if not isinstance(link.get("section"), str) or not link["section"].strip():
                    errors.append(f"{origin} -> {target}: placement section required")
        for target in sorted(required.get(origin, set()) - seen):
            errors.append(f"{origin}: undecided strong candidate {target}")
    return errors


def anchors(text):
    result = set()
    counts = {}
    for _, line in visible_lines(text):
        m = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if m:
            title = m[1]
            slug = re.sub(r"[^\w\-\s]", "", title.casefold()).replace(" ", "-")
            n = counts.get(slug, 0)
            counts[slug] = n + 1
            result.update([title, slug + (f"-{n}" if n else "")])
    return result


def verify(plan, root=ROOT):
    root = Path(root).resolve()
    scan = candidates(plan, root, final=True)
    errors = validate_decisions(plan, scan, root)
    if plan.get("relation_candidates"):
        errors += validate_decisions(plan, plan["relation_candidates"], root)
    pages = catalog(root)
    index = name_index(pages)
    pending = []
    checked_links = 0
    affected = {p["target_path"] for p in plan.get("pages", []) if p.get("action") in ACTIVE}
    for page in plan.get("pages", []):
        if page.get("action") not in ACTIVE:
            continue
        for d in page.get("relations", []) if isinstance(page.get("relations"), list) else []:
            if not isinstance(d, dict):
                continue
            if d.get("decision") == "pending":
                pending.append({"from_path": page["target_path"], **d})
            for link in d.get("placements", []) if isinstance(d.get("placements"), list) else []:
                if not isinstance(link, dict) or not all(isinstance(link.get(k), str) for k in ["from_path", "to_path", "section"]):
                    continue
                origin, dest = link["from_path"], link["to_path"]
                try:
                    wiki_path(origin, root); wiki_path(dest, root)
                except ValueError:
                    continue
                affected.add(origin)
                if not (root / origin).is_file() or not (root / dest).is_file():
                    errors.append(f"planned link endpoint missing: {origin} -> {dest}")
                    continue
                found = False
                if link["section"] == "frontmatter":
                    found = dest.removeprefix("wiki/") in sources(pages.get(origin, {})) and not link.get("target_anchor")
                for section, target, kind in links((root / origin).read_text(encoding="utf-8")):
                    resolved, anchor, state = resolve(target, kind, origin, pages, root, index)
                    if state == "local" and resolved == dest and section == link["section"] and (not link.get("target_anchor") or anchor == link["target_anchor"]):
                        found = True
                if not found:
                    errors.append(f"planned link not written at section {link['section']}: {origin} -> {dest}")
                checked_links += 1
    for path in sorted(affected):
        if not (root / path).is_file():
            errors.append(f"planned page missing: {path}")
            continue
        text = (root / path).read_text(encoding="utf-8")
        for section, target, kind in links(text):
            dest, anchor, state = resolve(target, kind, path, pages, root, index)
            if state == "external":
                continue
            if state != "local" or not (root / dest).is_file():
                errors.append(f"dead/ambiguous link in {path} [{section}]: {target}")
            elif anchor and (root / dest).suffix == ".md" and anchor not in anchors((root / dest).read_text(encoding="utf-8")):
                errors.append(f"missing anchor in {path}: {target}")
        for target in sources(pages.get(path, {})):
            if not (root / "wiki" / target).is_file():
                errors.append(f"source target missing in {path}: {target}")
    for path, digest in plan.get("verified_baselines", {}).items():
        file = root / wiki_path(path, root)
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            errors.append(f"verified page changed: {path}")
    errors = sorted(set(errors))
    return {"status": "failed" if errors else "needs_review" if pending else "passed",
            "complete": not errors and not pending, "errors": errors,
            "pending_relations": pending, "checked_placements": checked_links,
            "final_candidates": scan,
            "note": "Structural checks only; Agent must review relationship evidence and reading paths."}


def baselines(plan, root=ROOT):
    root = Path(root)
    paths = {p["target_path"] for p in plan["pages"] if p.get("action") in ACTIVE}
    for page in plan["pages"]:
        for decision in page.get("relations", []):
            for link in decision.get("placements", []):
                paths.add(link["from_path"])
    result = {}
    for path in paths:
        file = root / path
        if file.is_file():
            meta = wiki_lookup.read_metadata(file, root)
            if meta and meta.get("status") == "verified":
                result[path] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["candidates", "verify"])
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    try:
        result = candidates(plan) if args.command == "candidates" else verify(plan)
    except (ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(json.dumps({"output": args.output, "status": result.get("status", "candidates")}, ensure_ascii=False))
    else:
        print(output, end="")
    return 0 if result.get("status", "passed") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
