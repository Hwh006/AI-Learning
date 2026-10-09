#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_note_page.py — 07 note 个人加工页自检器（只读工具）

页型职责定义见 index.md；页面结构与校验按对应 Wiki Skill

用法：
  python3 verify_note_page.py --page wiki/notes/X.md       单页自检
  python3 verify_note_page.py --all                        全库体检（两型分布 + 版本链）
  python3 verify_note_page.py --chain "构建一套上下文工程"    看一条方法论的版本链

本脚本只读，不修改任何文件。
"""

import argparse
import glob
import io
import os
import re
import sys
from collections import defaultdict

if sys.version_info[0] < 3:
    print('需要 Python 3')
    sys.exit(2)

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

NOTES_DIR = 'wiki/notes'
HISTORY_DIR = 'wiki/notes/_history'

FAIL = '[FAIL]'
WARN = '[WARN]'

# ---------------------------------------------------------------- 常量（07 规范）

NOTE_TYPE_ENUM = ('judgment', 'methodology')
STATUS_ENUM = ('verified', 'draft')

# 判断型骨架（07 §3）
SECTIONS_JUDGMENT = ['判断', '为什么这么判断', '依据', '适用范围', '什么时候会失效', '相关']
# 方法论型只固定版本记录；情境、分论点、顺序步骤和边界由内容决定。
SECTIONS_METHODOLOGY = ['版本历史']

# 占位语（缺料不得用占位符，07 §7 / 09 §4.0 同一口径）
PLACEHOLDER = {
    '', '—', '-', '–', '——', '/', 'n/a', 'na', 'tbd', '待补充', '待定',
    '无', '同上', '?', '？', '暂无', '略', '略。', '（略）', '待完善', '（本节暂缺）',
}

# 明确的占位符 —— 出现即 FAIL（没有任何合法用法）
STRICT_PLACEHOLDER = {
    '待补充', '待完善', '待定', '（本节暂缺）', '(本节暂缺)', 'tbd', '略', '略。', '（略）',
}

# 单词型「空内容」—— 可能是合法的"无异常"，判 WARN 让人看一眼
LOOSE_PLACEHOLDER = {'无', '暂无', '同上', '—', '-', '–', '——', '/', '?', '？', 'n/a', 'na'}

# 合法地说明素材没给（不算占位）
GAP_MARKERS = [
    '素材未给出', '素材未说明', '素材没有给出', '素材只说明了', '素材未提供',
    '资料未覆盖', '素材未涉及', '素材中没有', '素材没有给', '来自经验',
]

# 「判断」节里的模糊词（断言要能证伪）
HEDGE_WORDS = ['可能', '也许', '或许', '视情况', '看情况', '大概', '应该是', '感觉']

# 方法论目标里的空话（07 §4：目标是"达成什么"）
VAGUE_GOAL = [
    '提升效率', '提高效率', '很重要', '非常重要', '很有价值', '值得重视',
    '加强管理', '优化流程', '提高质量', '降本增效', '赋能', '抓手',
]

WIKILINK_RE = re.compile(r'\[\[([^\]\|]+?)(?:\|[^\]]*)?\]\]')
TODO_LINK_RE = re.compile(r'\[待创建:\s*\[\[(.+?)\]\]\s*\]')
SEPARATOR_ROW_RE = re.compile(r'^:?-{2,}:?$')

# ---------------------------------------------------------------- 基础工具


def read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def strip_q(s):
    s = (s or '').strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def parse_frontmatter(text):
    """返回 (fm_dict, body)。fm 值是字符串；列表统一成逗号分隔的字符串。"""
    if not text.startswith('---'):
        return {}, text
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?', text, re.S)
    if not m:
        return {}, text
    raw, body = m.group(1), text[m.end():]
    fm = {}
    key = None
    for line in raw.split('\n'):
        if not line.strip() or line.strip().startswith('#'):
            continue
        if re.match(r'^\s+', line) and key:
            fm[key] = (fm.get(key, '') + ' ' + line.strip()).strip()
            continue
        mm = re.match(r'^([A-Za-z_][\w\-]*)\s*:\s*(.*)$', line)
        if mm:
            key = mm.group(1)
            fm[key] = mm.group(2).strip()
    return fm, body


def parse_list_field(value):
    """把 frontmatter 列表字段拆成元素列表（兼容 [a, b] 与 dash 列表两种写法）。"""
    v = (value or '').strip()
    if not v:
        return []
    v = v.strip('[]')
    v = re.sub(r'(^|\s)-\s+', r'\1,', v)
    out = []
    for p in v.split(','):
        p = p.strip().strip('"\'').strip()
        if p:
            out.append(p)
    return out


def find_section(body, name):
    """返回某节的正文（到下一个同/更高级标题为止），找不到返回 None。"""
    lines = body.split('\n')
    start = None
    level = None
    for i, ln in enumerate(lines):
        m = re.match(r'^(#{2,6})\s+(.*)$', ln)
        if not m:
            continue
        title = m.group(2).strip()
        if start is None:
            if title == name or title.startswith(name):
                start = i + 1
                level = len(m.group(1))
        else:
            if len(m.group(1)) <= level:
                return '\n'.join(lines[start:i])
    if start is not None:
        return '\n'.join(lines[start:])
    return None


def split_h3(text):
    """把一段正文按 ### 切成 [(标题, 正文), ...]。"""
    parts = []
    cur_name = None
    cur = []
    for ln in (text or '').split('\n'):
        m = re.match(r'^###\s+(.*)$', ln)
        if m:
            if cur_name is not None:
                parts.append((cur_name, '\n'.join(cur)))
            cur_name = m.group(1).strip()
            cur = []
        elif cur_name is not None:
            cur.append(ln)
    if cur_name is not None:
        parts.append((cur_name, '\n'.join(cur)))
    return parts


def all_sections(body):
    return [m.group(1).strip() for m in re.finditer(r'^##\s+(.+)$', body, re.M)]


def table_rows(text):
    """抽 markdown 表格行，返回 [[cell, ...], ...]，跳过表头分隔行。"""
    rows = []
    for ln in (text or '').split('\n'):
        s = ln.strip()
        if not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if all(SEPARATOR_ROW_RE.match(c.replace(' ', '')) or c == '' for c in cells):
            continue
        rows.append(cells)
    return rows


def is_placeholder(s):
    t = (s or '').strip().strip('*_` ')
    if t in PLACEHOLDER:
        return True
    if any(g in t for g in GAP_MARKERS):
        return False
    return len(t) < 2


def page_index():
    idx = set()
    for p in glob.glob('wiki/**/*.md', recursive=True):
        idx.add(os.path.splitext(os.path.basename(p))[0])
    return idx


def note_pages():
    return sorted(glob.glob(os.path.join(NOTES_DIR, '*.md')))


def history_pages():
    return sorted(glob.glob(os.path.join(HISTORY_DIR, '*.md')))


def rel_of(path):
    return path.replace(os.sep, '/')


def in_history(path):
    r = '/' + rel_of(path).strip('/') + '/'
    return '/notes/_history/' in r


def resolve_superseded(value):
    """把 superseded_by 的值解析成可判断的路径（兼容仓库根相对与 notes 内相对两种写法）。"""
    v = strip_q(value).strip()
    if not v:
        return ''
    if os.path.exists(v):
        return v
    cand = os.path.join(os.path.dirname(HISTORY_DIR), v)
    if os.path.exists(cand):
        return cand
    return v


# ---------------------------------------------------------------- 单页检查


def check_page(path):
    issues = []

    def add(level, area, msg):
        issues.append((level, area, msg))

    if not os.path.exists(path):
        add(FAIL, '文件', '文件不存在：%s' % path)
        return issues

    text = read(path)
    fm, body = parse_frontmatter(text)
    name = os.path.splitext(os.path.basename(path))[0]
    prefix = '（旧版）' if in_history(path) else ''

    # ---- frontmatter ----
    if strip_q(fm.get('type', '')) != 'note':
        add(FAIL, 'frontmatter', '%stype 必须是 note，当前是「%s」' % (prefix, fm.get('type', '')))

    nt = strip_q(fm.get('note_type', ''))
    if not nt:
        add(FAIL, 'frontmatter', '%s缺 note_type —— 必填，取值 judgment 或 methodology（07 §2）' % prefix)
    elif nt not in NOTE_TYPE_ENUM:
        add(FAIL, 'frontmatter', '%snote_type 取值非法：「%s」，只能是 %s'
            % (prefix, nt, ' / '.join(NOTE_TYPE_ENUM)))

    title = strip_q(fm.get('title', ''))
    if not title:
        add(FAIL, 'frontmatter', '%s缺 title（07 §2）' % prefix)

    tags = parse_list_field(fm.get('tags', ''))
    if not tags:
        add(FAIL, 'frontmatter', '%stags 必填且不能为空（07「标签约定」）' % prefix)

    author = strip_q(fm.get('author', ''))
    if author != 'human':
        add(FAIL, 'frontmatter', '%sauthor 必须是 human，当前是「%s」——author: human 是现有页面格式字段，方法论仍可由模型根据证据起草'
            % (prefix, author or '(空)'))

    status = strip_q(fm.get('status', ''))
    if status not in STATUS_ENUM:
        add(FAIL, 'frontmatter', '%sstatus 取值非法：「%s」，只能是 verified（你认可过）或 draft（AI 起草未确认）'
            % (prefix, status or '(空)'))
    if not strip_q(fm.get('created', '')):
        add(WARN, 'frontmatter', '%s缺 created' % prefix)

    # ---- H1 与 title 一致 ----
    mh1 = re.search(r'^#\s+(.+)$', body, re.M)
    if mh1 and title and mh1.group(1).strip() != title:
        add(WARN, 'frontmatter', '%sH1「%s」与 title「%s」不一致' % (prefix, mh1.group(1).strip(), title))
    if not mh1:
        add(FAIL, '结构', '%s缺 H1' % prefix)

    # ---- 版本字段 ----
    ver = strip_q(fm.get('version', ''))
    is_hist = in_history(path)
    sb = strip_q(fm.get('superseded_by', ''))
    vu = strip_q(fm.get('valid_until', ''))
    sc = strip_q(fm.get('searchable', '')).lower()

    if nt == 'methodology':
        if not ver:
            add(FAIL, '版本', '%s方法论型必须带 version（07 §2）' % prefix)
        elif not re.match(r'^\d+$', ver):
            add(FAIL, '版本', '%sversion 必须是整数，当前「%s」' % (prefix, ver))
    elif nt == 'judgment' and ver:
        add(WARN, '版本', '%s判断型不必带 version —— 版本化只作用于方法论型（07 §5）' % prefix)

    if is_hist:
        if not sb:
            add(FAIL, '版本', '旧版必须带 superseded_by —— 否则检索会命中它，照旧流程做错（07 §5）')
        else:
            tgt = resolve_superseded(sb)
            if not os.path.exists(tgt):
                add(FAIL, '版本', 'superseded_by 指向的文件不存在：%s' % sb)
        if not vu:
            add(FAIL, '版本', '旧版必须带 valid_until（它从哪天退役）')
        if sc == 'true':
            add(FAIL, '版本', '旧版 searchable 必须为 false —— 旧版不得被检索、不得当事实来源（07 §5）')
        elif sc != 'false':
            add(FAIL, '版本', '旧版必须带 searchable: false —— 在元数据上明确它不可被检索（07 §2）')
        if nt and nt != 'methodology':
            add(WARN, '版本', '只有方法论型才版本化，%s 不该出现在 _history/' % (nt or '该页'))
    else:
        if sb:
            add(FAIL, '版本', '当前版不应带 superseded_by —— 那是旧版的字段；若已作废请把本页移入 _history/')
        if sc == 'false':
            add(FAIL, '版本', '当前版 searchable 不得为 false —— 当前版可被检索并作为事实来源；若确已作废请移入 _history/')
        if ver and nt == 'methodology':
            base = name
            for h in sorted(glob.glob(os.path.join(HISTORY_DIR, base + '-v*.md'))):
                hfm, _ = parse_frontmatter(read(h))
                hv = strip_q(hfm.get('version', ''))
                hm = re.search(r'-v(\d+)\.md$', h)
                if hv.isdigit() and hv >= ver:
                    add(FAIL, '版本', '旧版 %s 的 version=%s 不小于当前版 %s —— 版本号倒挂'
                        % (os.path.basename(h), hv, ver))
                elif hm and not hv:
                    add(WARN, '版本', '旧版 %s 缺 version 字段' % os.path.basename(h))

    # ---- 骨架（按 note_type） ----
    present = all_sections(body)

    def has(sec):
        return any(s == sec or s.startswith(sec) for s in present)

    if nt == 'judgment':
        for sec in SECTIONS_JUDGMENT:
            if not has(sec):
                lv = FAIL if sec in ('判断', '什么时候会失效', '相关') else WARN
                note = ' —— 必写（07 §3）' if lv == FAIL else ''
                add(lv, '结构', '%s缺「%s」节%s' % (prefix, sec, note))

        jd = find_section(body, '判断')
        if jd is not None:
            lines = [l for l in jd.split('\n') if l.strip() and not l.strip().startswith('>')]
            if len(lines) > 2:
                add(WARN, '判断', '%s「判断」应是一句断言（1 行），当前 %d 行' % (prefix, len(lines)))
            hit = [h for h in HEDGE_WORDS if h in jd]
            if hit:
                add(WARN, '判断', '%s「判断」含模糊词 %s —— 断言要能证伪，模棱两可就无法验收'
                    % (prefix, '、'.join(hit)))

        sec = find_section(body, '什么时候会失效')
        if sec is not None and is_placeholder(sec.strip().split('\n')[0] if sec.strip() else ''):
            add(FAIL, '结构', '%s「什么时候会失效」是空的 —— 禁止留空（07 §3）' % prefix)

    if nt == 'methodology':
        for sec in SECTIONS_METHODOLOGY:
            if not has(sec):
                add(FAIL, '结构', '%s缺「%s」节 —— 当前方法论须保留版本记录' % (prefix, sec))

        if not any(s != '版本历史' and s != '相关' and '目录' not in s for s in present):
            add(FAIL, '正文', '%s没有解释知识点的正文小节 —— 目录和版本记录不能代替内容' % prefix)

        steps = find_section(body, '步骤')
        if steps is not None:
            subs = split_h3(steps)
            for sname, stxt in subs:
                if re.match(r'^步骤\s*\d+', sname) and not re.search(r'完成判据|做完|验收标准|如何确认|怎么判断|如何判断', stxt):
                    add(WARN, '步骤', '%s「%s」未说明如何判断结果可用；请人工检查正文是否已有等效解释'
                        % (prefix, sname))

        vh = find_section(body, '版本历史')
        if vh is not None:
            rows = table_rows(vh)
            if len(rows) < 2:
                add(FAIL, '版本历史', '%s「版本历史」应为表格，至少含表头 + 1 行（07 §5）' % prefix)
            else:
                for r in rows[1:]:
                    what = r[2] if len(r) > 2 else ''
                    why = r[3] if len(r) > 3 else ''
                    vcell = (r[0] if len(r) > 0 else '').strip().lower().lstrip('v').strip()
                    is_first = (vcell == '1')
                    if is_placeholder(what):
                        add(FAIL, '版本历史', '%s有一版没写「改了什么」：%s' % (prefix, ' | '.join(r)[:60]))
                    if is_placeholder(why) and not is_first:
                        add(FAIL, '版本历史', '%s有一版没写「为什么」（首版除外）：%s'
                            % (prefix, ' | '.join(r)[:60]))

        head = body[:400]
        vague = [v for v in VAGUE_GOAL if v in head]
        if vague:
            add(WARN, '目标', '%s目标含空话 %s —— 方法论的目标是"达成什么"，不是"要重视什么"（07 §4）'
                % (prefix, '、'.join(vague)))

    # ---- 出链（两型通用） ----
    links = [l.strip() for l in WIKILINK_RE.findall(body)]
    if not links:
        add(FAIL, '出链', '%s整页没有任何 [[wikilink]] —— note 必须出链，机制在 concept 页展开（07 §0）'
            % prefix)
    if name in links or (title and title in links):
        add(FAIL, '相关', '%s链了自己' % prefix)

    # ---- 占位符 ----
    for ln in body.split('\n'):
        s = ln.strip().lstrip('-* ').strip()
        if not s:
            continue
        if s in STRICT_PLACEHOLDER:
            add(FAIL, '占位符', '%s出现占位符「%s」—— 缺料要么写进相关待确认，要么整节删掉'
                % (prefix, s))
        elif s in LOOSE_PLACEHOLDER:
            add(WARN, '占位符', '%s「%s」可能就是没写实，检查这节该补内容还是整节删掉'
                % (prefix, s))

    # ---- 死链 ----
    idx = page_index()
    todo = set(TODO_LINK_RE.findall(body))
    for t in set(links):
        if t and t not in idx and t not in todo and t != name:
            add(WARN, '死链', '%s[[%s]] 目标页不存在，也没标 [待创建:]' % (prefix, t))

    return issues


def cmd_page(path):
    issues = check_page(path)
    fails = [i for i in issues if i[0] == FAIL]
    warns = [i for i in issues if i[0] == WARN]

    print('== 单页自检：%s ==' % path)
    print()
    if not issues:
        print('0 FAIL 0 WARN —— 元数据、版本与结构检查通过。')
        print('注意：还需人工检查知识点边界、解释是否充分、适用条件、行动依据与链接位置。')
        return 0

    by_area = defaultdict(list)
    for lv, area, msg in issues:
        by_area[(lv, area)].append(msg)
    for (lv, area), msgs in sorted(by_area.items()):
        print('%s %s' % (lv, area))
        for m in msgs:
            print('    %s' % m)
        print()
    print('小计：FAIL %d / WARN %d' % (len(fails), len(warns)))
    print()
    print('有 FAIL 就必须修完再交付。脚本验不了「抽掉行动者之后还剩什么」，那一步只能人工。')
    return 1 if fails else 0


# ---------------------------------------------------------------- --all


def cmd_all():
    pages = note_pages()
    hists = history_pages()
    print('== note 全库体检 ==')
    print()
    if not pages and not hists:
        print('wiki/notes/ 下没有 note 页。')
        return 0

    typed = defaultdict(int)
    rows = []
    for p in pages:
        fm, _ = parse_frontmatter(read(p))
        nt = strip_q(fm.get('note_type', '')) or '(未标)'
        typed[nt] += 1
        vers = strip_q(fm.get('version', ''))
        rows.append((os.path.basename(p)[:-3], nt, vers, len(glob.glob(os.path.join(HISTORY_DIR, os.path.splitext(os.path.basename(p))[0] + '-v*.md')))))

    print('现行版 %d 页 / 旧版 %d 份' % (len(pages), len(hists)))
    print()
    print('%-42s %-14s %-8s %s' % ('页面', 'note_type', 'version', '旧版数'))
    print('-' * 78)
    for nm, nt, ver, hn in rows:
        print('%-42s %-14s %-8s %s' % (nm[:42], nt, ver or '—', hn))
    print()
    print('分布：%s' % ' / '.join('%s %d' % (k, v) for k, v in sorted(typed.items())))
    print()

    # 孤儿旧版：_history 里没有对应现行版
    names = set(os.path.basename(p)[:-3] for p in pages)
    orphans = []
    for h in hists:
        base = re.sub(r'-v\d+$', '', os.path.splitext(os.path.basename(h))[0])
        if base not in names:
            orphans.append(os.path.basename(h))
    print('① 孤儿旧版（_history 里有，但当前版已不存在）')
    if orphans:
        for o in orphans:
            print('  %s %s' % (FAIL, o))
        print('  处理：要么补回当前版，要么连旧版一并删掉——留着没有 superseded_by 的去处。')
    else:
        print('  无。')
    print()

    # 逐页汇总 FAIL
    print('② 逐页 FAIL 汇总')
    bad = 0
    for p in pages + hists:
        issues = check_page(p)
        f = [i for i in issues if i[0] == FAIL]
        if f:
            bad += 1
            print('  %s %s（FAIL %d）' % (FAIL, p, len(f)))
            for _, area, msg in f[:4]:
                print('      %s' % msg)
    if not bad:
        print('  全部通过。')
    print()
    print('合计：%d 页有 FAIL。' % bad)
    return 1 if bad else 0


# ---------------------------------------------------------------- --chain


def cmd_chain(base):
    cur = os.path.join(NOTES_DIR, base + '.md')
    hists = sorted(glob.glob(os.path.join(HISTORY_DIR, base + '-v*.md')))
    if not os.path.exists(cur) and not hists:
        print('找不到与「%s」相关的 note 页。' % base)
        return 2
    print('== 版本链：%s ==' % base)
    print()
    items = []
    for p in hists + ([cur] if os.path.exists(cur) else []):
        fm, _ = parse_frontmatter(read(p))
        v = strip_q(fm.get('version', ''))
        items.append((int(v) if v.isdigit() else -1, p, fm))
    for v, p, fm in sorted(items, key=lambda x: x[0]):
        tag = '当前版' if p == cur else '旧版'
        print('v%-4s %-6s %s' % (v if v >= 0 else '?', tag, p))
        if p != cur:
            print('        searchable: %s   superseded_by: %s   valid_until: %s'
                  % (strip_q(fm.get('searchable', '')) or '(缺)',
                     strip_q(fm.get('superseded_by', '')) or '(缺)',
                     strip_q(fm.get('valid_until', '')) or '(缺)'))
        body = parse_frontmatter(read(p))[1]
        vh = find_section(body, '版本历史')
        if vh:
            rows = table_rows(vh)
            for r in rows[1:4]:
                print('        %s' % ' | '.join(r)[:90])
        print()
    print('判据：只有"照着做会出错"的改动才升版；旧版只读、不索引（07 §5）。')
    return 0


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(
        description='07 note 个人加工页自检器（只读）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--page', help='单页自检：给一个 note 页路径（含 _history/ 里的旧版）')
    ap.add_argument('--all', action='store_true', help='全库体检：两型分布 + 版本链 + FAIL 汇总')
    ap.add_argument('--chain', help='看一条方法论的版本链，给基名（不含 .md，不含 -vN）')

    args = ap.parse_args()

    if args.page:
        return cmd_page(args.page)
    if args.all:
        return cmd_all()
    if args.chain:
        return cmd_chain(args.chain)

    ap.print_help()
    print()
    print('至少给一个模式：--page / --all / --chain')
    return 2


if __name__ == '__main__':
    sys.exit(main())
