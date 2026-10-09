#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_solution_page.py — 09 solution 项目设计方案页自检器（只读工具）

页型职责定义见 index.md；页面结构与校验按对应 Wiki Skill

用法：
  python3 verify_solution_page.py --page wiki/solutions/X.md    单页自检
  python3 verify_solution_page.py --all                          全库体检 + FAIL 聚类
  python3 verify_solution_page.py --dup                          与 concept 页标题撞名 + 源清单重合
  python3 verify_solution_page.py --scan                         反扫未建页的业务主题候选

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

SOLUTION_DIR = 'wiki/solutions'
CONCEPT_DIR = 'wiki/concepts'
SOURCE_DIR = 'wiki/sources'

FAIL = '[FAIL]'
WARN = '[WARN]'

# ---------------------------------------------------------------- 常量（09 规范）

# 四段阅读骨架（09 §3，2026-09-25 起）——乙·环节式
# (段名, [(节名, 档位)])；档位 must=必写 / soft=有料就写 / may=可缺 / cond=条件节
STAGES = [
    ('一、这是什么方案', [
        ('业务场景与痛点', 'soft'),
        ('核心角色与职责', 'soft'),
    ]),
    ('二、方案怎么运转', [
        ('业务流程与状态', 'must'),
        ('关键动作与功能点', 'must'),
        ('数据对象与字段', 'soft'),
    ]),
    ('三、边界与保障', [
        ('异常与逆向流程', 'soft'),
        ('系统协作与边界', 'soft'),
        ('AI 能力与评估', 'cond'),
        ('指标与验证', 'soft'),
    ]),
    ('四、怎么落地、还缺什么', [
        ('版本拆分与取舍', 'may'),
        ('待确认清单', 'must'),
        ('相关', 'must'),
    ]),
]

# 节的完整固定顺序（含条件节）
FULL_ORDER = [s for _, secs in STAGES for s, _ in secs]
# 节 → 所属段
SECTION_STAGE = {s: st for st, secs in STAGES for s, _ in secs}
# 节 → 档位
SECTION_LEVEL = {s: lv for _, secs in STAGES for s, lv in secs}
# 必写节（档位 must）
REQUIRED_SECTIONS = [s for s in FULL_ORDER if SECTION_LEVEL[s] == 'must']
# 条件节
CONDITIONAL_SECTIONS = [s for s in FULL_ORDER if SECTION_LEVEL[s] == 'cond']

# 段名核心词（去掉「一、」这类序号后比对，容忍全角/半角与标点差异）
STAGE_CORES = ['这是什么方案', '方案怎么运转', '边界与保障', '怎么落地还缺什么']
LEVEL_LABEL = {'must': '必写', 'soft': '有料就写', 'may': '可缺', 'cond': '条件节'}

# 旧版 solution 页用过的节名（09 §6）：出现即提示对照新骨架
LEGACY_SECTIONS = [
    '方案适用范围', '先验证业务目标', '角色与数据对象', '核心流程',
    '权限、审核与分配', '页面与能力拆分', '关键异常与待确认',
    '建模步骤', '单据设计检查表', '单据设计检查',
    '适用与版本边界', '一期：会员主档与交易关联', '迭代：会员权益和标签分群',
    '积分账户与规则', '积分兑换端到端流程', '流水、关联和客服排查', '关键校验与指标',
    '先界定业务模式', '正向订单流程', '取消和异常履约', '售后和退款流程',
    '系统边界和接口', '版本和指标',
]

# frontmatter 必填字段（09 §2）
REQUIRED_FIELDS = ['type', 'title', 'sources', 'status', 'tags', 'case_origin']
LEGACY_FIELDS = ['created', 'updated']
CASE_ORIGIN_ENUM = {'课程', '外部', '混合'}

# 待确认清单里的推诿句式（09 §4.11）
DODGE_PATTERNS = [
    '由业务确认', '视情况而定', '需进一步沟通', '需与业务', '待业务确认',
    '根据实际情况', '视具体情况', '进一步明确', '后续确认', '待业务明确',
    '由业务决定', '按实际业务', '需业务方确认', '与实际业务确认',
]

# 合法地说明素材没给（不算推诿）
GAP_MARKERS = [
    '素材未给出', '素材未说明', '素材没有给出', '素材未提供', '资料未覆盖',
    '素材未涉及', '素材中没有', '素材没有给', '不存在通用答案', '必须逐项确认',
    '需逐项确认', '要逐项确认', '不存在仅凭', '素材没有规定', '素材只',
]

# 问句判据
QUESTION_WORDS = [
    '什么', '哪些', '怎么', '如何', '是否', '能否', '多少', '谁', '何时',
    '哪边', '为准', '是不是', '要不要', '有没有', '按哪个', '哪个', '几',
]
QUESTION_MARKS = ['？', '?']

PLACEHOLDER = {
    '', '—', '-', '–', '——', '/', 'n/a', 'na', 'tbd', '待补充', '待定',
    '无', '同上', '?', '？', '暂无', '略', '略。', '（略）', '待完善', '待补充。',
}

# 条件节启用判据（09 §4.10）——正文出现这些内容即视为涉及模型
# 注意：不要放裸的「模型」「温度」「质检」——「数据权限模型」这类会误命中
MODEL_HINTS = [
    '大模型', 'LLM', 'RAG', '检索增强', '意图识别', '准确率', '召回',
    '评测集', 'Agent', '智能体', '智能客服', '知识问答', '向量化', '向量检索',
    '提示词', 'Prompt', '幻觉', '微调', 'ChatBI', 'embedding', '转人工',
]

# 提炼标记（09 §0）
REFINE_MARK = '**提炼**'

# 实施记录信号（09 §7.1 Q4）——出现具体客户名或交付术语，说明这一页在写某个客户的项目而非参考实现
IMPL_NAME_RE = re.compile(
    r'客户\s*[A-Za-z0-9甲乙丙丁戊己庚辛壬癸一二三四五六七八九十]{1,8}\s*'
    r'(?:公司|集团|银行|医院|学校|科技|实业)')
IMPL_TERMS = ['上线排期', '项目排期', '实施周期', '报价单', '投标文件', '交付验收', '验收报告', '付款节点']

# 外部来源短名前缀（09 §4.13）
EXTERNAL_MARK = '外·'

# 反向扫描的业务域词表（09 §9）
DOMAIN_HINTS = {
    'CRM与客户管理': ['CRM', '线索', '商机', '客户档案', '拜访', '销售跟进'],
    '会员与积分': ['会员', '积分', '权益', '分群', '付费会员'],
    '订单与履约售后': ['订单履约', '拆单', '发货单', 'WMS', '售后', '退货退款', '库存'],
    '客服与智能质检': ['智能客服', '质检', '坐席', '工单', '抽检', '客服机器人'],
    '知识库与RAG问答': ['知识库', 'RAG', '检索增强', '文档解析', '向量检索', '意图识别'],
    '对话式数据分析': ['ChatBI', '对话式商业智能', '自然语言查询', '取数', '数据看板'],
    '法律与专利': ['法律AI', '专利', '权利要求', '审查意见', '法律文档'],
    '内部办公助手': ['办公助手', '订房', '入职', '审批流', 'OA'],
}

# 来源标注
KAKU_RE = re.compile(r'〔([^〕]*)〕')
MDLINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)\]\]')
TODO_LINK_RE = re.compile(r'\[待创建[:：]\s*\[\[([^\]|]+?)\]\]\]')


# ---------------------------------------------------------------- 基础工具

def read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def parse_frontmatter(text):
    """返回 (fm_dict, body)。fm 的值是多行原样拼接的字符串。"""
    if not text.startswith('---'):
        return {}, text
    parts = text.split('---', 2)
    if len(parts) < 3:
        return {}, text
    fm_text, body = parts[1], parts[2]
    fm = {}
    key = None
    buf = []
    for line in fm_text.split('\n'):
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*:', line):
            if key is not None:
                fm[key] = '\n'.join(buf).strip()
            key, _, val = line.partition(':')
            buf = [val.strip()]
        elif key is not None:
            buf.append(line)
    if key is not None:
        fm[key] = '\n'.join(buf).strip()
    return fm, body


def parse_list_field(value):
    """拆 frontmatter 列表字段，支持单行数组、多行数组、dash 列表三种写法。"""
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


def clean_sec(name):
    """清洗节标题：去掉条件标记方括号与星号。"""
    n = (name or '').strip()
    n = re.sub(r'[〔\[【]([^〕\]】]*)[〕\]】]', r'\1', n)
    n = n.replace('★', '').strip()
    return n


def norm_stage_key(name):
    """段名归一化：去掉「一、」这类序号与标点，便于容错比对。"""
    n = clean_sec(name)
    n = re.sub(r'^第?[一二三四1234]\s*[、.．,，)）]?\s*', '', n)
    n = re.sub(r'[、，,。．\.\s·]', '', n)
    return n


def parse_skeleton(body):
    """按「## 段 / ### 节」两层解析正文。

    返回 (stages, sections)：
      stages   = [段名, ...]，按出现顺序
      sections = [(节名, 内容, 所属段名), ...]，按出现顺序
    旧结构（十二节平铺成 ##）会得到 stages=十二节名、sections=[]。
    """
    stages = []
    sections = []
    cur_stage = ''
    cur_sec = None
    buf = []

    def flush():
        if cur_sec is not None:
            sections.append((cur_sec, '\n'.join(buf), cur_stage))

    for line in body.split('\n'):
        m2 = re.match(r'^##\s+(.+?)\s*$', line)
        if m2:
            flush()
            cur_sec = None
            buf = []
            cur_stage = clean_sec(m2.group(1))
            stages.append(cur_stage)
            continue
        m3 = re.match(r'^###\s+(.+?)\s*$', line)
        if m3:
            flush()
            cur_sec = clean_sec(m3.group(1))
            buf = []
            continue
        if cur_sec is not None:
            buf.append(line)
    flush()
    return stages, sections


def all_sections(body):
    """返回 [(节名, 内容)]，按出现顺序——只取 ## 层（旧结构用）。"""
    out = []
    cur = None
    buf = []
    for line in body.split('\n'):
        m = re.match(r'^##\s+(.+?)\s*$', line)
        if m:
            if cur is not None:
                out.append((cur, '\n'.join(buf)))
            cur = clean_sec(m.group(1))
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur, '\n'.join(buf)))
    return out


def find_section(body, name):
    """先按新骨架在 ### 节里找，再退回旧的 ## 平铺结构。"""
    _, sections = parse_skeleton(body)
    for n, c, _ in sections:
        if n == name:
            return c
    for n, c in all_sections(body):
        if n == name:
            return c
    return None


def summary_block(body):
    """取正文第一条 `> ` 引文（引言块）。"""
    for line in body.split('\n'):
        s = line.strip()
        if s.startswith('>'):
            return s.lstrip('> ').strip()
    return ''


def strip_marks(s):
    s = KAKU_RE.sub('', s)
    s = MDLINK_RE.sub(r'\1', s)
    s = re.sub(r'[\[\]\(\)\*`#\s，。；：、！？]', '', s)
    return s


def page_index():
    idx = {}
    for p in glob.glob('wiki/**/*.md', recursive=True):
        n = os.path.splitext(os.path.basename(p))[0]
        idx[n] = p.replace('\\', '/')
    return idx


def concept_pages():
    return sorted(glob.glob(os.path.join(CONCEPT_DIR, '*.md')))


def solution_pages():
    return sorted(glob.glob(os.path.join(SOLUTION_DIR, '*.md')))


def has_question(s):
    if any(m in s for m in QUESTION_MARKS):
        return True
    return any(w in s for w in QUESTION_WORDS)


def ngram_set(s, n=2):
    s = re.sub(r'[\s（()）【】\[\]【】·、，。：；]', '', s)
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


# ---------------------------------------------------------------- 单页自检

def cmd_page(path):
    if not os.path.exists(path):
        print('错误：文件不存在：%s' % path)
        return 2

    msgs = []

    def add(level, area, msg):
        msgs.append((level, area, msg))

    text = read(path)
    fm, body = parse_frontmatter(text)
    name = os.path.splitext(os.path.basename(path))[0]
    idx = page_index()

    print('== 页面自检：%s ==' % path)
    print()

    # --- 1. frontmatter ---
    if fm.get('type') != 'solution':
        add(FAIL, 'frontmatter', 'type 应为 solution，实际为 %r' % fm.get('type', ''))

    for f in REQUIRED_FIELDS:
        if not (fm.get(f) or '').strip():
            add(FAIL, 'frontmatter', '缺少必填字段 %s（09 §2）' % f)

    for f in LEGACY_FIELDS:
        if f in fm:
            add(WARN, 'frontmatter', '残留废弃字段 %s（01–05 v2 已删，09 §2）' % f)

    co = (fm.get('case_origin') or '').strip()
    if co and co not in CASE_ORIGIN_ENUM:
        add(FAIL, 'frontmatter',
            'case_origin 取值不在枚举内：%r（应为 %s）' % (co, '/'.join(sorted(CASE_ORIGIN_ENUM))))

    tags = parse_list_field(fm.get('tags', ''))
    if len(tags) < 2:
        add(WARN, 'frontmatter',
            'tags 只有 %d 个（%s）——需含一个领域词，四页全写同一个标签等于没写（09 §2）'
            % (len(tags), '、'.join(tags) or '空'))

    srcs = parse_list_field(fm.get('sources', ''))
    if not srcs:
        add(FAIL, 'frontmatter', 'sources 为空（09 §2）')
    for s in srcs:
        s_norm = s.replace('\\', '/').lstrip('./')
        if not s_norm.startswith('sources/'):
            add(FAIL, 'frontmatter',
                'sources 里放了非 source 页：%s（可回溯链必须是「本页 → source 页 → raw」）' % s)
            continue
        if not any(os.path.exists(c) for c in (s_norm, os.path.join('wiki', s_norm))):
            add(WARN, 'frontmatter', 'sources 指向的页不存在：%s' % s)

    # --- 2. 标题一致性 ---
    h1 = ''
    for line in body.split('\n'):
        if line.startswith('# '):
            h1 = line[2:].strip()
            break
    fm_title = (fm.get('title') or '').strip()
    if h1 and fm_title and h1 != fm_title:
        add(FAIL, '标题', 'frontmatter title 与正文 H1 不一致：%r vs %r（09 §2）' % (fm_title, h1))

    concept_names = {os.path.splitext(os.path.basename(p))[0] for p in concept_pages()}
    if fm_title and fm_title in concept_names:
        add(FAIL, '标题',
            '与 concept 页同名：%s——同一主题两个权威（09 §6）' % fm_title)

    # --- 3. 引言块 ---
    lead = summary_block(body)
    if not lead:
        add(FAIL, '引言块', '缺 `> ` 一句话引文（09 §3）')
    elif len(lead) < 15:
        add(WARN, '引言块', '引文过短（%d 字），说明不清这个主题解决什么问题' % len(lead))

    # --- 4. 骨架：四段（##）+ 十二节（###）---
    stages, sections = parse_skeleton(body)
    sec_names = [n for n, _, _ in sections]
    # 旧结构：## 层直接是节名，且没有 ### 节
    legacy_layout = (not sections) and any(s in FULL_ORDER for s in stages)

    if legacy_layout:
        secs = [n for n, _ in all_sections(body)]
        add(WARN, '节骨架',
            '仍是十二节平铺的旧骨架——2026-09-25 起改为「四段 H2 + 十二节 H3」，待迁移（09 §3、§9）')
        present = set(secs)
        for s in REQUIRED_SECTIONS:
            if s not in present:
                add(FAIL, '节骨架',
                    '缺必写节「%s」（09 §3 档位：%s）' % (s, LEVEL_LABEL[SECTION_LEVEL[s]]))
        # 旧结构不做顺序检查：新骨架调了相对次序（AI 能力与评估移到「指标与验证」之前、
        # 版本拆分与取舍移到第四段），拿新顺序要求旧页是错配。迁移时一并重排。
    else:
        secs = sec_names
        matched = [(i, s) for i, s in enumerate(stages)
                   if norm_stage_key(s) in STAGE_CORES]
        matched_cores = [STAGE_CORES.index(norm_stage_key(s)) for _, s in matched]

        if not matched:
            add(FAIL, '节骨架',
                '未识别四段骨架——应为「一、这是什么方案 / 二、方案怎么运转 / '
                '三、边界与保障 / 四、怎么落地、还缺什么」（09 §3）')
        else:
            # 段顺序
            for i in range(1, len(matched_cores)):
                if matched_cores[i] < matched_cores[i - 1]:
                    add(FAIL, '节骨架',
                        '段顺序错误：「%s」出现在「%s」之后（09 §3 固定顺序）'
                        % (matched[i][1], matched[i - 1][1]))
                    break
            # 缺段
            for i, core in enumerate(STAGE_CORES):
                if i in matched_cores:
                    continue
                st_name, st_secs = STAGES[i]
                musts = [s for s, lv in st_secs if lv == 'must']
                if musts:
                    add(FAIL, '节骨架',
                        '缺整个段「%s」，而该段含必写节 %s——段内节不能全删（09 §3）'
                        % (st_name, '、'.join(musts)))
                else:
                    add(WARN, '节骨架',
                        '缺段「%s」——确认该段内各节都没料才可删（09 §3）' % st_name)
            # 段内一个节都没有
            for st_name, _ in STAGES:
                if norm_stage_key(st_name) not in [norm_stage_key(s) for _, s in matched]:
                    continue
                inner = [n for n, _, st in sections
                         if norm_stage_key(st) == norm_stage_key(st_name)]
                if not inner:
                    add(WARN, '节骨架',
                        '段「%s」下没有任何节——段内节全删则应整段删（09 §3）' % st_name)

        # 节顺序
        order_idx = {n: i for i, n in enumerate(FULL_ORDER)}
        seq = [(order_idx[n], n) for n in sec_names if n in order_idx]
        for i in range(1, len(seq)):
            if seq[i][0] < seq[i - 1][0]:
                add(FAIL, '节骨架',
                    '节顺序错误：「%s」出现在「%s」之后（09 §3 固定顺序）'
                    % (seq[i][1], seq[i - 1][1]))
                break

        # 节与段的归属固定
        for n, _, st in sections:
            if n not in SECTION_STAGE:
                continue
            exp = SECTION_STAGE[n]
            if norm_stage_key(st) != norm_stage_key(exp):
                add(FAIL, '节骨架',
                    '节「%s」应放在段「%s」下，实际在「%s」（09 §3 归属固定）'
                    % (n, exp, st or '（无段）'))

        # 必写节缺失（条件节不在其列）
        present = set(sec_names)
        for s in REQUIRED_SECTIONS:
            if s not in present:
                add(FAIL, '节骨架',
                    '缺必写节「%s」（09 §3 档位：%s）' % (s, LEVEL_LABEL[SECTION_LEVEL[s]]))

    # 未识别节（两种结构共用）
    known = set(FULL_ORDER) | set(STAGE_CORES)
    extra = [n for n in secs
             if n not in known and norm_stage_key(n) not in STAGE_CORES]
    legacy = [n for n in extra if n in LEGACY_SECTIONS]
    unknown = [n for n in extra if n not in LEGACY_SECTIONS]
    if legacy:
        add(WARN, '节骨架',
            '使用了旧版节名 %s——对照 09 §3 重命名' % '、'.join(legacy))
    if unknown:
        add(WARN, '节骨架', '未识别节 %s（如需保留，确认它不属于十二节要素）' % '、'.join(unknown))

    # --- 5. 条件节启用判据 ---
    # 排除「相关」节：那节的 wikilink 名里可能含「智能体」等词，不是正文内容
    rel_content = find_section(body, '相关') or ''
    body_no_sec = body.replace(rel_content, '') if rel_content else body
    model_hits = [w for w in MODEL_HINTS if w in body_no_sec]
    has_ai_sec = 'AI 能力与评估' in present

    if model_hits and not has_ai_sec:
        add(WARN, '条件节',
            '正文出现模型相关内容（%s）但未启用「AI 能力与评估」节（09 §4.10）'
            % '、'.join(model_hits[:5]))
    if has_ai_sec:
        ai_content = find_section(body, 'AI 能力与评估') or ''
        if not any(w in ai_content for w in MODEL_HINTS):
            add(WARN, '条件节',
                '启用了「AI 能力与评估」节，但该节内容里没有模型相关表述——纯业务系统方案不要为它凑内容')

    # --- 6. 待确认清单 ★ ---
    pend = find_section(body, '待确认清单')
    if pend is None:
        pass  # 已在上面的必备节检查里报过
    else:
        items = []
        for line in pend.split('\n'):
            s = line.strip()
            if not s:
                continue
            s = re.sub(r'^[-*+]\s*', '', s).strip()
            s = re.sub(r'^\d+[.、)]\s*', '', s).strip()
            if s:
                items.append(s)
        if not items:
            add(FAIL, '待确认清单', '本节为空——素材没交代的必须写成问句（09 §4.11）')
        else:
            dodge_items = []
            vague_items = []
            for it in items:
                core = strip_marks(it)
                if core in PLACEHOLDER or len(core) < 4:
                    add(FAIL, '待确认清单', '条目过短或为占位：%r' % it[:40])
                    continue
                d = [p for p in DODGE_PATTERNS if p in it]
                has_gap = any(g in it for g in GAP_MARKERS)
                if d and not has_gap:
                    dodge_items.append((it, d))
                elif not has_question(it):
                    vague_items.append(it)
            for it, d in dodge_items:
                add(FAIL, '待确认清单',
                    '推诿句式 %s —— 待确认项是唯一产出，退化成推回业务等于本页无产出：%r'
                    % ('、'.join(d), it[:50]))
            for it in vague_items:
                add(WARN, '待确认清单',
                    '不像问句（缺疑问词，访谈时问不出口）：%r' % it[:50])

    # --- 7. 实施记录信号 ---
    impl_names = IMPL_NAME_RE.findall(body)
    impl_terms = [t for t in IMPL_TERMS if t in body]
    if impl_names or len(impl_terms) >= 2:
        detail = '、'.join(list(impl_names[:2]) + impl_terms[:3])
        add(WARN, '实施记录',
            '出现具体客户名或交付术语（%s）——本页是参考实现，'
            '具体客户的上线实施方案不入库（09 §7.1 Q4）' % detail)

    # --- 8. 位置标注 ---
    kaku = KAKU_RE.findall(body)
    mdlinks = [u for _, u in MDLINK_RE.findall(body) if 'sources/' in u]
    total_marks = len(kaku) + len(mdlinks)

    same_refs = len(re.findall(r'[（(]同上|[（(]同前|同上\s*§', body))
    if same_refs:
        add(WARN, '位置标注',
            '出现 %d 处「同上」式简写——多源页每条标注必须带来源短名，'
            '读者跨节阅读时无法确定「同上」指哪份素材（09 §4.13）' % same_refs)

    if total_marks == 0:
        add(FAIL, '位置标注', '正文没有任何 〔〕 位置标注（09 §4.13）')
    else:
        unmarked = []
        for s in [x for x in FULL_ORDER if x in present]:
            # 「相关」是链接清单、「待确认清单」装的就是素材没说的内容——都不该要求位置标注
            if s in ('相关', '待确认清单'):
                continue
            c = find_section(body, s)
            if c is None or len(strip_marks(c)) < 30:
                continue
            if not KAKU_RE.search(c) and not any('sources/' in u for _, u in MDLINK_RE.findall(c)):
                unmarked.append(s)
        if unmarked:
            add(WARN, '位置标注',
                '这些节有内容但没有位置标注：%s（09 §4.13）' % '、'.join(unmarked))

    # --- 9. 来源类型标注 ---
    if co in ('外部', '混合'):
        if EXTERNAL_MARK not in body:
            add(WARN, '来源类型',
                'case_origin=%s 但正文没有 `外·` 来源短名标注（09 §4.13）' % co)

    # --- 10. 提炼标记 ---
    n_refine = body.count(REFINE_MARK)
    if n_refine == 0:
        add(WARN, '提炼',
            '全文没有 `**提炼**：` 标记——09 §0 要求「记录 + 提炼」，且提炼必须与事实分开')

    # --- 11. 出链 ---
    todo_targets = set(TODO_LINK_RE.findall(body))
    dead = []
    for t in set(WIKILINK_RE.findall(body)):
        t = t.strip()
        if not t or t == name:
            continue
        if t not in idx and t not in todo_targets:
            dead.append(t)
    if dead:
        add(FAIL, '出链', '死链 %s（目标页不存在，也未标 [待创建: [[X]]]）' % '、'.join('[[%s]]' % d for d in dead))

    rel = find_section(body, '相关')
    if rel is not None:
        targets = [t.strip() for t in WIKILINK_RE.findall(rel)]
        concept_hits = [t for t in targets if t in concept_names]
        if not targets:
            add(FAIL, '相关', '「相关」节没有任何 wikilink（09 §4.12）')
        elif not concept_hits:
            add(WARN, '相关',
                '「相关」节没有一条 concept 出链——方案的设计选择要能追到原理（09 §4.12）')

    # --- 输出 ---
    order = {FAIL: 0, WARN: 1}
    msgs.sort(key=lambda m: order.get(m[0], 2))
    for lv, area, msg in msgs:
        print('%s %-12s %s' % (lv, area, msg))

    nf = sum(1 for m in msgs if m[0] == FAIL)
    nw = sum(1 for m in msgs if m[0] == WARN)
    if not msgs:
        print('全部检查通过，未发现问题。')
    print()
    print('小计：FAIL %d / WARN %d' % (nf, nw))
    print('信息：%d 节 · %d 字符 · 位置标注 %d 处 · 提炼标记 %d 处'
          % (len(secs), len(text), total_marks, n_refine))
    return 1 if nf else 0


# ---------------------------------------------------------------- 全库体检

def cmd_all(limit):
    pages = solution_pages()
    if not pages:
        print('错误：%s 下没有页面' % SOLUTION_DIR)
        return 2

    print('== 全库体检：%d 页 solution ==' % len(pages))
    print()

    area_counter = Counter()
    rows = []
    for p in pages:
        name = os.path.splitext(os.path.basename(p))[0]
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            cmd_page(p)
        finally:
            sys.stdout = old
        txt = out.getvalue()
        nf = txt.count(FAIL)
        nw = txt.count(WARN)
        rows.append((nf, nw, name, txt))
        for m in re.finditer(r'%s\s+(\S+)' % re.escape(FAIL), txt):
            area_counter[m.group(1)] += 1

    print('%-30s %5s %5s' % ('页面', 'FAIL', 'WARN'))
    print('-' * 44)
    for nf, nw, name, _ in rows:
        print('%-30s %5d %5d' % (name[:30], nf, nw))
    print('-' * 44)
    print('%-30s %5d %5d' % ('合计',
                             sum(r[0] for r in rows), sum(r[1] for r in rows)))
    print()

    if area_counter:
        print('FAIL 按检查项聚类：')
        for a, c in area_counter.most_common():
            print('  %-14s %d' % (a, c))
        print()

    print('处理顺序建议：先修出现次数最多的那一类（通常是全库共性问题），再看单页。')
    return 1 if sum(r[0] for r in rows) else 0


# ---------------------------------------------------------------- 查重

def cmd_dup(limit):
    sol = solution_pages()
    con = concept_pages()
    if not sol:
        print('错误：%s 下没有页面' % SOLUTION_DIR)
        return 2

    print('== 与 concept 页的重合检测 ==')
    print('solution %d 页 · concept %d 页' % (len(sol), len(con)))
    print()

    con_map = {}
    for p in con:
        cn = os.path.splitext(os.path.basename(p))[0]
        cfm, _ = parse_frontmatter(read(p))
        con_map[cn] = set(parse_list_field(cfm.get('sources', '')))

    hits = 0
    for p in sol:
        sn = os.path.splitext(os.path.basename(p))[0]
        sfm, _ = parse_frontmatter(read(p))
        stitle = (sfm.get('title') or sn).strip()
        ssrcs = set(parse_list_field(sfm.get('sources', '')))
        stoks = ngram_set(re.sub(r'设计方案|方案|设计', '', stitle))

        print('── %s ──' % sn)

        found = False
        for cn, csrcs in sorted(con_map.items()):
            ctoks = ngram_set(cn)
            j = jaccard(stoks, ctoks)
            sj = jaccard(ssrcs, csrcs)

            if sn == cn or stitle == cn:
                print('  %s 与 concept「%s」完全同名——同一主题两个权威（09 §6）' % (FAIL, cn))
                found = True
                continue

            if j >= 0.35:
                print('  %s 标题与 concept「%s」词面重合 %.0f%%——判断是否主题重叠'
                      % (WARN, cn, j * 100))
                found = True

            if sj >= 0.75 and len(ssrcs & csrcs) >= 2:
                print('  %s 与 concept「%s」源清单重合 %.0f%%（%d/%d）——'
                      '两者可能在讲同一件事的不同侧面'
                      % (WARN, cn, sj * 100, len(ssrcs & csrcs), len(ssrcs | csrcs)))
                found = True

        if not found:
            print('  未发现与 concept 页的重合。')
        else:
            hits += 1
        print()

    print('合计 %d/%d 页存在标题或源清单重合，需人工判断是「主题重叠」还是「正常分工」。' % (hits, len(sol)))
    print()
    print('判断依据（09 §7.2）：concept 讲机制与原理、solution 讲这个实例怎么用。')
    print('两者用出链互指，不复制内容；标题不得相同。')
    return 0


# ---------------------------------------------------------------- 反扫

def cmd_scan(limit):
    print('== 反扫未建页的业务主题候选（09 §8）==')
    print('判据：source 页或 concept 页中出现该业务域的关键词，且尚无 solution 页覆盖')
    print()

    src_files = sorted(glob.glob(os.path.join(SOURCE_DIR, '*.md')))
    con_files = concept_pages()
    if not src_files and not con_files:
        print('错误：%s / %s 下没有页面' % (SOURCE_DIR, CONCEPT_DIR))
        return 2

    # 已建 solution 覆盖了什么
    built = []
    for p in solution_pages():
        fm, body = parse_frontmatter(read(p))
        t = (fm.get('title') or '') + '\n' + body
        built.append((os.path.splitext(os.path.basename(p))[0], t))

    rows = []
    for domain, hints in DOMAIN_HINTS.items():
        src_hit, con_hit = set(), set()
        for p in src_files:
            t = read(p)
            if sum(1 for h in hints if h in t) >= 2:
                src_hit.add(os.path.basename(p)[:-3])
        for p in con_files:
            t = read(p)
            if sum(1 for h in hints if h in t) >= 2:
                con_hit.add(os.path.basename(p)[:-3])

        if not src_hit and not con_hit:
            continue

        cover = [n for n, t in built if sum(1 for h in hints if h in t) >= 2]
        rows.append((len(src_hit), len(con_hit), domain, cover, sorted(con_hit)[:4]))

    rows.sort(key=lambda r: (-(r[0] + r[1]), r[2]))

    print('%-22s %5s %5s  %s' % ('业务域', '源页', '概念页', '状态'))
    print('-' * 74)
    unbuilt = []
    for ns, nc, domain, cover, cons in rows:
        if cover:
            status = '已建：%s' % '、'.join(c[:16] for c in cover)
        else:
            status = '未建 ★'
            unbuilt.append((ns, nc, domain, cons))
        print('%-22s %5d %5d  %s' % (domain, ns, nc, status))

    print()
    if unbuilt:
        print('未建页的候选（按素材覆盖度排序）：')
        for ns, nc, domain, cons in unbuilt[:limit]:
            print('  %s  —— 源页 %d 份' % (domain, ns))
            if cons:
                print('      涉及的 concept：%s' % '、'.join(c[:20] for c in cons))
        print()
        print('处理：对每个候选跑 09 §7.1 的四问闸门——')
        print('      Q1 有具体实例吗？Q2 指得到某个可辨认的讲述吗？')
        print('      Q3 撑得起「数据对象 / 系统协作 / 版本 / 指标」中的两项吗？Q4 是实施记录吗？')
    else:
        print('所有识别到的业务域都已有 solution 页覆盖。')

    print()
    print('⚠️ 本扫描只认词表内的业务域；词表外的领域扫不出来，需人工扫正文。')
    return 0


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(
        description='09 solution 项目设计方案页自检器（只读）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--page', help='单页自检，传页面路径')
    ap.add_argument('--all', action='store_true', help='全库体检 + FAIL 聚类')
    ap.add_argument('--dup', action='store_true', help='与 concept 页标题撞名 + 源清单重合检测')
    ap.add_argument('--scan', action='store_true', help='反扫未建页的业务主题候选')
    ap.add_argument('--limit', type=int, default=20, help='列表条数上限（默认 20）')

    args = ap.parse_args()

    if args.page:
        return cmd_page(args.page)
    if args.all:
        return cmd_all(args.limit)
    if args.dup:
        return cmd_dup(args.limit)
    if args.scan:
        return cmd_scan(args.limit)

    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
