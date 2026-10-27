#!/usr/bin/env python3
"""按 Wiki 页的 title、tags 定位候选，优先覆盖问题核心关键词。

从项目根目录运行：
  python3 tools/wiki_lookup.py search 'RAG 智能问答 知识库 评测' --core RAG --core 智能问答
  python3 tools/wiki_lookup.py search '优惠券' --type solution
  python3 tools/wiki_lookup.py search 'RAG 评测' --core RAG --related-to 'wiki/concepts/企业知识库与 RAG 建设.md'
  python3 tools/wiki_lookup.py unregistered

search 只返回候选和匹配依据；引用前由 Agent 读取 Wiki 正文。
触发、关键词扩展、正文取舍与停止条件见 tools/wiki_retrieval.md。
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "wiki"
RAW = ROOT / "raw"


def read_metadata(path, root=None):
    root = ROOT if root is None else Path(root)
    with path.open(encoding="utf-8") as stream:
        if stream.readline().strip() != "---":
            return None
        header = []
        for line in stream:
            if line.strip() == "---":
                break
            header.append(line)
        else:
            return None
        intro = []
        for line in stream:
            if line.startswith("## "):
                break
            intro.append(line)
    fields = {}
    current = None
    for line in header:
        field = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if field:
            current = field.group(1)
            fields[current] = field.group(2).strip().strip('"\'')
        elif current and line.startswith("  - "):
            fields[current] += ", " + line[4:].strip().strip('"\'')
    for key in ("tags", "aliases"):
        fields[key] = [value.strip().strip('"\'') for value in fields.get(key, "").strip("[]").split(",") if value.strip()]
    fields["path"] = path.relative_to(root).as_posix()
    if not fields.get("summary"):
        lead = re.search(r"(?m)^>\s*(\S.*)$", "".join(intro))
        fields["summary"] = lead.group(1).strip() if lead else ""
    return fields


def pages(root=None):
    root = ROOT if root is None else Path(root)
    wiki = root / "wiki"
    for path in wiki.rglob("*.md"):
        if any(part.startswith("_") for part in path.relative_to(wiki).parts):
            continue
        fields = read_metadata(path, root)
        if fields and fields.get("type") and fields.get("searchable", "true").lower() != "false":
            yield fields


def norm(value):
    return re.sub(r"[^\w]+", "", value.casefold())


def terms(query):
    found = re.findall(r"[A-Za-z][A-Za-z0-9+.-]*|[\u4e00-\u9fff]+", query)
    unique = {}
    for term in found:
        unique.setdefault(norm(term), term)
    return list(unique.values())


def term_matches(term, value):
    # Latin acronyms must not match inside unrelated words such as stoRAGe.
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", term):
        return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", value, re.I))
    return bool(norm(term) and norm(term) in norm(value))


def match_page(page, query_terms, core_terms):
    matches = {}
    points = 0
    for term in terms(" ".join(core_terms + query_terms)):
        fields = []
        if term_matches(term, page.get("title", "")):
            fields.append("title")
            points += 8
        if any(term_matches(term, tag) for tag in page.get("tags", [])):
            fields.append("tags")
            points += 6
        if fields:
            matches[term] = fields
    matched = {norm(term) for term in matches}
    core_hits = [term for term in core_terms if norm(term) in matched]
    return {"core_matches": core_hits,
            "core_missing": [term for term in core_terms if norm(term) not in matched],
            "matches": matches, "score": points}


def score(page, query):
    return match_page(page, terms(query), [])['score']


def rank_pages(records, query, core_keywords=None, limit=8):
    query_terms = terms(query)
    core_terms = terms(" ".join(core_keywords)) if core_keywords is not None else query_terms
    ranked = []
    for page in records:
        match = match_page(page, query_terms, core_terms)
        matched = {norm(term) for term in match["matches"]}
        if any(norm(term) in matched for term in query_terms):
            ranked.append({"path": page["path"], "type": page.get("type", ""),
                           "status": page.get("status", ""), "title": page.get("title", ""),
                           "tags": page.get("tags", []), "linked_from": page.get("linked_from", []),
                           "navigation_only": page.get("type") == "map", **match})
    ranked.sort(key=lambda p: (-len(p["core_matches"]), -p["score"], p["path"]))
    return ranked[:max(0, min(limit, 30))]


def linked_records(origin, records, root=None):
    # Reuse the ingest link parser, but only inspect this read page's outgoing links.
    try:
        from tools import wiki_relations
    except ModuleNotFoundError:
        import wiki_relations
    root = ROOT if root is None else Path(root).resolve()
    catalog = {p["path"]: p for p in records}
    if origin not in catalog:
        raise ValueError("关联入口必须是当前可检索的 wiki/ 页面")
    index = wiki_relations.name_index(catalog)
    found = {}
    for section, target, kind in wiki_relations.links((root / origin).read_text(encoding="utf-8")):
        dest, _, state = wiki_relations.resolve(target, kind, origin, catalog, root, index)
        if state == "local" and dest in catalog and dest != origin:
            page = found.setdefault(dest, {**catalog[dest], "linked_from": []})
            if section not in page["linked_from"]:
                page["linked_from"].append(section)
    return list(found.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="仅按 title、tags 匹配，优先覆盖核心关键词")
    search.add_argument("query", help="Agent 从问题拆出的关键词，以空格分隔，可含扩展词")
    search.add_argument("--core", action="append", help="原问题核心关键词，可重复；省略时所有查询词均为核心词")
    search.add_argument("--related-to", help="只筛选此 Wiki 正文链接的关联页，仍按问题关键词匹配")
    search.add_argument("--json", action="store_true", help="返回结构化候选及匹配依据")
    search.add_argument("--type", choices=["source", "entity", "concept", "comparison", "solution", "synthesis", "query", "note", "map"])
    search.add_argument("--limit", type=int, default=8)
    sub.add_parser("unregistered", help="列出尚无 source_path 登记的 raw Markdown")
    args = parser.parse_args()
    records = list(pages())
    if args.command == "unregistered":
        registered = {p.get("source_path", "") for p in records if p.get("type") == "source"}
        missing = sorted(p.relative_to(ROOT).as_posix() for p in RAW.rglob("*.md") if p.relative_to(ROOT).as_posix() not in registered)
        print("\n".join(missing) if missing else "全部 raw Markdown 均有 source_path 登记")
        return
    if not terms(args.query) or (args.core is not None and not terms(" ".join(args.core))):
        parser.error("查询词和核心词不能为空")
    if args.related_to:
        try:
            records = linked_records(args.related_to, records)
        except ValueError as error:
            parser.error(str(error))
    ranked = rank_pages([p for p in records if not args.type or p.get("type") == args.type],
                        args.query, args.core, args.limit)
    if args.json:
        print(json.dumps(ranked, ensure_ascii=False, indent=2))
        return
    if not ranked:
        print("未找到 title/tags 匹配的候选；不能据此判断 Wiki 中不存在相关知识。")
    for page in ranked:
        tags = ", ".join(page["tags"])
        print(f"{page['path']} | {page['type']} | {page.get('status', '')} | {page.get('title', '')} | {tags}")
        hits = ", ".join(page["core_matches"]) or "无"
        missing = ", ".join(page["core_missing"]) or "无"
        reasons = "; ".join(f"{term}:{'+'.join(fields)}" for term, fields in page["matches"].items())
        print(f"  核心命中: {hits} | 核心缺失: {missing} | 匹配: {reasons}")
        if page["linked_from"]:
            print("  关联入口章节: " + ", ".join(page["linked_from"]))
        if page["navigation_only"]:
            print("  仅用于导航，不能作为事实依据。")


if __name__ == "__main__":
    main()
