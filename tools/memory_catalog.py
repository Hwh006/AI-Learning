#!/usr/bin/env python3
"""Query memory frontmatter, validate entries, and render directory navigation."""

import argparse
from datetime import date
import io
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("经历", "偏好", "个人原则", "个人目标", "项目", "任务", "决策方式")
CATEGORY_GUIDANCE = {
    "经历": ("亲历、处境、想法、外部观察及协作经验", "回答个人背景、写简历或参考过去处理经验时"),
    "偏好": ("回复、表达、交付及协作的核心要求", "每轮回复前必须检索并应用适用偏好；执行方式偏好在行动前应用"),
    "个人原则": ("用户明确的判断准则、支持经历与例外", "决策需要参考用户自己的原则时"),
    "个人目标": ("用户明确的长期方向及约束", "制定计划、安排优先级或讨论长期选择时"),
    "项目": ("项目背景、职责、方案、结果及权威资料入口", "讨论具体项目、准备面试或核对本人贡献时"),
    "任务": ("需要跨会话保留的行动、进度与下一步", "继续此前工作、查询待办或恢复任务时"),
    "决策方式": ("一类情境中的关注因素、判据与取舍", "遇到相似选择，需要参考过去判断方式时"),
}
ORIGINS = ("user_requirement", "user_statement", "conversation_experience")
REQUIRED = ("id", "title", "category", "tags", "summary",
            "status", "origin", "created", "updated")


def read_metadata(stream):
    if stream.readline().strip() != "---":
        raise ValueError("记忆缺少 frontmatter")
    fields = {}
    for line in stream:
        if line.strip() == "---":
            return fields
        match = re.fullmatch(r"([a-z_]+):\s*(.*?)\s*\n?", line)
        if not match or match[1] in fields:
            raise ValueError("元数据须为不重复的单行字段")
        key, value = match.groups()
        if value.startswith(('"', '[')):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                if value.startswith('[') and value.endswith(']'):
                    value = [x.strip() for x in value[1:-1].split(',') if x.strip()]
                else:
                    raise ValueError("元数据字符串引号不合法")
        fields[key] = value
    raise ValueError("frontmatter 未闭合")


def validate_entry(path, fields):
    if any(not fields.get(key) for key in REQUIRED):
        raise ValueError(f"{path}: 记忆缺少必需元数据")
    if any(not isinstance(fields[key], str) for key in REQUIRED if key != "tags"):
        raise ValueError(f"{path}: 元数据须为字符串，tags 须为列表")
    if "scope" in fields and (not isinstance(fields["scope"], str) or not fields["scope"].strip()):
        raise ValueError(f"{path}: scope 须为非空字符串，无额外适用限制时省略")
    if fields["category"] not in CATEGORIES or path.parent.name != fields["category"]:
        raise ValueError(f"{path}: 分类与目录不一致")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", fields["id"]):
        raise ValueError(f"{path}: id 须为稳定的英文标识")
    if fields["status"] not in ("active", "retired") or fields["origin"] not in ORIGINS:
        raise ValueError(f"{path}: 状态或来源不合法")
    tags = fields["tags"]
    if not isinstance(tags, list) or not tags or any(not isinstance(x, str) or not x.strip() for x in tags):
        raise ValueError(f"{path}: tags 须为非空字符串列表")
    if len(tags) != len(set(tags)):
        raise ValueError(f"{path}: tags 重复")
    length = len(re.sub(r"\s", "", fields["summary"]))
    if not 100 <= length <= 150:
        raise ValueError(f"{path}: 摘要须为100–150字，当前{length}字")
    if date.fromisoformat(fields["updated"]) < date.fromisoformat(fields["created"]):
        raise ValueError(f"{path}: updated 早于 created")


def entries(root=ROOT, overrides=None, removed=(), *, categories=None):
    root = Path(root).resolve()
    overrides = overrides or {}
    categories = CATEGORIES if categories is None else tuple(categories)
    if any(category not in CATEGORIES for category in categories):
        raise ValueError("未知记忆类别")
    paths = {p for category in categories for p in (root / "memory" / category).glob("*.md")}
    paths.update(p for p in overrides if p != root / "memory/索引.md" and p.parent.name in categories)
    result, ids = [], set()
    for path in sorted(paths - set(removed)):
        if path in overrides:
            fields = read_metadata(io.StringIO(overrides[path].decode("utf-8")))
        else:
            with path.open(encoding="utf-8") as stream:
                fields = read_metadata(stream)
        validate_entry(path, fields)
        if fields["id"] in ids:
            raise ValueError(f"重复记忆 id: {fields['id']}")
        ids.add(fields["id"])
        result.append(dict(fields, path=path.relative_to(root / "memory").as_posix()))
    return result


def render_index(items):
    lines = ["# 记忆目录导航", "",
             "本页说明各目录的用途和调用时机，不汇总具体记忆的标题、标签、摘要或正文。每轮回复前必须检索偏好并按适用要求回复；其他目录按当前任务需要选择，再通过元数据查询定位条目。",
             "目录用于缩小检索范围；未命中或分类不明确时扩大查询，不把目录当作唯一边界。空目录不代表已有记忆。", "",
             "| 目录 | 存什么 | 什么时候读取 | 有效条目 | 已停用 |",
             "|---|---|---|---|---|"]
    for category in CATEGORIES:
        group = [x for x in items if x["category"] == category]
        purpose, when = CATEGORY_GUIDANCE[category]
        lines.append(f"| [{category}/]({category}/) | {purpose} | {when} | {sum(x['status'] == 'active' for x in group)} | {sum(x['status'] == 'retired' for x in group)} |")
    lines.extend(["", "## 检索入口", "",
                  "- [读取机制](读取机制.md)：选择范围、查询元数据、回读正文与未命中补查。",
                  "- [查询工具说明](../tools/memory_catalog.md)：命令、参数、返回字段与匹配限制。",
                  "- [目录说明](README.md)：分类边界、来源性质与维护入口。", "",
                  "本页由 `python3 tools/memory_catalog.py` 生成；条目元数据保留在各文件的 frontmatter 中，查询工具只返回本次候选。`active` 表示可供检索，不证明事实仍然成立。"])
    return "\n".join(lines).rstrip() + "\n"


def search_entries(items, query="", *, status="active", limit=8):
    terms = list(dict.fromkeys(query.casefold().split()))
    matches = []
    for item in items:
        if status != "all" and item["status"] != status:
            continue
        fields = {key: " ".join(item[key]) if isinstance(item[key], list) else item[key]
                  for key in ("id", "title", "tags", "summary", "scope") if key in item}
        matched = {key: [term for term in terms if term in value.casefold()]
                   for key, value in fields.items()}
        matched = {key: value for key, value in matched.items() if value}
        if terms and not matched:
            continue
        matches.append(dict(item, matched_fields=matched))
    matches.sort(key=lambda item: (-len({term for terms in item["matched_fields"].values() for term in terms}), item["id"]))
    return {"total": len(matches), "returned": len(matches[:limit]),
            "truncated": len(matches) > limit, "results": matches[:limit]}


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="校验元数据和现有索引是否一致，不写文件")
    commands = parser.add_subparsers(dest="command")
    search = commands.add_parser("search", help="只查询记忆元数据，不读取正文")
    search.add_argument("query", nargs="?", default="", help="空格分隔关键词；省略时浏览所选目录元数据")
    search.add_argument("--category", action="append", choices=CATEGORIES, help="可重复；省略时查七类目录")
    search.add_argument("--status", choices=("active", "retired", "all"), default="active")
    search.add_argument("--limit", type=positive_int, default=8)
    search.add_argument("--json", action="store_true", help="输出结构化候选、命中字段和截断状态")
    args = parser.parse_args()
    if args.check and args.command:
        parser.error("--check 与 search 不能同时使用")
    try:
        items = entries(ROOT, categories=args.category if args.command == "search" else None)
        if args.command == "search":
            result = dict(query=args.query, categories=args.category or list(CATEGORIES),
                          **search_entries(items, args.query, status=args.status, limit=args.limit))
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"命中 {result['total']} 条，返回 {result['returned']} 条；truncated={result['truncated']}")
                for item in result["results"]:
                    print(f"memory/{item['path']} | {item['title']} | {item['status']} | {item['origin']}")
                    print(f"  标签：{'、'.join(item['tags'])}\n  摘要：{item['summary']}")
                    if item.get("scope"):
                        print(f"  适用限制：{item['scope']}")
                    print(f"  命中：{json.dumps(item['matched_fields'], ensure_ascii=False)}")
                if result["truncated"]:
                    print("结果已截断；可细化查询或增大 --limit，未返回不代表不存在。")
            return 0
        index = render_index(items)
        if not args.check:
            print(index, end="")
            return 0
        if (ROOT / "memory/索引.md").read_text() != index:
            raise ValueError("索引与条目元数据不一致")
        print(json.dumps({"ok": True, "entries": len(items), "categories": len(CATEGORIES)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
