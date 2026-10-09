#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source 页硬自检器 —— 页型职责定义见 index.md；页面结构与校验按本 Skill

只读工具：不改动任何文件，只输出检查报告。

为什么需要它
------------
01 规范里的核心要求是「摘录必须是能在原文中逐字检索到的连续文本」。
这句话本身不可执行——模型没有"搜一遍"的动作能力时，就会写成转述。
本脚本把这条要求变成机器检查：每条摘录拿回原文比对，搜不到直接判 FAIL。

段号口径
--------
「段」= 原文里的一个非空行。段号从 1 开始，空行不计。
这个口径与 --locate 的输出一致，所以页面里的段号应当由 --locate 给出，
不要手工数。

用法
----
1) 校验一页 source（原文路径自动从 frontmatter 的 source_path 读取）
   python3 verify_source_page.py --page wiki/sources/2026-09-23-xxx.md

2) 显式指定原文
   python3 verify_source_page.py --page wiki/sources/xxx.md --raw raw/record_cleaned_markdown/xxx.md

3) 定位：给一段原话，输出可直接粘进页面的位置标注
   python3 verify_source_page.py --raw raw/xxx.md --locate "表格召回是95%"

4) 候选：列出疑似含数值 / 条件 / 因果的段落，用于确认没漏摘关键内容
   python3 verify_source_page.py --raw raw/xxx.md --candidates

退出码
------
0 = 全部通过（可能含 WARN）
1 = 存在 FAIL
2 = 用法或文件错误
"""

import argparse
import hashlib
import os
import re
import sys

# ---------- 常量 ----------

QUOTE_RE = re.compile(r'「([^」]+)」')
POS_RE = re.compile(r'〔([^〕]+)〕')
TS_RE = re.compile(r'\d{1,2}:\d{2}')
SENTENCE_HEAD_RE = re.compile(r'^第\s*(\d+)\s*段\s*(?:「(.*?)」)?')
# 无编号章节的材料（英文网页文章、官方工程博客等）位置写成「§标题路径」，
# 开篇（H1 下、首个 ## 之前）没有标题可指，统一写「§开篇」。见 01 §5.3。
SEC_PATH_RE = re.compile(r'^§\s*(?P<sec>.+)$')
OPENING_SECS = ('开篇', '前言', '全文')

REQUIRED_FM = [
    'type', 'title', 'source_path', 'sha256',
    'material_type', 'reliability', 'ingested_at', 'status', 'tags',
]
DRIFT_FM = ['sources', 'author', 'created', 'updated']
RELIABILITY_ENUM = {'结构化原文', '二手整理', '口语转写', '练习材料'}
MATERIAL_ENUM = {'课程总结', '课堂转写', '智能纪要', '面试资料', '外部文章'}
PLACEHOLDER_MARKERS = ['全文检索', '自行检索', '请自行搜索']

# 候选段筛选词。阈值故意收紧：宁少不滥——这个工具是防漏摘的提醒，不是穷举清单。
# 命中规则（三者任一）：含数值 / 含硬约束词 / 含案例线索词。
NUM_RE = re.compile(r'\d+(?:\.\d+)?\s*%|\d+\s*(?:万|亿|倍|天|小时|分钟|个|条|人|次|家)|\d{2,}')
HARD_COND_WORDS = ['除非', '至少', '千万别', '前提', '建议', '上限', '下限',
                   '基准', '红线', '做不到', '不可能']
CASE_WORDS = ['客户', '案例', '踩坑', '踩过', '烂尾', '续费', '用不起来',
              '上线之后', '上线后', '供应商', '真实项目']

OK, WARN, FAIL = 'OK', 'WARN', 'FAIL'


# ---------- 基础 ----------

def read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def norm(s):
    """去掉所有空白字符，用于容忍摘录里的折行与空格差异，但不放过任何字符增删。"""
    return re.sub(r'\s+', '', s)


def split_paras(text):
    """段 = 非空行。段号从 1 开始。"""
    return [ln.strip() for ln in text.split('\n') if ln.strip()]


def norm_title(s):
    """标题归一化：折行空格压成一个、去掉 Markdown 装饰、小写。
    英文标题的大小写与空格在网页上不稳定，比对时不能较真。"""
    s = re.sub(r'\s+', ' ', (s or '').strip())
    return s.strip('*_`# ').strip().lower()


def header_lines(raw_text):
    """返回 [(层级, 标题文本, 行号)]，即原文所有 Markdown 标题行。"""
    out = []
    for i, line in enumerate(raw_text.split('\n')):
        m = re.match(r'^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$', line)
        if m:
            out.append((len(m.group(1)), m.group(2).strip(), i))
    return out


def match_sec_path(raw_text, spec):
    """核一个 §标题路径，返回 (是否通过, 没找到的那一段, 最深一节的行区间)。

    支持「§开篇」和「A」「A / B」（01 §5.3：H2 或 H2 / H3）。一个标注里可能带补充说明
    （「§THEN AND NOW · 图 B 注」「§Skills（技能）」），只核标题路径本身。
    多段路径要求逐级命中且行号递增——「A / B」里的 B 必须在 A 之后，否则等于没核层级。
    返回的行区间用于进一步确认本行摘录确实落在该节内，而不是位置写对、摘录在别处。
    """
    base = re.split(r'\s*[·・]\s*', spec)[0].strip()
    base = re.sub(r'\s*[（(][^）)]*[）)]\s*$', '', base).strip()
    heads = [x.strip() for x in re.split(r'[/／]', base) if x.strip()]
    if not heads:
        return False, spec, None
    if len(heads) == 1 and heads[0] in OPENING_SECS:
        # 开篇 = H1 之下、第一个 ## 之前。H1 自身不算开篇，否则区间为空、摘录全被判越界。
        first = next((i for lv, t, i in header_lines(raw_text) if lv >= 2), None)
        if first is None:
            first = len(raw_text.split('\n'))
        return True, heads[0], (0, first)

    hdrs = header_lines(raw_text)
    prev = -1
    hit_level = None
    for h in heads:
        nh = norm_title(h)
        # 先找完全同名的标题，找不到再退一步找包含它的——否则「§CLAUDE.md」会先撞上文中更早的
        # 「### Then: Memory in CLAUDE.md files」，「§References」会撞上「### Now: Rich references」：
        # 位置看着对，其实指到了另一节，是假通过。
        cands = [(lv, t, i) for lv, t, i in hdrs if i > prev]
        found = (next(((lv, t, i) for lv, t, i in cands if nh == norm_title(t)), None)
                 or next(((lv, t, i) for lv, t, i in cands if nh in norm_title(t)), None))
        if found is None:
            return False, h, None
        hit_level, _, prev = found
    # 最深一节的区间：从它的标题行到下一个「层级 ≤ 它」的标题行
    end = next((i for lv, t, i in hdrs if i > prev and lv <= (hit_level or 6)),
               len(raw_text.split('\n')))
    return True, base, (prev, end)


def parse_frontmatter(text):
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.S)
    if not m:
        return None, text
    fm = {}
    for line in m.group(1).split('\n'):
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith('#') or ':' not in line:
            continue
        k, v = line.split(':', 1)
        v = v.strip()
        # 去掉 YAML 引号，否则 source_path 会带着引号去找文件
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        fm[k.strip()] = v
    return fm, text[m.end():]


def split_sections(body):
    """按 ## 标题切分，返回 (标题, 内容) 列表。"""
    out = []
    cur, buf = None, []
    for line in body.split('\n'):
        if re.match(r'^##\s+', line):
            if cur is not None:
                out.append((cur, '\n'.join(buf)))
            cur = re.sub(r'^##\s+', '', line).strip()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur, '\n'.join(buf)))
    return out


def find_section(body, keyword):
    for title, content in split_sections(body):
        if keyword in title:
            return title, content
    return None, None


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def strip_ellipsis(s):
    return s.rstrip('…').rstrip('.').rstrip('·').strip()


def section_path_at(raw_text, para_index):
    """给一个段号，返回它所属的标题路径，如 ('THEN AND NOW', 'Now: Design interfaces')。

    外部文章类素材没有编号章节，位置只能用标题路径表达；而「第 N 段」对它也没法用
    （段号是清洗稿的标位方式）。这里把段号换算成标题路径，让 --locate 对这类素材同样可用。
    返回空列表表示该段落在首个 ## 之前，即「§开篇」。
    """
    lines = raw_text.split('\n')
    seen = target_line = None
    for i, ln in enumerate(lines):
        if ln.strip():
            seen = (seen or 0) + 1
            if seen == para_index:
                target_line = i
                break
    if target_line is None:
        return []
    stack = []
    for lv, t, i in header_lines(raw_text):
        if i >= target_line:
            break
        while stack and stack[-1][0] >= lv:
            stack.pop()
        stack.append((lv, t))
    return [t for lv, t in stack if lv >= 2]


# ---------- --locate ----------

def cmd_locate(raw_path, frag):
    text = read(raw_path)
    paras = split_paras(text)
    nf = norm(frag)
    hits = [i for i, p in enumerate(paras, 1) if nf in norm(p)]

    print('原文：%s（%d 段，%d 字符）' % (raw_path, len(paras), len(text)))
    print('片段：%s' % (frag[:60] + ('…' if len(frag) > 60 else '')))
    print()

    if not hits:
        # 退化匹配：取片段前 20 字再试
        head = nf[:20]
        near = [i for i, p in enumerate(paras, 1) if head in norm(p)] if head else []
        print('FAIL 原文中找不到该片段。')
        if near:
            print('      前 20 字命中第 %s 段，可能是摘录与原文有出入（改字/漏字/标点差异）。' % near[0])
            print('      原文：%s' % paras[near[0] - 1][:120])
        print()
        print('提示：摘录必须是原文的连续字符序列。请从原文复制，不要凭记忆写。')
        return 2

    print('命中 %d 处：' % len(hits))
    for i in hits:
        para = paras[i - 1]
        head10 = para[:10]
        ts = TS_RE.search(para)
        print()
        print('  第 %d 段' % i)
        print('    可直接粘贴的位置标注：〔第 %d 段「%s…」〕' % (i, head10))
        # 只有原文真有 ## 级小节时才给标题路径建议；清洗稿通篇只有 H1（无小节），
        # 对它提「§开篇」是误导——那类素材的正解是段号。
        if any(lv >= 2 for lv, _, _ in header_lines(text)):
            path = section_path_at(text, i)
            print('    §标题路径（外部文章类素材用）：〔§%s〕'
                  % (' / '.join(path) if path else '开篇'))
        if ts:
            print('    该段含时间戳：〔%s〕' % ts.group(0))
        print('    段首 60 字：%s' % para[:60])
    print()
    if len(hits) > 1:
        print('注意：命中多段。请选信息量最高的那一段，或把摘录加长以唯一定位。')
    return 0


# ---------- --candidates ----------

def cmd_candidates(raw_path, limit):
    text = read(raw_path)
    paras = split_paras(text)
    rows = []
    for i, p in enumerate(paras, 1):
        if len(p) < 15:
            continue
        reasons = []
        if NUM_RE.search(p):
            reasons.append('数值')
        if any(w in p for w in HARD_COND_WORDS):
            reasons.append('硬约束')
        if any(w in p for w in CASE_WORDS):
            reasons.append('案例')
        if reasons:
            rows.append((i, '+'.join(reasons), p))

    print('原文：%s（共 %d 段）' % (raw_path, len(paras)))
    print('疑似含关键材料的段落：%d 条（命中规则：含数值 / 含硬约束词 / 含案例线索词）' % len(rows))
    print()
    for i, why, p in rows[:limit]:
        print('第 %-3d 段 [%s] %s' % (i, why, p[:70] + ('…' if len(p) > 70 else '')))
    if len(rows) > limit:
        print()
        print('… 另有 %d 条，用 --limit 调整显示上限。' % (len(rows) - limit))
    print()
    print('用法：对其中真正重要的段落，用 --locate "原话片段" 拿到精确位置标注。')
    print('提醒：候选 ≠ 必摘。判断标准是「下游断言需不需要它」，不是「它看起来有数字」。')
    return 0


# ---------- --page ----------

def cmd_page(page_path, raw_override):
    text = read(page_path)
    fm, body = parse_frontmatter(text)
    results = []

    def add(level, item, msg):
        results.append((level, item, msg))

    print('== 校验 %s ==' % page_path)

    if fm is None:
        print('FAIL frontmatter 缺失（文件未以 --- 开头的 YAML 块起始）')
        print()
        print('通过 0 / 警告 0 / 失败 1')
        return 1

    # --- 字段检查 ---
    for k in REQUIRED_FM:
        if k not in fm:
            add(FAIL, 'frontmatter', '缺少必填字段：%s' % k)
    for k in DRIFT_FM:
        if k in fm:
            add(WARN, 'frontmatter', '存在已废弃字段：%s（规范已移除，不应再写入）' % k)
    if fm.get('type') and fm['type'] != 'source':
        add(FAIL, 'frontmatter', 'type 应为 source，实际为 %s' % fm['type'])
    rel = fm.get('reliability', '')
    if rel and rel not in RELIABILITY_ENUM:
        add(WARN, 'frontmatter', 'reliability 取值不在枚举内：%s（应为 %s）'
            % (rel, '/'.join(sorted(RELIABILITY_ENUM))))
    mt = fm.get('material_type', '')
    if mt and mt not in MATERIAL_ENUM:
        add(WARN, 'frontmatter', 'material_type 取值不在枚举内：%s（应为 %s）'
            % (mt, '/'.join(sorted(MATERIAL_ENUM))))
    if not fm.get('title'):
        add(FAIL, 'frontmatter', 'title 为空')

    # 标题一致性
    h1 = re.search(r'^#\s+(.+)$', body, re.M)
    if h1 and fm.get('title'):
        t1 = norm(h1.group(1))
        t2 = norm(fm['title'])
        if t1 != t2:
            add(WARN, 'frontmatter', 'frontmatter title 与正文 H1 不一致：「%s」vs「%s」'
                % (fm['title'], h1.group(1).strip()))
    elif not h1:
        add(WARN, 'frontmatter', '正文缺少 H1 标题')

    # --- 原文与哈希 ---
    raw_path = raw_override or fm.get('source_path', '')
    raw_text = None
    if not raw_path:
        add(FAIL, 'source_path', 'source_path 为空，无法定位原文')
    elif not os.path.exists(raw_path):
        add(FAIL, 'source_path', '原文不存在：%s（相对路径请从项目根目录运行本脚本）' % raw_path)
    else:
        raw_text = read(raw_path)
        real = sha256_of(raw_path)
        claimed = (fm.get('sha256') or '').strip()
        if not claimed:
            add(FAIL, 'sha256', '未填写 sha256（增量判据缺失，无法判断是否需要重跑）')
        elif claimed != real:
            add(FAIL, 'sha256', 'sha256 不匹配：页面写 %s…，实际 %s…' % (claimed[:12], real[:12]))
        else:
            add(OK, 'sha256', '与原文一致（%s…）' % real[:12])

    if raw_override and fm.get('source_path') and os.path.normpath(raw_override) != os.path.normpath(fm['source_path']):
        add(WARN, 'source_path', '命令行传入的 --raw 与 frontmatter 的 source_path 不一致')

    # --- 正文结构 ---
    sec_titles = [t for t, _ in split_sections(body)]
    for kw, label in [('核心观点', '核心观点'), ('证据摘录', '证据摘录'), ('可用边界', '素材的可用边界')]:
        title, _ = find_section(body, kw)
        if title is None:
            near = [t for t in sec_titles if kw[:2] in t]
            extra = ('（发现近似章节「%s」，但规范要求标题含「%s」）' % (near[0], kw)) if near else ''
            add(FAIL, '结构', '缺少章节：%s%s' % (label, extra))

    # 占位符
    for marker in PLACEHOLDER_MARKERS:
        if marker in body:
            add(FAIL, '位置标注', '出现占位符「%s」——那不是位置，是让读者自己去干活的空话' % marker)

    # --- 证据摘录逐条机检 ---
    quote_stats = {'total': 0, 'ok': 0, 'fail': 0, 'pos_ok': 0, 'pos_fail': 0, 'no_support': 0}
    if raw_text is not None:
        _, sec = find_section(body, '证据摘录')
        if sec:
            paras = split_paras(raw_text)
            raw_lines = raw_text.split('\n')
            nraw = norm(raw_text)
            sec_lines = sec.split('\n')
            for idx, rawline in enumerate(sec_lines):
                line = rawline.strip()
                if not line or line.startswith('#'):
                    continue
                # 摘录行的特征是「行首（可带列表符）就是中文引号」。
                # 这样「可用于支持」等说明文字里的中文引号（中文里也用作强调）不会被误判成摘录。
                if not re.match(r'^\s*(?:[-*]\s*)?「', line):
                    continue
                positions = POS_RE.findall(line)
                clean = POS_RE.sub('', line)
                quotes = QUOTE_RE.findall(clean)

                if quotes and not positions:
                    add(WARN, '证据摘录', '摘录未标位置：%s' % quotes[0][:30])

                for q in quotes:
                    quote_stats['total'] += 1
                    nq = norm(q)
                    if len(nq) < 8:
                        add(WARN, '证据摘录', '摘录过短，疑似半截话：%s' % q[:30])
                    if nq in nraw:
                        quote_stats['ok'] += 1
                    else:
                        quote_stats['fail'] += 1
                        add(FAIL, '证据摘录', '原文中搜不到（疑似转述或改字）：%s'
                            % q[:60] + ('…' if len(q) > 60 else ''))

                # 位置校验
                for pos in positions:
                    m = SENTENCE_HEAD_RE.match(pos.strip())
                    if m:
                        n = int(m.group(1))
                        head = m.group(2)
                        if n < 1 or n > len(paras):
                            quote_stats['pos_fail'] += 1
                            add(FAIL, '位置标注', '段号越界：第 %d 段（原文共 %d 段）' % (n, len(paras)))
                            continue
                        seg = paras[n - 1]
                        seg_ok = any(norm(q) in norm(seg) for q in quotes) if quotes else True
                        if seg_ok:
                            quote_stats['pos_ok'] += 1
                        else:
                            quote_stats['pos_fail'] += 1
                            real_hits = [i for i, p in enumerate(paras, 1)
                                         if quotes and norm(quotes[0]) in norm(p)]
                            hint = ('实际在第 %d 段' % real_hits[0]) if real_hits else '原文未找到该摘录'
                            add(FAIL, '位置标注', '第 %d 段内找不到本行摘录（%s）' % (n, hint))
                        if head:
                            h = strip_ellipsis(head)
                            if h and not norm(seg).startswith(norm(h)):
                                add(WARN, '位置标注', '段首 10 字与原文不符：写「%s」，实际段首「%s」'
                                    % (h, seg[:10]))
                    elif TS_RE.search(pos):
                        ts = TS_RE.search(pos).group(0)
                        if ts not in raw_text:
                            quote_stats['pos_fail'] += 1
                            add(FAIL, '位置标注', '时间戳 %s 在原文中不存在' % ts)
                        elif quotes and not any(norm(q) in nraw for q in quotes):
                            quote_stats['pos_fail'] += 1
                        else:
                            quote_stats['pos_ok'] += 1
                    elif SEC_PATH_RE.match(pos.strip()):
                        # §标题路径 / §开篇（无编号章节的外部文章，见 01 §5.3）。
                        # 不只核标题存在，还要核本行摘录确实落在那一节里——否则「位置对了、摘录在别处」也能蒙混过关。
                        spec = SEC_PATH_RE.match(pos.strip()).group('sec').strip()
                        ok, bad, span = match_sec_path(raw_text, spec)
                        if not ok:
                            quote_stats['pos_fail'] += 1
                            add(FAIL, '位置标注', '标题「%s」在原文的标题行里找不到（§%s）' % (bad[:26], spec[:26]))
                        elif span is not None and quotes and not any(
                                norm(q) in norm('\n'.join(raw_lines[span[0]:span[1]])) for q in quotes):
                            quote_stats['pos_fail'] += 1
                            add(FAIL, '位置标注', '本行摘录不在「§%s」这一节内' % spec[:26])
                        else:
                            quote_stats['pos_ok'] += 1
                    else:
                        add(WARN, '位置标注', '无法识别的位置格式：%s' % pos[:30])

                # 「可用于支持」：允许紧随其后 1–2 行
                follow = '\n'.join(sec_lines[idx + 1:idx + 3])
                if quotes and '可用于支持' not in line and '可用于支持' not in follow:
                    quote_stats['no_support'] += 1

        if quote_stats['no_support']:
            add(WARN, '证据摘录', '%d 条摘录未紧跟「可用于支持：」说明用途' % quote_stats['no_support'])
        if quote_stats['total'] == 0:
            add(WARN, '证据摘录', '一条摘录也没有')
        elif quote_stats['total'] < 8:
            add(WARN, '证据摘录',
                '仅 %d 条摘录。条数不是标准，但信息密集的素材（如两万字符的课堂录音）可达 30 条以上——请确认是否漏摘。'
                % quote_stats['total'])

        # 数值留存抽检
        num_paras = sum(1 for p in split_paras(raw_text) if NUM_RE.search(p))
        if num_paras >= 5 and quote_stats['total'] > 0:
            _, sec2 = find_section(body, '证据摘录')
            if sec2 and not NUM_RE.search(POS_RE.sub('', sec2)):
                add(WARN, '证据摘录',
                    '本页摘录中没有任何数值，但原文有 %d 段含数值。确认是否把可核验的数字漏掉了。' % num_paras)

    # --- 输出 ---
    print()
    print('── frontmatter 与结构 ──')
    for level, item, msg in results:
        print('%-4s [%s] %s' % (level, item, msg))
    if not results:
        print('OK   无问题')

    print()
    print('── 证据摘录 ──')
    if raw_text is None:
        print('跳过（原文不可读）')
    else:
        print('摘录 %d 条：原文可搜 %d，搜不到 %d' % (
            quote_stats['total'], quote_stats['ok'], quote_stats['fail']))
        print('位置标注：通过 %d，失败 %d' % (quote_stats['pos_ok'], quote_stats['pos_fail']))

    n_fail = sum(1 for r in results if r[0] == FAIL)
    n_warn = sum(1 for r in results if r[0] == WARN)
    print()
    print('── 汇总 ──')
    print('失败 %d / 警告 %d' % (n_fail, n_warn))
    if n_fail:
        print('FAIL 项必须修完再交付。修好后重跑本脚本，退出码 0 才算完成。')
    elif n_warn:
        print('无硬失败，但警告项需要逐条人工判断。')
    else:
        print('全部机检项通过。注意：脚本只能验「摘录可搜 + 位置正确 + 字段合规」，'
              '验不了「编译封闭性」——那要靠人工回答：下游断言能不能只靠本页支撑？')
    return 1 if n_fail else 0


# ---------- 入口 ----------

def main():
    ap = argparse.ArgumentParser(
        description='source 页硬自检器（Wiki Skill 页面结构校验）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--page', help='要校验的 source 页路径')
    ap.add_argument('--raw', help='原文路径（--page 模式下可省略，自动读 frontmatter）')
    ap.add_argument('--locate', help='在原文中定位一个片段，输出可粘贴的位置标注')
    ap.add_argument('--candidates', action='store_true', help='列出疑似含数值/条件/因果的段落')
    ap.add_argument('--limit', type=int, default=60, help='--candidates 的显示上限，默认 60')
    args = ap.parse_args()

    if args.locate:
        if not args.raw:
            print('错误：--locate 需要同时提供 --raw', file=sys.stderr)
            return 2
        if not os.path.exists(args.raw):
            print('错误：找不到原文 %s' % args.raw, file=sys.stderr)
            return 2
        return cmd_locate(args.raw, args.locate)

    if args.candidates:
        if not args.raw:
            print('错误：--candidates 需要同时提供 --raw', file=sys.stderr)
            return 2
        if not os.path.exists(args.raw):
            print('错误：找不到原文 %s' % args.raw, file=sys.stderr)
            return 2
        return cmd_candidates(args.raw, args.limit)

    if args.page:
        if not os.path.exists(args.page):
            print('错误：找不到页面 %s' % args.page, file=sys.stderr)
            return 2
        return cmd_page(args.page, args.raw)

    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
