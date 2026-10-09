#!/usr/bin/env python3
"""按 wiki 页的元数据定位候选，不维护逐页登记文件。

从项目根目录运行：
  python3 tools/wiki_lookup.py search 'RAG 评估' --limit 8
  python3 tools/wiki_lookup.py search '优惠券' --type solution
  python3 tools/wiki_lookup.py unregistered

search 只输出候选定位信息；回答前仍须读取命中页及必要的 source/raw。
"""

import argparse
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


def score(page, query):
    phrase = norm(query)
    terms = [norm(term) for term in re.findall(r"[A-Za-z][A-Za-z0-9+.-]*|[\u4e00-\u9fff]+", query)]
    fields = [(page.get("title", ""), 8)]
    fields += [(tag, 6) for tag in page["tags"]]
    fields += [(alias, 7) for alias in page["aliases"]]
    fields += [(page.get("summary", ""), 2)]
    total = 0
    for value, weight in fields:
        value = norm(value)
        if not value:
            continue
        if phrase and len(phrase) > 1 and (phrase in value or (len(value) > 1 and value in phrase)):
            total += weight * 2
        elif any(len(term) > 1 and (term in value or (len(value) > 1 and value in term)) for term in terms):
            total += weight
    if page.get("type") == "map":
        total -= 5
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="按标题、标签、别名和一句话导语定位候选页")
    search.add_argument("query", help="从问题中提取的 1 至 3 个关键词")
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
    ranked = sorted(((score(p, args.query), p) for p in records if not args.type or p.get("type") == args.type), key=lambda item: (-item[0], item[1]["path"]))
    for points, page in ranked[:max(0, min(args.limit, 30))]:
        if points <= 0:
            break
        description = page.get("summary", "").replace("\n", " ")[:110]
        tags = ", ".join(page["tags"])
        print(f"{page['path']} | {page['type']} | {page.get('status', '')} | {page.get('title', '')} | {tags}")
        if description:
            print(f"  {description}")


if __name__ == "__main__":
    main()
