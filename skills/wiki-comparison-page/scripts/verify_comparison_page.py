#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_comparison_page.py — 04 comparison 对比页自检器（只读工具）

页型职责定义见 index.md；页面结构与校验按对应 Wiki Skill

用法：
  python3 verify_comparison_page.py --page wiki/comparisons/X.md   单页自检
  python3 verify_comparison_page.py --all                          全库体检 + FAIL 聚类
  python3 verify_comparison_page.py --dup                          与 concept/entity 页同名检测 + 跨页重复句
  python3 verify_comparison_page.py --scan                         从 raw 反扫对比线索，列出未建页候选

本脚本只读，不修改任何文件。
"""

import argparse
import glob
import io
import os
import re
import sys
from collections import Counter, defaultdict

if sys.version_info[0] < 3:
    print('需要 Python 3')
    sys.exit(2)

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

COMPARISON_DIR = 'wiki/comparisons'
RAW_GLOBS = ['raw/**/*.md']

FAIL = '[FAIL]'
WARN = '[WARN]'

# ---------------------------------------------------------------- 常量

COMPARISON_TYPES = {'select', 'partition'}

DECISION_SECTION = {'select': '怎么选', 'partition': '边界规则'}
OTHER_DECISION = {'select': '边界规则', 'partition': '怎么选'}

REQUIRED_SECTIONS = ['对比表', '判据来源', '相关']

# 一句话结论里的和稀泥词（04 §4.1）
BANNED_CONCLUSION = [
    '各有优劣', '各有利弊', '各有千秋', '见仁见智', '因人而异',
    '都有好处', '不能一概而论', '视情况而定', '看具体情况',
]

# 决策节里的推诿句式（04 §4.5）
DODGE_PATTERNS = [
    '由业务确认', '需业务确认', '由业务决定', '需业务决定', '由业务判断',
    '视具体情况而定', '视实际情况', '具体问题具体分析', '看实际需求',
    '根据实际情况判断', '需进一步确认', '再另行确认', '另行确认',
    '结合实际情况', '由团队自行判断',
]

# 缺口标注：合法地说明素材没给条件（04 §4.5）
GAP_MARKERS = ['素材未给出', '素材未说明', '素材没有给出', '素材只说明了', '素材未提供']

# 条件规则标记（04 §4.5）——启发式
CONDITION_MARKERS = [
    '若', '如果', '一旦', '优先', '≥', '<=', '>=', '＞', '满⾜',
    '则选', '则用', '用后者', '用前者', '两边都不', '都不选',
    '列全', '列不全', '能不能', '能否', '条件',
]

# 判据单元格的占位语
PLACEHOLDER_CELLS = {
    '', '—', '-', '–', '——', '/', 'n/a', 'na', 'tbd', '待补充', '待定',
    '视情况', '视具体情况', '无', '同上', '?', '？',
}

# 判据写成元注释的信号（04 §4.2）
META_COMMENT_MARKERS = ['仅作', '不能作为', '该课程说明', '课堂举例', '仅供参考', '不是行业']

# 疑似复述 concept 的信号（04 §4.10）
RESTATE_MARKERS = ['是一种', '的定义是', '主要功能', '由以下部分组成', '主要包括', '指的是']

# 「怎么选」里出现这些词，说明其实是划界型（04 §4.6）
COEXIST_MARKERS = ['可并存', '可以并存', '同时存在', '可以同时', '不冲突', '两者都要', '并存并']

# 禁用的来源标注方式
HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.S)
LABEL_RE = re.compile(r'〔([^〕]{1,120})〕')
WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)\]\]')
TODO_LINK_RE = re.compile(r'\[待创建:\s*\[\[([^\]|]+?)\]\]\]')
BAD_TODO_RE = re.compile(r'\[\[待创建\s*[：:]\s*([^\]|]+?)\]\]')
SEPARATOR_ROW_RE = re.compile(r'^:?-{2,}:?$')

# ---------------------------------------------------------------- 基础工具


def read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def strip_q(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def parse_frontmatter(text):
    """极简 YAML frontmatter 解析；返回 (dict, body)。"""
    if not text.startswith('---'):
        return {}, text
    lines = text.split('\n')
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end = i
            break
    if end is None:
        return {}, text

    fm = {}
    cur = None
    for line in lines[1:end]:
        if not line.strip() or line.strip().startswith('#'):
            continue
        m_block = re.match(r'^\s+-\s+(.*)$', line)
        if m_block and cur:
            if not isinstance(fm.get(cur), list):
                fm[cur] = []
            fm[cur].append(strip_q(m_block.group(1)))
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_\-]*)\s*:\s*(.*)$', line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        cur = k
        if v == '':
            fm[k] = []
        elif v.startswith('[') and v.endswith(']'):
            inner = v[1:-1].strip()
            fm[k] = [strip_q(x) for x in inner.split(',') if x.strip()] if inner else []
        else:
            fm[k] = strip_q(v)
    return fm, '\n'.join(lines[end + 1:])


def as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x]
    return [v] if v != '' else []


def all_sections(body):
    """返回 [(节名, 内容)]，只认 ## 级。"""
    out = []
    cur = None
    buf = []
    for line in body.split('\n'):
        m = re.match(r'^##\s+(.+?)\s*$', line)
        if m:
            if cur is not None:
                out.append((cur, '\n'.join(buf)))
            cur = m.group(1)
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur, '\n'.join(buf)))
    return out


def find_section(body, name):
    for n, c in all_sections(body):
        if n == name:
            return c
    return None


def parse_tables(text):
    """返回 [[row, ...], ...]，已去掉分隔行；每 row 是 cell 列表。"""
    tables = []
    rows = []
    for line in text.split('\n'):
        s = line.strip()
        if s.startswith('|') and s.endswith('|') and len(s) > 1:
            cells = [c.strip() for c in s[1:-1].split('|')]
            rows.append(cells)
        else:
            if rows:
                tables.append(rows)
                rows = []
    if rows:
        tables.append(rows)

    cleaned = []
    for t in tables:
        t2 = [r for r in t if not all(SEPARATOR_ROW_RE.match(c) for c in r)]
        if len(t2) >= 2:
            cleaned.append(t2)
    return cleaned


def get_conclusion(body):
    """取 H1 之后、第一个 ## 之前的引用块（一句话结论）。"""
    head = []
    for line in body.split('\n'):
        if re.match(r'^##\s', line):
            break
        head.append(line)
    out = []
    for line in head:
        s = line.strip()
        if s.startswith('>'):
            out.append(s.lstrip('>').strip())
        elif out:
            break
    return '\n'.join(out)


def check_anchors(body, level, area, add, allow_empty=True):
    """来源标注检查：〔〕 是否存在、是否残留 HTML 注释。"""
    labels = LABEL_RE.findall(body)
    comments = HTML_COMMENT_RE.findall(body)
    if comments:
        add(FAIL, area, '%s 残留 HTML 注释来源 %d 处（阅读视图不可见，改用行内 〔〕）'
            % (level, len(comments)))
    if not labels:
        add(FAIL, area, '%s没有任何 〔〕 位置标注（04 §4.11）' % level)
    return labels


def normalize_cell(s):
    return re.sub(r'\s+', '', s or '')


# ---------------------------------------------------------------- 单页自检


def check_page(path):
    problems = []

    def add(level, area, msg):
        problems.append((level, area, msg))

    if not os.path.exists(path):
        add(FAIL, '路径', '文件不存在：%s' % path)
        return problems, None

    text = read(path)
    fm, body = parse_frontmatter(text)
    name = os.path.splitext(os.path.basename(path))[0]

    # --- frontmatter ---
    if fm.get('type') != 'comparison':
        add(FAIL, 'frontmatter', 'type 应为 comparison，实为：%r' % fm.get('type'))

    title = fm.get('title') if isinstance(fm.get('title'), str) else ''
    if not title:
        add(FAIL, 'frontmatter', '缺少 title')

    ctype = fm.get('comparison_type') if isinstance(fm.get('comparison_type'), str) else ''
    if not ctype:
        add(FAIL, 'frontmatter',
            '缺少 comparison_type（select / partition，决定第五节写「怎么选」还是「边界规则」，见 04 §2）')
    elif ctype not in COMPARISON_TYPES:
        add(FAIL, 'frontmatter', 'comparison_type 取值非法：%r（应为 select / partition）' % ctype)

    sources = as_list(fm.get('sources'))
    if not sources:
        add(FAIL, 'frontmatter', 'sources 为空（对比页至少要 1 篇在做这个对比的来源）')
    else:
        for s in sources:
            if not s.endswith('.md'):
                add(WARN, 'frontmatter', 'sources 条目不是 .md 路径：%s' % s)
            elif not (os.path.exists(s) or os.path.exists(os.path.join('wiki', s))):
                add(WARN, 'frontmatter', 'sources 指向的页不存在：%s' % s)

    if not fm.get('status'):
        add(WARN, 'frontmatter', '缺少 status')

    if not as_list(fm.get('tags')):
        add(FAIL, 'frontmatter', 'tags 为空（至少一个共享主题标签）')

    for dep in ('author', 'created', 'updated'):
        if dep in fm:
            add(FAIL, 'frontmatter', '残留废弃字段 %s（frontmatter 以 04 §2 为准）' % dep)

    # --- H1 / 一句话结论 ---
    h1 = re.search(r'^#\s+(.+)$', body, re.M)
    if not h1:
        add(FAIL, 'H1', '缺少 H1 标题')
    else:
        h1t = h1.group(1).strip()
        if title and h1t != title:
            add(WARN, 'H1', 'H1（%s）与 frontmatter title（%s）不一致' % (h1t, title))
        if '与' not in h1t and 'vs' not in h1t.lower():
            add(WARN, 'H1', '对比页标题建议写成「A 与 B」形式，当前：%s' % h1t)

    concl = get_conclusion(body)
    if not concl:
        add(FAIL, '一句话结论', '缺少 H1 下方的一行引用块结论（04 §4.1）')
    else:
        hit = [w for w in BANNED_CONCLUSION if w in concl]
        if hit:
            add(FAIL, '一句话结论', '含和稀泥用语：%s（必须落到「什么条件下选谁」）' % '、'.join(hit))
        if len(concl) > 120:
            add(WARN, '一句话结论', '结论过长（%d 字），应为一句话' % len(concl))

    # --- 节骨架 ---
    secs = all_sections(body)
    sec_names = [n for n, _ in secs]

    for req in REQUIRED_SECTIONS:
        if req not in sec_names:
            add(FAIL, '节骨架', '缺少必需节：「%s」' % req)

    boundary = [n for n in sec_names if n.endswith('适用边界')]
    if len(boundary) != 2:
        add(WARN, '节骨架', '「X 的适用边界」应有 2 节（A、B 各一），实际 %d 节' % len(boundary))

    if ctype in COMPARISON_TYPES:
        want = DECISION_SECTION[ctype]
        other = OTHER_DECISION[ctype]
        if want not in sec_names:
            add(FAIL, '节骨架',
                'comparison_type=%s 必须有「%s」节，实际没有' % (ctype, want))
        if other in sec_names:
            add(FAIL, '类型错配',
                'comparison_type=%s 却出现了「%s」节——同一页两套决策节，类型未定（04 §2）'
                % (ctype, other))
    else:
        if '怎么选' in sec_names and '边界规则' in sec_names:
            add(FAIL, '类型错配',
                '同时出现「怎么选」与「边界规则」——两节只能有其一，先定 comparison_type（04 §2）')

    if sec_names:
        if sec_names[0] != '对比表':
            add(WARN, '节骨架', '「对比表」应为第一个 ## 节')
        if sec_names[-1] != '相关':
            add(WARN, '节骨架', '「相关」应为最后一个 ## 节')

    # --- 对比表 ---
    ts = parse_tables(find_section(body, '对比表') or '')
    dims = []
    if not ts:
        add(FAIL, '对比表', '「对比表」节里没有可解析的表格')
    else:
        if len(ts) > 1:
            add(WARN, '对比表', '「对比表」节里有 %d 张表，应只有一张' % len(ts))
        t = ts[0]
        header = t[0]
        ncol = len(header)
        if ncol != 4:
            add(FAIL, '对比表', '表头应为 4 列「维度 | A | B | 判据」，实际 %d 列：%s'
                % (ncol, ' | '.join(header)))
        if not any('判据' in c for c in header):
            add(FAIL, '对比表', '表头缺「判据」列——没有判据的对比只是参数罗列，无法解析（04 §4.2）')
        if not any('维度' in c for c in header):
            add(WARN, '对比表', '表头第一列建议为「维度」')

        jidx = None
        for i, c in enumerate(header):
            if '判据' in c:
                jidx = i
                break

        rows = t[1:]
        if len(rows) < 3:
            add(WARN, '对比表', '只有 %d 个维度行（少于 3 个时先怀疑是不是在硬凑对比页）' % len(rows))

        for r in rows:
            dim = r[0].strip() if r else ''
            dims.append(dim)
            if not dim:
                add(WARN, '对比表', '有空维度名')
            if jidx is None:
                continue
            cell = r[jidx].strip() if jidx < len(r) else ''
            if cell.lower() in PLACEHOLDER_CELLS:
                add(FAIL, '对比表', '维度「%s」的判据为空或占位（%r）' % (dim, cell))
            elif any(m in cell for m in META_COMMENT_MARKERS):
                add(WARN, '对比表',
                    '维度「%s」的判据写成了元注释（%r）——判据要写「用什么可观察事实判定选哪边」'
                    % (dim, cell[:40]))

    # --- 决策节 ---
    # ctype 缺失、或声明的节不存在时也要查：回落到实际存在的决策节，
    # 否则节名写错正好会绕过检查——而那正是最该报的情况
    dec_name = DECISION_SECTION.get(ctype)
    if dec_name is None or dec_name not in sec_names:
        for cand in ('怎么选', '边界规则'):
            if cand in sec_names:
                dec_name = cand
                break
    if dec_name:
        content = find_section(body, dec_name)
        if content is not None:
            body_text = re.sub(r'[〔（(][^〕）)]*[〕）)]', '', content)
            if len(body_text.strip()) < 20:
                add(FAIL, '决策节', '「%s」节内容过短（%d 字），不可能给出可照做的规则'
                    % (dec_name, len(body_text.strip())))
            has_gap = any(g in body_text for g in GAP_MARKERS)
            dodge = [p for p in DODGE_PATTERNS if p in body_text]
            if dodge and not has_gap:
                add(FAIL, '决策节',
                    '「%s」节含推诿句式：%s——决策节是唯一产出，退化成推回业务等于本页无产出（04 §4.5）'
                    % (dec_name, '、'.join(dodge)))
            elif dodge and has_gap:
                add(WARN, '决策节',
                    '「%s」节含推诿句式：%s（虽已标素材缺口，仍建议把已有条件写出来）'
                    % (dec_name, '、'.join(dodge)))
            if not any(m in body_text for m in CONDITION_MARKERS) and not has_gap:
                add(FAIL, '决策节',
                    '「%s」节没有条件规则（应出现「若/如果/一旦/优先/则选/≥」这类可判定条件）'
                    % dec_name)
            if dec_name == '边界规则':
                for bad in ['选 A 还是选 B', '优先用', '二选一', '选哪一个', '只能选']:
                    if bad in body_text:
                        add(FAIL, '决策节', '划界型不应出现二选一表述：%r' % bad)
            if dec_name == '怎么选':
                coexist = [w for w in COEXIST_MARKERS if w in body_text]
                if coexist:
                    add(WARN, '类型错配',
                        '「怎么选」节出现 %s——出现这类表述说明两者可以共存，'
                        '本页很可能是划界型（comparison_type: partition + 「边界规则」），'
                        '而不是择一型（04 §4.6）' % '、'.join(coexist))

    # --- 判据来源 ---
    src_content = find_section(body, '判据来源')
    if src_content is not None:
        sts = parse_tables(src_content)
        if not sts:
            add(FAIL, '判据来源', '「判据来源」节里没有表格（应一行一个对比维度）')
        else:
            st = sts[0]
            if len(st[0]) != 3:
                add(FAIL, '判据来源', '表头应为 3 列「对比维度 | 出处 | 置信度」，实际 %d 列'
                    % len(st[0]))
            src_dims = [r[0].strip() for r in st[1:] if r]
            norm_src = {normalize_cell(d) for d in src_dims if d}
            norm_tab = {normalize_cell(d) for d in dims if d}

            missing = norm_tab - norm_src
            extra = norm_src - norm_tab
            if missing:
                add(FAIL, '判据来源', '对比表有这些维度但来源表没有：%s（每行判据都要能追到出处）'
                    % '、'.join(sorted(missing)))
            if extra:
                add(WARN, '判据来源', '来源表多出对比表里没有的维度：%s' % '、'.join(sorted(extra)))

            if len(st[0]) >= 3:
                for r in st[1:]:
                    if len(r) < 3 or not r[1].strip():
                        add(FAIL, '判据来源', '维度「%s」没有出处' % (r[0] if r else '?'))
                        continue
                    conf = r[2].strip().upper()
                    if conf not in ('EXTRACTED', 'INFERRED'):
                        add(WARN, '判据来源',
                            '维度「%s」的置信度 %r 不在 EXTRACTED / INFERRED 内' % (r[0], conf))
                    out = r[1]
                    if not re.search(r'(sources/|〔|§|第\s*\d+\s*段)', out):
                        add(WARN, '判据来源',
                            '维度「%s」的出处看不出具体位置（应含 source 链接或 §/第 N 段）' % r[0])

    # --- 来源标注 ---
    labels = check_anchors(body, '正文', '来源标注', add)
    if len(sources) >= 2 and labels:
        short_style = sum(1 for l in labels
                          if l.strip().startswith('§') or re.match(r'^第\s*\d+\s*段', l.strip()))
        if short_style == len(labels):
            add(WARN, '来源标注',
                '本页有 %d 个来源，但所有 〔〕 标注都没带来源短名——读者不知道去哪个 source 核（04 §4.11）'
                % len(sources))

    # --- 相关 ---
    rel = find_section(body, '相关')
    if rel is not None:
        links = [x.strip() for x in WIKILINK_RE.findall(rel)]
        todo = [x.strip() for x in TODO_LINK_RE.findall(rel)]
        if len(links) + len(todo) < 3:
            add(WARN, '相关', '「相关」节只有 %d 个链接（建议 3–6 个）' % (len(links) + len(todo)))
        if len(links) + len(todo) > 6:
            add(WARN, '相关', '「相关」节有 %d 个链接（建议 3–6 个）' % (len(links) + len(todo)))
        self_targets = {name} | ({title} if title else set())
        for t in links:
            if t in self_targets:
                add(FAIL, '相关', '「相关」节链接到了自己（[[%s]]）——那是 A 或 B 的 concept 页，不是本页' % t)

    # --- 待创建格式 ---
    bad_todo = BAD_TODO_RE.findall(body)
    if bad_todo:
        for b in set(bad_todo):
            add(FAIL, '链接格式',
                '「待创建」写进了 wikilink 内部：[[待创建：%s]] → 正确写法 [待创建: [[%s]]]' % (b, b))

    # --- 死链 ---
    idx = page_index()
    todo_targets = set(TODO_LINK_RE.findall(body))
    self_names = {name}
    if title:
        self_names.add(title)
    for t in set(WIKILINK_RE.findall(body)):
        t = t.strip()
        if t and t not in idx and t not in todo_targets and t not in self_names:
            add(WARN, '死链', '[[%s]] 目标页不存在，也没标 [待创建:]' % t)

    # --- 复述 concept（启发式）---
    for n in boundary:
        c = find_section(body, n) or ''
        for m in RESTATE_MARKERS:
            if m in c:
                add(WARN, '复述风险',
                    '「%s」节出现 %r——这一节该写「什么时候选它」，不是「它是什么」（04 §4.10）'
                    % (n, m))
                break

    return problems, fm


def page_index():
    """全库页面名 → 路径。"""
    idx = {}
    for p in glob.glob('wiki/**/*.md', recursive=True):
        idx[os.path.splitext(os.path.basename(p))[0]] = p
    return idx


def comparison_pages():
    return sorted(glob.glob(os.path.join(COMPARISON_DIR, '*.md')))


def report(path, problems):
    fails = [p for p in problems if p[0] == FAIL]
    warns = [p for p in problems if p[0] == WARN]
    print('===== %s =====' % path)
    if not problems:
        print('  通过：0 FAIL / 0 WARN')
        return 0, 0
    for level, area, msg in problems:
        print('  %s %-10s %s' % (level, area, msg))
    print('  -- 小计 FAIL %d / WARN %d' % (len(fails), len(warns)))
    return len(fails), len(warns)


# ---------------------------------------------------------------- 模式


def cmd_page(path):
    problems, _ = check_page(path)
    f, w = report(path, problems)
    if f:
        print()
        print('有 FAIL，必须修完再交付（04 §7.1）。')
        return 1
    return 0


def cmd_all(limit):
    pages = comparison_pages()
    if not pages:
        print('错误：%s 下没有页面' % COMPARISON_DIR)
        return 2

    print('== comparison 全库体检 ==')
    print('共 %d 页' % len(pages))
    print()

    total_f = total_w = 0
    cluster = Counter()
    worst = []
    for p in pages:
        problems, _ = check_page(p)
        f, w = report(p, problems)
        total_f += f
        total_w += w
        for level, area, _ in problems:
            if level == FAIL:
                cluster[area] += 1
        if f:
            worst.append((f, p))
        print()

    print('== 汇总 ==')
    print('合计 FAIL %d / WARN %d' % (total_f, total_w))
    print()
    if cluster:
        print('FAIL 聚类（共性问题，按命中页数排序）：')
        for area, n in cluster.most_common():
            print('  %-12s %d 页' % (area, n))
        print()
    if worst:
        print('FAIL 最多的页：')
        for f, p in sorted(worst, reverse=True)[:limit]:
            print('  %d 个 FAIL  %s' % (f, p))
        print()
    print('处理顺序建议：先修「类型错配」与「判据来源对不上」（结构性），再修来源标注与链接（格式性）。')
    return 0


def sentences_of(body):
    raw = re.split(r'[。！？\n]', body)
    out = []
    for s in raw:
        s = s.strip()
        s = re.sub(r'^[\-\*\d\.\)\s|]+', '', s)
        s = LABEL_RE.sub('', s)
        if len(s) >= 25:
            out.append(s)
    return out


def cmd_dup(threshold, limit):
    pages = comparison_pages()
    idx = page_index()

    print('== comparison 查重 ==')
    print('全库可解析页面：%d 个；comparison 页：%d 个' % (len(idx), len(pages)))
    print()

    # ① 与 concept / entity / solution 页 title 重合
    other_titles = defaultdict(list)
    for p in glob.glob('wiki/**/*.md', recursive=True):
        if os.sep + 'comparisons' + os.sep in p:
            continue
        text = read(p)
        fm, _ = parse_frontmatter(text)
        t = fm.get('title') if isinstance(fm.get('title'), str) else ''
        if t:
            other_titles[t].append(p)

    print('-- ① 与 concept / entity / solution 页的 title 重合 --')
    any_hit = False
    for p in pages:
        text = read(p)
        fm, _ = parse_frontmatter(text)
        t = fm.get('title') if isinstance(fm.get('title'), str) else ''
        if not t:
            continue
        if t in other_titles:
            any_hit = True
            print('  %s 「%s」' % (FAIL, t))
            print('       %s' % p)
            for q in other_titles[t]:
                print('       同名： %s' % q)
            print('       处理（04 §4.10）：不是"合并"，是分工——concept 页留「是什么、怎么运作」，')
            print('       对比页只留「差异与选择」，且两页 title 不能相同。')
    if not any_hit:
        print('  未发现 title 完全重合。')
    print()

    # ② 跨页重复句
    print('-- ② 跨 comparison 页重复句（≥25 字，逐字相同） --')
    holders = defaultdict(list)
    for p in pages:
        text = read(p)
        _, body = parse_frontmatter(text)
        name = os.path.splitext(os.path.basename(p))[0]
        for s in set(sentences_of(body)):
            holders[LABEL_RE.sub('', s)].append(name)

    dup = {k: v for k, v in holders.items() if len(v) >= threshold}
    if not dup:
        print('  未发现跨页重复的完整句子。')
        print('  注意：只比对逐字相同的句子；换了说法的重复查不出来，仍需人工看。')
    else:
        for k, v in sorted(dup.items(), key=lambda x: -len(x[1]))[:limit]:
            print('  出现在 %d 页：%s' % (len(v), ' · '.join(v)))
            print('    「%s」' % k[:70])
        print()
        print('  处理：留一处展开，其余改成一句 + 出链。')
    return 0


SCAN_PATTERNS = [
    # 内联：拉丁词 vs 拉丁词（左边界必须非中文，避免切进句子中间）
    re.compile(r'(?<![\u4e00-\u9fa5])([A-Za-z0-9][A-Za-z0-9\-\._ ]{1,14}?)\s*(?:vs\.?|VS|Vs)\s*'
               r'([A-Za-z0-9][A-Za-z0-9\-\._ ]{1,14})'),
    # 内联：中文「A 与/和 B 的区别」
    re.compile(r'(?<![\u4e00-\u9fa5])([\u4e00-\u9fa5A-Za-z0-9\-]{2,12})\s*(?:与|和|跟)\s*'
               r'([\u4e00-\u9fa5A-Za-z0-9\-]{2,12}?)\s*(?:的)?\s*(?:区别|差异|对比|不同)'),
]

# 表格表头第一列出现这些词，说明这张表在做成组对照（结构化课程材料里最可靠的信号）
TABLE_CONTRAST_HEADS = {
    '对比维度', '对比项', '比较维度', '维度', '评估方式', '对比', '对比类型', '对比内容',
}

# 这些列名不是候选，是结果列
TABLE_RESULT_COLS = {'提升', '变化', '说明', '备注', '判据', '差异', '结论'}

# 这些列名是通用属性名，不是候选对象（说明这张表不是在对照两个候选）
TABLE_GENERIC_COLS = {
    '定义', '适用场景', '特点', '作用', '评分方式', '类型', '描述', '优势', '劣势',
    '能力', '功能', '方法', '示例', '举例', '目标', '输入', '输出', '指标', '指标定义',
    '评估内容', '阶段', '评估维度', '评估方式', '定义与作用', '使用场景', '核心逻辑',
}

# 含这些词组的候选基本是句子碎片，不是具名候选
SCAN_FUNCTION_WORDS = [
    '有什么', '的是', '之间', '到底', '其实', '没有', '什么', '怎么', '因为', '所以',
    '以及', '或者', '这个', '那个', '一下', '他们', '我们', '你们', '就是', '还是',
    '但是', '如果', '不是', '可以', '可能', '知道', '觉得', '感觉', '对于', '关于',
    '最后', '首先', '那么', '来说', '一点', '事情', '然后', '这样', '而是', '一个',
]

# 表格表头行：| 第一列 | 其余列 |
TABLE_HEADER_RE = re.compile(r'^\|\s*([^|]{1,16}?)\s*\|(.+?)\|\s*$')

SCAN_STOP = {'等等', '什么', '这个', '那个', '一下', '这边', '那边', '的话', '等等等'}

# 04 §1 已定：面试资料不作为生成依据，扫描默认跳过
SCAN_EXCLUDE_DIRS = ['raw/interview_question']

# 候选词的长度上限：超过这个长度基本都是句子碎片，不是候选名
SCAN_MAX_TOKEN = 14

# 候选词开头的口水词
SCAN_LEAD_NOISE = [
    '你觉得', '请问', '关于', '那么', '所以说', '就是', '然后', '这个', '那个',
    '一下', '大家', '我们', '你们', '其实', '所以', '因为', '但是', '如果',
]

SCAN_BAD_CHARS = set('，。？！、；：""' + "''" + '（）()【】《》…—·\u3000 \t')


def clean_token(s):
    s = s.strip(' \t\u3000，。、：:；;()（）')
    for n in SCAN_LEAD_NOISE:
        if s.startswith(n) and len(s) > len(n) + 1:
            s = s[len(n):]
            break
    return s.strip()


def ok_token(s):
    if len(s) < 2 or len(s) > SCAN_MAX_TOKEN:
        return False
    if any(c in SCAN_BAD_CHARS for c in s):
        return False
    if s in SCAN_STOP:
        return False
    for w in SCAN_FUNCTION_WORDS:
        if w in s:
            return False
    return True


def short_name(s):
    """去掉括号内容与空白，用于与已建页比对。"""
    s = re.sub(r'[（(][^）)]*[）)]', '', s)
    return re.sub(r'\s+', '', s).strip()


def cmd_scan(limit, include_interview=False):
    files = []
    for g in RAW_GLOBS:
        files.extend(glob.glob(g, recursive=True))
    files = sorted(set(files))
    if not include_interview:
        files = [f for f in files
                 if not any(f.replace(os.sep, '/').startswith(d) for d in SCAN_EXCLUDE_DIRS)]
    if not files:
        print('错误：%s 下没有素材' % RAW_GLOBS)
        return 2

    # ① 表格里的对比组
    groups = defaultdict(lambda: {'n': 0, 'files': Counter(), 'sample': ''})
    # ② 内联对比句式
    pairs = defaultdict(lambda: {'n': 0, 'files': Counter(), 'sample': ''})

    for f in files:
        base = os.path.basename(f)
        for line in read(f).split('\n'):
            stripped = line.strip()
            is_table_row = stripped.startswith('|')
            if is_table_row:
                m = TABLE_HEADER_RE.match(stripped)
                if m:
                    head = m.group(1).strip()
                    if head in TABLE_CONTRAST_HEADS:
                        cols = [c.strip() for c in m.group(2).split('|') if c.strip()]
                        cols = [c for c in cols
                                if c not in TABLE_RESULT_COLS and c not in TABLE_GENERIC_COLS]
                        if len(cols) >= 2:
                            key = tuple(cols)
                            rec = groups[key]
                            rec['n'] += 1
                            rec['files'][base] += 1
                            if not rec['sample']:
                                rec['sample'] = stripped[:110]
                # 表格行不再进内联扫描：单元格里的「与…区别」多是正文，不是候选
                continue
            for pat in SCAN_PATTERNS:
                for mm in pat.finditer(line):
                    a = clean_token(mm.group(1))
                    b = clean_token(mm.group(2))
                    if not (ok_token(a) and ok_token(b)) or a == b:
                        continue
                    key = tuple(sorted([a, b]))
                    rec = pairs[key]
                    rec['n'] += 1
                    rec['files'][base] += 1
                    if not rec['sample']:
                        rec['sample'] = stripped[:110]

    built_pages = [(os.path.basename(p), read(p)) for p in comparison_pages()]

    def is_built(names):
        shorts = [short_name(x) for x in names if short_name(x)]
        if len(shorts) < 2:
            return False
        return any(all(s in t for s in shorts) for _, t in built_pages)

    print('== 从 raw 反扫对比线索 ==')
    print('已扫 %d 份素材%s' % (len(files),
                            '' if include_interview else '（已跳过 raw/interview_question/，04 §1）'))
    print()

    rows = sorted(groups.items(), key=lambda kv: -kv[1]['n'])
    print('-- ① 表格里的对比组（素材在成组对照，最可靠）--')
    print()
    if not rows:
        print('  没有扫到「对比维度 | A | B」这类成组对照的表头。')
    else:
        for cols, rec in rows[:limit]:
            flag = '已建' if is_built(cols) else '未建'
            print('  [%s]  %s' % (flag, ' · '.join(cols)))
            for fn, n in rec['files'].most_common(3):
                print('          %s' % fn)
            if rec['sample']:
                print('          %s' % rec['sample'])
    print()

    prows = sorted(pairs.items(), key=lambda kv: -kv[1]['n'])
    print('-- ② 内联对比句式（A vs B / A 与 B 的区别）--')
    print()
    if not prows:
        print('  没有扫到内联对比句式。')
    else:
        for (a, b), rec in prows[:limit]:
            flag = '已建' if is_built([a, b]) else '未建'
            print('  [%s]  %s 与 %s   （%d 次，%s）'
                  % (flag, a, b, rec['n'], '、'.join(list(rec['files'])[:2])))
            if rec['sample']:
                print('          %s' % rec['sample'])
    print()

    print('== 汇总 ==')
    print('成组对照 %d 组（未建 %d），内联句式 %d 组（未建 %d）'
          % (len(rows), sum(1 for c, _ in rows if not is_built(c)),
             len(prows), sum(1 for (a, b), _ in prows if not is_built([a, b]))))
    print()
    print('处理：先按 04 §1 三条信号判「该不该建页」——')
    print('      这两个候选会在同一个决策点上被挑吗？不会 → 只出链，不建页。')
    print('      ⚠️ 本扫描只认显式句式与成组表头；口语转写里的自然表述（「这个和那个有什么不一样」）')
    print('         扫不到，需人工扫正文。')
    return 0


# ---------------------------------------------------------------- CLI


def main():
    ap = argparse.ArgumentParser(
        description='04 comparison 对比页自检器（只读）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='例：python3 verify_comparison_page.py --page "wiki/comparisons/X.md"')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--page', metavar='PATH', help='单页自检')
    g.add_argument('--all', action='store_true', help='全库体检 + FAIL 聚类')
    g.add_argument('--dup', action='store_true', help='查重：title 重合 + 跨页重复句')
    g.add_argument('--scan', action='store_true', help='从 raw 反扫对比线索，列出未建页候选')
    ap.add_argument('--limit', type=int, default=30, help='列表条数上限（默认 30）')
    ap.add_argument('--threshold', type=int, default=2, help='重复句报警阈值（默认同一句出现 ≥2 页）')
    ap.add_argument('--include-interview', action='store_true',
                    help='--scan 时把 raw/interview_question/ 也算进来（默认跳过，见 04 §1）')

    args = ap.parse_args()

    if args.page:
        return cmd_page(args.page)
    if args.all:
        return cmd_all(args.limit)
    if args.dup:
        return cmd_dup(args.threshold, args.limit)
    if args.scan:
        return cmd_scan(args.limit, args.include_interview)
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
