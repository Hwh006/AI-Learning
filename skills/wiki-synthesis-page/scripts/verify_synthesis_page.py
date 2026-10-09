#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_synthesis_page.py — 05 synthesis 综合页自检器（只读工具）

页型职责定义见 index.md；页面结构与校验按对应 Wiki Skill

用法：
  python3 verify_synthesis_page.py --page wiki/synthesis/X.md      单页自检
  python3 verify_synthesis_page.py --all                            全库体检 + FAIL 聚类
  python3 verify_synthesis_page.py --probe                          回扫 concept 页，列出有料但未综合的主题
  python3 verify_synthesis_page.py --premises wiki/synthesis/X.md   抽支撑来源表，检查撤前提列
  python3 verify_synthesis_page.py --dup                            与 concept 页 title 重合 + 跨页重复句

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

SYNTHESIS_DIR = 'wiki/synthesis'
CONCEPT_DIR = 'wiki/concepts'
SOURCE_DIR = 'wiki/sources'

FAIL = '[FAIL]'
WARN = '[WARN]'

# ---------------------------------------------------------------- 常量（05 规范）

# 7 个 ## 节，固定顺序（05 §3）
REQUIRED_SECTIONS = ['论证链', '支撑来源', '反面证据', '适用域', '不确定性', '待验证', '相关']

# 旧规范留下的节名，出现即提示（05 §4.10 / 03 v2 已收口）
LEGACY_SECTIONS = ['不同素材中的观点', '新入库素材中的补充说法', '支撑证据', '结论']

# 一句话结论的禁用语（05 §4.1）
CONCLUSION_BANNED = [
    '很重要', '需要注意', '影响很大', '非常关键', '值得重视', '要充分考虑',
    '因人而异', '视情况而定', '至关重要', '不可忽视', '有重要意义',
    '具有重要', '趋势明显', '大有可为', '值得关注',
]

# 占位语
PLACEHOLDER = {
    '', '—', '-', '–', '——', '/', 'n/a', 'na', 'tbd', '待补充', '待定',
    '无', '同上', '?', '？', '暂无', '略', '略。', '（略）', '待完善',
}

# 合法地说明素材没给（不算占位）
GAP_MARKERS = [
    '素材未给出', '素材未说明', '素材没有给出', '素材只说明了', '素材未提供',
    '资料未覆盖', '素材未涉及', '素材中没有', '素材没有给',
]

# 适用域里的空话（05 §4.5）
APPLY_BANNED = [
    '一般情况都适用', '任何情况都适用', '普遍适用', '均适用',
    '适用范围很广', '没有明显边界', '基本都适用',
]

# 「撤掉它，结论还成立吗」列的判定（05 §4.3）
NO_MARKERS = ['否', '不成立', '不行', '推不出', '缺它就', '去掉就', '无法成立']
YES_PAT = re.compile(r'^[\*\s]*是[\s\*，,。—\-—(（]')

# 论证链里的推导标记（05 §4.2）——启发式
DERIVE_MARKERS = ['因此', '所以', '合起来', '推出', '→', '综上', '可见', '这说明', '因而']

# 反面证据里必须真有一条否定/削弱（05 §4.4）
COUNTER_MARKERS = ['削弱', '相反', '反驳', '不支持', '矛盾', '顶牛', '冲突', '反例',
                   '不成立', '反向', '与…相反', '却', '但素材', '然而']

# 来源标注
LABEL_RE = re.compile(r'〔([^〕]{1,140})〕')
HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.S)
WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)\]\]')
TODO_LINK_RE = re.compile(r'\[待创建:\s*\[\[([^\]|]+?)\]\]\]')
BAD_TODO_RE = re.compile(r'\[\[待创建\s*[：:]\s*([^\]|]+?)\]\]')
SEPARATOR_ROW_RE = re.compile(r'^:?-{2,}:?$')

WRONG_DIR_MARKERS = [SOURCE_DIR + '/', CONCEPT_DIR + '/']

MAX_CONCLUSION_LEN = 200

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
    """把 frontmatter 里的列表字段拆成元素列表。

    支持三种实际写法：
        sources: [a.md, b.md]              单行数组
        sources: [a.md,                    多行数组（续行被拼成一行）
                  b.md]
        sources:                           dash 列表（续行拼成 "- a.md - b.md"）
          - a.md
          - b.md
    """
    v = (value or '').strip()
    if not v:
        return []
    v = v.strip('[]')
    # dash 列表：把行首或空格后的 "- " 统一成逗号分隔
    v = re.sub(r'(^|\s)-\s+', r'\1,', v)
    out = []
    for p in v.split(','):
        p = p.strip().strip('"\'').strip()
        if p:
            out.append(p)
    return out


def find_section(body, name):
    """返回某 ## 节的正文（到下一个同/更高级标题为止），找不到返回 None。"""
    lines = body.split('\n')
    start = None
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


def all_sections(body):
    return [m.group(1).strip() for m in re.finditer(r'^##\s+(.+)$', body, re.M)]


def section_positions(body):
    return {m.group(1).strip(): m.start() for m in re.finditer(r'^##\s+(.+)$', body, re.M)}


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


def text_len_wo_marks(s):
    return len(re.sub(r'[〔〕\[\]\(\)（）\s\*_`·—\-]', '', s or ''))


def page_index():
    idx = set()
    for p in glob.glob('wiki/**/*.md', recursive=True):
        idx.add(os.path.splitext(os.path.basename(p))[0])
    return idx


def synthesis_pages():
    return sorted(glob.glob(os.path.join(SYNTHESIS_DIR, '*.md')))


def concept_pages():
    return sorted(glob.glob(os.path.join(CONCEPT_DIR, '*.md')))


def norm_key(s):
    return re.sub(r'[\s\-_·、，。：:；;（）()\[\]【】]', '', (s or '')).lower()


# ---------------------------------------------------------------- 单页检查


def check_page(path, verbose=True):
    issues = []

    def add(level, area, msg):
        issues.append((level, area, msg))

    if not os.path.exists(path):
        add(FAIL, 'file', '文件不存在：%s' % path)
        return issues

    text = read(path)
    fm, body = parse_frontmatter(text)
    name = os.path.splitext(os.path.basename(path))[0]

    # --- frontmatter ---
    if not fm:
        add(FAIL, 'frontmatter', '缺少 frontmatter')
    else:
        if fm.get('type') != 'synthesis':
            add(FAIL, 'frontmatter', 'type 应为 synthesis，实际：%r' % fm.get('type'))
        if not fm.get('title'):
            add(FAIL, 'frontmatter', '缺少 title')
        else:
            t = strip_q(fm['title'])
            if not re.search(r'[\u4e00-\u9fa5]', t):
                add(WARN, 'frontmatter', 'title 应为中文标题：%r' % t)
            if re.search(r'汇总|总结|综述$', t):
                add(WARN, 'frontmatter',
                    'title 像"总结/汇总"（%r）——本页是结论，不是汇总；命名依据是结论主题' % t)
        tags = parse_list_field(fm.get('tags', ''))
        if not tags:
            add(FAIL, 'frontmatter', '缺少 tags')
        if fm.get('derived', '').lower() != 'true':
            add(FAIL, 'frontmatter', '缺少 derived: true（ingest 靠它跳过本页，否则形成自证环）')
        for dead in ('created', 'updated', 'author', 'confidence'):
            if dead in fm:
                add(FAIL, 'frontmatter', '废弃字段 %s（01/02/03/04 v2 已废）' % dead)

        srcs = parse_list_field(fm.get('sources', ''))
        if len(srcs) < 3:
            add(FAIL, 'frontmatter', 'sources 少于 3 条（实际 %d）：本页是综合，前提必须 ≥3' % len(srcs))
        seen = set()
        for s in srcs:
            if not s.endswith('.md'):
                add(WARN, 'frontmatter', 'sources 条目不是 .md 路径：%s' % s)
            else:
                s_norm = s.replace('\\', '/').lstrip('./')
                if not s_norm.startswith('sources/'):
                    add(FAIL, 'frontmatter',
                        'sources 里放了非 source 页：%s（可回溯链必须是「本页 → source 页 → raw」）' % s)
                # sources 字段里的路径相对 wiki/ 书写
                if not any(os.path.exists(c) for c in (s_norm, os.path.join('wiki', s_norm))):
                    add(WARN, 'frontmatter', 'sources 指向的页不存在：%s' % s)
            k = norm_key(s)
            if k in seen:
                add(WARN, 'frontmatter', 'sources 有重复项：%s' % s)
            seen.add(k)

    # --- 引言块（一句话结论） ---
    first_h2 = body.find('\n## ')
    head = body if first_h2 < 0 else body[:first_h2]
    quote_lines = [l.strip() for l in head.split('\n') if l.strip().startswith('>')]
    if not quote_lines:
        add(FAIL, '一句话结论', '第一个 ## 之前没有引言块（> 一句话结论）——结论是定理的陈述，必须有')
    else:
        conclusion = ' '.join(l.lstrip('> ').strip() for l in quote_lines if l.strip() != '>')
        if not conclusion.strip():
            add(FAIL, '一句话结论', '引言块是空的')
        else:
            hit = [w for w in CONCLUSION_BANNED if w in conclusion]
            if hit:
                add(FAIL, '一句话结论', '含不可证伪的表述：%s（05 §4.1）' % '、'.join(hit))
            if conclusion.rstrip().endswith('？') or conclusion.rstrip().endswith('?'):
                add(FAIL, '一句话结论', '结论写成了问句，无法判真假（05 §4.1）')
            if text_len_wo_marks(conclusion) > MAX_CONCLUSION_LEN:
                add(WARN, '一句话结论', '结论过长（%d 字）——一句话结论应是一句'
                    % text_len_wo_marks(conclusion))

    # --- 节骨架 ---
    secs = all_sections(body)
    pos = section_positions(body)
    missing = [s for s in REQUIRED_SECTIONS if s not in secs]
    if missing:
        add(FAIL, '节骨架', '缺节：%s（05 §3 固定 7 节）' % '、'.join(missing))
    legacy = [s for s in secs if s in LEGACY_SECTIONS]
    if legacy:
        add(FAIL, '节骨架', '出现旧节名：%s' % '、'.join(legacy))
    extra = [s for s in secs if s not in REQUIRED_SECTIONS and s not in LEGACY_SECTIONS]
    if extra:
        add(WARN, '节骨架', '出现骨架外的节：%s（05 §3 只有 7 节）' % '、'.join(extra))
    present = [s for s in REQUIRED_SECTIONS if s in pos]
    if len(present) >= 2:
        order = [pos[s] for s in present]
        if order != sorted(order):
            add(FAIL, '节骨架', '节顺序不符（应为 %s）' % ' → '.join(REQUIRED_SECTIONS))

    # --- 论证链 ---
    chain = find_section(body, '论证链')
    if chain is not None:
        steps = re.findall(r'^\s*(?:\d+[\.、)]|[-*])\s+\S', chain, re.M)
        if len(steps) < 2:
            add(FAIL, '论证链', '少于 2 个步骤 —— 论证链的价值在步骤间的推导，单段陈述不算')
        if not any(m in chain for m in DERIVE_MARKERS):
            add(WARN, '论证链', '没有推导标记（因此/所以/→/合起来）——可能只是把前提各写一遍')
        if not LABEL_RE.search(chain):
            add(WARN, '论证链', '论证链里没有 〔〕 位置标注 —— 每步应能指到某条前提')

    # --- 支撑来源 ---
    sup = find_section(body, '支撑来源')
    if sup is not None:
        rows = table_rows(sup)
        if not rows:
            add(FAIL, '支撑来源', '不是表格（05 §4.3 要求三列表）')
        else:
            header = rows[0]
            data = rows[1:]
            if len(header) < 3:
                add(FAIL, '支撑来源', '表格少于 3 列 —— 必须有「撤掉它，结论还成立吗」列')
            else:
                h3 = header[2]
                if not ('撤' in h3 and '成立' in h3):
                    add(FAIL, '支撑来源',
                        '第三列表头应为「撤掉它，结论还成立吗」，实际：%r' % h3)
            if len(data) < 3:
                add(FAIL, '支撑来源', '数据行少于 3 行（实际 %d）' % len(data))
            no_cnt = yes_cnt = unk_cnt = yes_unmarked = 0
            for r in data:
                if len(r) < 3:
                    unk_cnt += 1
                    continue
                c = r[2]
                if any(m in c.replace('是否', '') for m in NO_MARKERS):
                    no_cnt += 1
                elif YES_PAT.match(c.strip()) or c.strip().startswith('是'):
                    yes_cnt += 1
                    if not any(k in c for k in ('佐证', '装饰', '可删', '不影响', '已标注')):
                        yes_unmarked += 1
                else:
                    unk_cnt += 1
            if no_cnt < 2:
                add(FAIL, '支撑来源',
                    '「撤掉它结论还成立吗」答「否」的行只有 %d 条，要求 ≥2 —— '
                    '全是「是」说明没有真前提，本页不该存在（05 §4.3）' % no_cnt)
            if unk_cnt:
                add(WARN, '支撑来源', '%d 行的撤前提列无法判定，请写成「否 —— 原因」或「是 —— 佐证」' % unk_cnt)
            if yes_unmarked:
                add(WARN, '支撑来源',
                    '有 %d 行答「是」（撤掉后结论仍成立）却没标注为佐证 —— '
                    '读者会把它当前提，请写明「是 —— 佐证」' % yes_unmarked)
            if len(data) < 3 and len(header) >= 3:
                pass

    # --- 反面证据 ---
    counter = find_section(body, '反面证据')
    if counter is not None:
        stripped = re.sub(r'[〔〕\[\]\(\)（）\s\*_`·—\-]', '', counter)
        if is_placeholder(counter):
            add(FAIL, '反面证据', '是空的或占位语 —— 省了这一节就是立场，不是综合（05 §4.4）')
        elif len(stripped) < 30:
            add(WARN, '反面证据', '内容过短（%d 字）—— 至少写清这条反证削弱了什么' % len(stripped))
        elif not any(m in counter for m in COUNTER_MARKERS):
            add(WARN, '反面证据', '没看到「削弱/相反/反驳/反例」这类表述 —— 确认真的在写反证')

    # --- 适用域 ---
    apply_sec = find_section(body, '适用域')
    if apply_sec is not None:
        stripped = re.sub(r'[〔〕\[\]\(\)（）\s\*_`·—\-]', '', apply_sec)
        if is_placeholder(apply_sec):
            add(FAIL, '适用域', '是空的或占位语 —— 必须写清什么条件下结论不成立（05 §4.5）')
        elif any(b in apply_sec for b in APPLY_BANNED):
            add(FAIL, '适用域', '含空话（%s）—— 不能写「普遍适用」'
                % '、'.join(b for b in APPLY_BANNED if b in apply_sec))
        elif len(stripped) < 20:
            add(WARN, '适用域', '内容过短（%d 字）' % len(stripped))

    # --- 不确定 / 待验证 ---
    for sec, label in (('不确定性', '不确定性'), ('待验证', '待验证')):
        c = find_section(body, sec)
        if c is not None and is_placeholder(c):
            add(FAIL, label, '是空的或占位语')

    unc = find_section(body, '不确定性')
    if unc and not re.search(r'INFERRED|推断|推测|缺.*数据|未验证', unc):
        add(WARN, '不确定性', '没标出哪个环节是推断（INFERRED）—— 别把推断伪装成事实')

    # --- 来源标注 ---
    labels = LABEL_RE.findall(body)
    if not labels:
        add(FAIL, '来源标注', '正文没有任何 〔〕 位置标注（05 §4.9）')
    if HTML_COMMENT_RE.search(body):
        add(FAIL, '来源标注', '残留 HTML 注释式来源 —— 阅读视图不可见，等于没标（05 §4.9）')

    # --- 相关 ---
    rel = find_section(body, '相关')
    if rel is not None:
        links = [l.strip() for l in WIKILINK_RE.findall(rel)]
        if len(links) < 3:
            add(FAIL, '相关', '相关节少于 3 个 wikilink（实际 %d）' % len(links))
        if len(links) > 8:
            add(WARN, '相关', '相关节有 %d 个链接，偏多（05 §4.8 建议 3–6）' % len(links))
        if name in links:
            add(FAIL, '相关', '链了自己：[[%s]]' % name)
        if TODO_LINK_RE.search(rel):
            add(FAIL, '相关', '相关节用了 [待创建:] —— 综合页的前提必须已存在（05 §4.8）')

    # --- 死链 ---
    idx = page_index()
    todo = set(TODO_LINK_RE.findall(body))
    for t in set(WIKILINK_RE.findall(body)):
        t = t.strip()
        if t and t not in idx and t not in todo and t != name:
            add(WARN, '死链', '[[%s]] 目标页不存在，也没标 [待创建:]' % t)
    for bad in BAD_TODO_RE.findall(body):
        add(FAIL, '链接格式', '[[待创建: %s]] 写法错误，应为 [待创建: [[%s]]]' % (bad, bad))

    return issues


def cmd_page(path):
    issues = check_page(path)
    fails = [i for i in issues if i[0] == FAIL]
    warns = [i for i in issues if i[0] == WARN]

    print('== 单页自检：%s ==' % path)
    print()
    if not issues:
        print('0 FAIL 0 WARN —— 结构与字段全部通过。')
        print('注意：脚本验不了 §0 的「证伪测试」（什么条件下结论不成立），需人工做。')
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
    print('有 FAIL 就必须修完再交付。脚本验不了「证伪测试」，那一步只能人工。')
    return 1 if fails else 0


# ---------------------------------------------------------------- --all


def cmd_all(limit):
    pages = synthesis_pages()
    print('== 全库 synthesis 体检 ==')
    print()
    if not pages:
        print('wiki/synthesis/ 下还没有页面。')
        print()
        print('先跑 --probe 看哪些主题已经有料（concept 页 sources ≥3 且证据表 ≥3 行）：')
        print('    python3 skills/wiki-synthesis-page/scripts/verify_synthesis_page.py --probe')
        return 0

    print('共 %d 页：%s' % (len(pages), '、'.join(os.path.basename(p)[:-3] for p in pages)))
    print()

    total_fail = total_warn = 0
    fail_kinds = Counter()
    for p in pages:
        issues = check_page(p)
        fails = [i for i in issues if i[0] == FAIL]
        warns = [i for i in issues if i[0] == WARN]
        total_fail += len(fails)
        total_warn += len(warns)
        for _, area, _ in fails:
            fail_kinds[area] += 1
        flag = 'OK  ' if not fails else 'FAIL'
        print('%s  %-46s FAIL %2d  WARN %2d' % (flag, os.path.basename(p)[:-3][:46], len(fails), len(warns)))

    print()
    print('合计：FAIL %d / WARN %d' % (total_fail, total_warn))
    if fail_kinds:
        print()
        print('FAIL 聚类（按节）：')
        for area, n in fail_kinds.most_common(limit):
            print('  %-16s %d 页' % (area, n))
    return 1 if total_fail else 0


# ---------------------------------------------------------------- --probe


def evidence_rows(path):
    """统计 concept 页证据表的行数（含旧节名与追加节），并报告是否用了旧节名。"""
    body = parse_frontmatter(read(path))[1]
    total = 0
    legacy = False
    for nm, is_legacy in (('来源与证据', False),
                          ('不同素材中的观点', True),
                          ('新入库素材中的补充说法', False)):
        sec = find_section(body, nm)
        if sec is None:
            continue
        if is_legacy:
            legacy = True
        rows = table_rows(sec)
        if rows:
            total += max(0, len(rows) - 1)
        else:
            total += len(re.findall(r'^\s*[-*]\s+\S', sec, re.M))
    return total, legacy


def cmd_probe(limit):
    pages = concept_pages()
    if not pages:
        print('错误：%s 下没有页面' % CONCEPT_DIR)
        return 2

    # 已建 synthesis 的源集合，用于判断「该主题是否已被覆盖」
    built = []
    for p in synthesis_pages():
        fm, _ = parse_frontmatter(read(p))
        srcs = {norm_key(s) for s in parse_list_field(fm.get('sources', ''))}
        built.append((os.path.splitext(os.path.basename(p))[0], srcs))

    print('== 回扫：有料但尚无综合的主题 ==')
    print('判据：concept 页 sources ≥3（05 §1）。证据行数只作参考，不参与筛选。')
    print()

    ready = []
    for p in pages:
        text = read(p)
        fm, _ = parse_frontmatter(text)
        srcs = parse_list_field(fm.get('sources', ''))
        if len(srcs) < 3:
            continue
        n, legacy = evidence_rows(p)
        name = os.path.splitext(os.path.basename(p))[0]
        src_set = {norm_key(s) for s in srcs}
        covered = ''
        for sname, sset in built:
            if len(src_set & sset) >= 2:
                covered = sname
                break
        ready.append((len(srcs), n, name, legacy, covered))

    if not ready:
        print('没有 sources ≥3 且证据表 ≥3 行的 concept 页。')
        print('说明：要么素材还没 ingest 完，要么该主题只有一个来源 —— 两种情况都不该建综合页。')
        return 0

    ready.sort(key=lambda r: (-r[1], -r[0], r[2]))
    print('%-40s %6s %6s  %s' % ('concept 页', '源数', '证据行', '综合页状态'))
    print('-' * 82)
    for ns, ne, name, legacy, covered in ready[:limit]:
        status = ('已覆盖：%s' % covered[:24]) if covered else '未综合'
        mark = ' ⚠旧节名' if legacy else ''
        print('%-40s %6d %6d  %s%s' % (name[:40], ns, ne, status, mark))
    if len(ready) > limit:
        print('… 另有 %d 个，用 --limit 调整' % (len(ready) - limit))
    print()
    unbuilt = [r for r in ready if not r[4]]
    print('合计 %d 个有料主题，其中 %d 个尚未综合。' % (len(ready), len(unbuilt)))
    print()
    print('下一步：逐页问一句话 ——「这几行的共同约束是什么？」')
    print('       有答案 → 跑三个测试（撤前提 / 不可回溯 / 反向构造，05 §1）')
    print('       没答案 → 跳过，别硬写。')
    if any(r[3] for r in ready):
        print()
        print('⚠ 标了「旧节名」的页仍用「不同素材中的观点」——那是 03 v2 已废止的节名，')
        print('  行数统计可能偏低。先按 03 v2 把节名收口成「来源与证据」。')
    return 0


# ---------------------------------------------------------------- --premises


def cmd_premises(path):
    if not os.path.exists(path):
        print('错误：文件不存在：%s' % path)
        return 2
    body = parse_frontmatter(read(path))[1]
    sup = find_section(body, '支撑来源')
    print('== 撤前提检查：%s ==' % path)
    print()
    if sup is None:
        print('错误：找不到「支撑来源」节')
        return 2
    rows = table_rows(sup)
    if not rows:
        print('错误：「支撑来源」不是表格')
        return 2
    data = rows[1:]
    print('%-28s %-34s %s' % ('来源', '贡献了哪一步', '撤掉还成立吗'))
    print('-' * 92)
    no_cnt = yes_cnt = unk_cnt = 0
    for r in data:
        src = r[0] if len(r) > 0 else ''
        step = r[1] if len(r) > 1 else ''
        verdict = r[2] if len(r) > 2 else ''
        c = verdict.replace('是否', '')
        if any(m in c for m in NO_MARKERS):
            tag, no_cnt = '否', no_cnt + 1
        elif verdict.strip().startswith('是') or YES_PAT.match(verdict.strip()):
            tag, yes_cnt = '是', yes_cnt + 1
        else:
            tag, unk_cnt = '?', unk_cnt + 1
        print('%-28s %-34s %s' % (src[:28], step[:34], tag))
    print()
    print('统计：否 %d 条 / 是 %d 条 / 未判定 %d 条' % (no_cnt, yes_cnt, unk_cnt))
    print()
    if no_cnt < 2:
        print('%s 答「否」的行只有 %d 条，要求 ≥2 —— 全是「是」说明没有真前提，本页不该存在（05 §4.3）'
              % (FAIL, no_cnt))
        return 1
    if unk_cnt:
        print('%s %d 行无法判定，请写成「否 —— 原因」或「是 —— 佐证」' % (WARN, unk_cnt))
    print('通过：真前提 %d 条。撤掉它们就不成立，其内容即「适用域」的来源（05 §4.5）。' % no_cnt)
    if yes_cnt:
        print('提示：答「是」的 %d 行是佐证，正文里应明确标注，否则读者会当前提。' % yes_cnt)
    return 0


# ---------------------------------------------------------------- --dup


def sentences_of(body):
    out = []
    for s in re.split(r'[。！？\n]', body):
        s = s.strip()
        s = re.sub(r'^[\-\*\d\.\)\s|]+', '', s)
        s = LABEL_RE.sub('', s)
        if text_len_wo_marks(s) >= 25:
            out.append(s)
    return out


def cmd_dup(threshold, limit):
    spages = synthesis_pages()
    cpages = concept_pages()
    print('== 查重 ==')
    print()

    # 1) title 与 concept 页重合
    ctitles = {}
    for p in cpages:
        fm, _ = parse_frontmatter(read(p))
        t = strip_q(fm.get('title', '')) or os.path.splitext(os.path.basename(p))[0]
        ctitles[norm_key(t)] = os.path.basename(p)[:-3]

    print('① synthesis 页 title 与 concept 页重合')
    hit = False
    for p in spages:
        fm, _ = parse_frontmatter(read(p))
        t = strip_q(fm.get('title', '')) or os.path.splitext(os.path.basename(p))[0]
        k = norm_key(t)
        if k in ctitles:
            hit = True
            print('  %s' % FAIL)
            print('    synthesis「%s」 与 concept「%s」title 重合' % (t, ctitles[k]))
            print('    本页 title 应是**结论**，不是概念名 —— 重名说明写的是概念，不是跨源结论。')
    if not hit:
        print('  未发现 title 重合。')
    print()

    # 2) synthesis 页之间重复句
    print('② synthesis 页之间的重复句（≥%d 页出现同一句即报警）' % threshold)
    if len(spages) < 2:
        print('  synthesis 页不足 2 页，跳过。')
    else:
        holders = defaultdict(list)
        for p in spages:
            body = parse_frontmatter(read(p))[1]
            nm = os.path.splitext(os.path.basename(p))[0]
            for s in set(sentences_of(body)):
                holders[norm_key(s)].append((nm, s))
        dup = {k: v for k, v in holders.items() if len(v) >= threshold}
        if not dup:
            print('  未发现跨页重复的完整句子。')
        else:
            print('  发现 %d 组：' % len(dup))
            for k, v in sorted(dup.items(), key=lambda x: -len(x[1]))[:limit]:
                print('    出现在 %d 页：%s' % (len(v), ' · '.join(n for n, _ in v)))
                print('      「%s」' % v[0][1][:66])
            print('  处理：两页都在展开同一件事 → 留一处，其余改成一句 + 出链。')
    print()

    # 3) 未综合的有料主题（顺带提示）
    print('③ 顺带：有料但未综合的主题')
    print('  用 --probe 看完整清单。')
    return 0


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(
        description='05 synthesis 综合页自检器（只读）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--page', help='单页自检：给一个 synthesis 页路径')
    ap.add_argument('--all', action='store_true', help='全库体检 + FAIL 聚类')
    ap.add_argument('--probe', action='store_true', help='回扫 concept 页，列出有料但未综合的主题')
    ap.add_argument('--premises', help='抽支撑来源表，检查撤前提列')
    ap.add_argument('--dup', action='store_true', help='与 concept 页 title 重合 + 跨页重复句')
    ap.add_argument('--limit', type=int, default=30, help='列表条数上限（默认 30）')
    ap.add_argument('--threshold', type=int, default=2, help='重复句报警阈值（默认 ≥2 页）')

    args = ap.parse_args()

    if args.page:
        return cmd_page(args.page)
    if args.all:
        return cmd_all(args.limit)
    if args.probe:
        return cmd_probe(args.limit)
    if args.premises:
        return cmd_premises(args.premises)
    if args.dup:
        return cmd_dup(args.threshold, args.limit)

    ap.print_help()
    print()
    print('至少给一个模式：--page / --all / --probe / --premises / --dup')
    return 2


if __name__ == '__main__':
    sys.exit(main())
