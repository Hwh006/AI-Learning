#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""concept 页硬自检器 —— 页型职责定义见 index.md；页面结构与校验按本 Skill

只读工具：不改动任何文件，只输出检查报告。

为什么需要它
------------
03 规范的核心是「给定一个业务问题，只用 concept 层能不能拿到步骤、条件、数字口径、
失败信号」。这句话本身不可执行，只能人工闭卷作答。

但它的四个高频失败点是可机检的，实测现有 22 页全部命中：

1. 来源用 HTML 注释标注 → 阅读视图不可见，等于没标（18 页）
2. 失效边界里一个数字都没有 → 读者既不知量级也不知该不该信（22 页）
3. 提到具名工具却不出链 → 标准库不连符号表（0 条 entity 出链）
4. 同一机制在多页被重复展开 → 三处维护、互相矛盾

本脚本把这四条变成命令。它验不了语义，只保证「不犯这四类机械错误」。

用法
----
1) 校验一页
   python3 verify_concept_page.py --page wiki/concepts/文档解析.md

2) 全库体检（每页一行）
   python3 verify_concept_page.py --all

3) 出链检查：死链 + 提到却没出链的具名对象
   python3 verify_concept_page.py --links

4) 跨页重复定义检测
   python3 verify_concept_page.py --dup

5) 待建页清单（来自 sources 的「关键概念」节与全库 [待创建:] 标记）
   python3 verify_concept_page.py --stubs

退出码
------
0 = 全部通过（可能含 WARN）
1 = 存在 FAIL
2 = 用法或文件错误
"""

import argparse
import glob
import os
import re
import sys
from collections import Counter, defaultdict

# ---------- 常量 ----------

OK, WARN, FAIL = 'OK', 'WARN', 'FAIL'

CONCEPT_DIR = 'wiki/concepts'
SEARCH_DIRS = ['wiki/concepts', 'wiki/entities', 'wiki/solutions',
               'wiki/comparisons', 'wiki/sources', 'wiki/map', 'wiki/notes',
               'wiki/queries', 'wiki/synthesis']

REQUIRED_FM = ['type', 'title', 'concept_type', 'sources', 'status', 'tags']
DRIFT_FM = ['author', 'created', 'updated', 'source_path', 'sha256',
            'material_type', 'reliability', 'ingested_at']
CONCEPT_TYPE_ENUM = {'机制', '方法', '指标', '架构', '术语'}

# 十节骨架（顺序固定）。旧名 -> 新名 用于给出迁移提示。
SECTION_ORDER = ['是什么', '有什么用', '怎么运作', '怎么用', '适用场景',
                 '容易混的点', '失效边界', '来源与证据', '相关']
RENAMED_SECTIONS = {'不同素材中的观点': '来源与证据',
                    '来源与证据表': '来源与证据',
                    '新入库素材中的补充说法': '（删除，合并进「来源与证据」表）'}

# 必须有实质内容的节
MUST_HAVE_BODY = ['是什么', '怎么运作', '容易混的点', '失效边界', '来源与证据', '相关']

PLACEHOLDER_MARKERS = ['全文检索', '自行检索', '请自行搜索']
PLACEHOLDER_WORDS = ['待补充', '待完善', '待定', '暂无', 'TBD', 'TODO', '略', '略述']

NUM_RE = re.compile(r'\d+(?:\.\d+)?\s*%|\$\s*\d|\d+\s*(?:万|亿|倍|天|小时|分钟|秒|毫秒|个|条|人|次|家|页|分)|\d+\.\d+\s*分|\d{2,}')
POS_RE = re.compile(r'〔([^〕]*)〕')
WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)\]\]')
TODO_LINK_RE = re.compile(r'\[待创建\s*[：:]\s*\[\[([^\]|]+?)\]\]')
STUB_LINK_RE = TODO_LINK_RE
# 错误写法：把「待创建」写进了 wikilink 内部 → 会生成一个名字荒谬的页面条目
BAD_STUB_RE = re.compile(r'\[\[\s*待创建\s*[：:]\s*([^\]|]+?)\]\]')
HTML_CONF_RE = re.compile(r'<!--\s*confidence')
# 「第 N 段」「§…」「@说话人 00:26」开头 = 没带来源短名
POS_NO_SOURCE_RE = re.compile(r'^(?:第\s*\d+\s*段|§|@)')

EVAL_WORDS = ['非常好用', '强烈推荐', '业界领先', '颠覆性', '最好用', '最差',
              '我认为', '我觉得', '毫无疑问', '显然优于']

# concept_type 专项检查词
METRIC_WORDS = ['衡量', '口径', '分母', '样本', '方向', '阈值', '不能证明', '计算']
METHOD_WORDS = ['目的', '输入', '步骤', '依据', '案例', '反例', '边界', '追问']

# 具名对象识别：高置信工具名（本项目语料实际出现过的）
TOOL_HINTS = [
    'FAISS', 'Milvus', 'Qdrant', 'Weaviate', 'Pinecone', 'Elasticsearch',
    'Zvec', 'ChromaDB', 'PGVector', 'Redis', 'MinIO', 'DuckDB', 'ClickHouse',
    'Dify', 'Coze', 'n8n', 'LangChain', 'LlamaIndex', 'RAGFlow', 'ragflow',
    'Ollama', 'vLLM', 'FastGPT', 'MaxKB', 'AnythingLLM', 'LangSmith', 'RAGAS',
    'MinerU', 'PaddleOCR', 'PaddleOCR-VL',
    'DeepSeek', 'Qwen', 'ChatGLM', 'GLM', 'Kimi', 'LoRA', 'QLoRA', 'BGE',
    'bge-m3', 'OpenAI', 'ChatGPT', 'Claude', 'Gemini', 'LLaMA', 'Mistral',
    'PostgreSQL', 'Postgres', 'MongoDB', 'Neo4j', 'Kafka',
    '讯飞', '商汤', '通义', '豆包', '元宝', '飞书', '智谱',
]

# 泛称 / 概念缩写：不是 entity，不该被要求出链
CONCEPT_STOP = {
    'RAG', 'LLM', 'API', 'OCR', 'SQL', 'FAQ', 'CRUD', 'RPA', 'NLP', 'ASR',
    'TTS', 'UI', 'UX', 'JSON', 'YAML', 'HTTP', 'URL', 'ID', 'PDF', 'WORD',
    'EXCEL', 'PPT', 'MARKDOWN', 'CSV', 'XML', 'HTML', 'CSS', 'SDK', 'CLI',
    'PROMPT', 'CHUNK', 'EMBEDDING', 'RERANK', 'QUERY', 'ROUTER', 'AGENT',
    'SOP', 'POC', 'ROI', 'KA', 'TOB', 'TOC', 'QA', 'PIPELINE', 'WORKFLOW',
    'SCHEMA', 'TOKEN', 'TOOL', 'FUNCTION', 'MEMORY', 'CONTEXT', 'CACHE',
    'BM25', 'TF', 'IDF', 'MRR', 'NDCG', 'AUC', 'F1', 'KV',
    # 置信度标记与元字段（来自 HTML 注释）
    'EXTRACTED', 'INFERRED', 'CONFIDENCE',
    # 本库常见概念缩写与范式名（不是 entity）
    'AI', 'PRD', 'CRM', 'ERP', 'B2B', 'B2C', 'MCP', 'DSL', 'KA', 'SPU', 'SKU',
    'REACT', 'CALLING', 'THOUGHT', 'AGENT', 'CHAIN', 'TEXT', 'OVERLAP',
    'SIZE', 'TYPE', 'MODE', 'LEVEL', 'STEP', 'LIST', 'DATA', 'SERVICE',
    'RERANKER', 'MTEB', 'MMR', 'LTR', 'HNSW', 'IVF', 'PQ', 'ANN', 'TASK',
}


# ---------- 基础 ----------

def read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def norm(s):
    """去掉所有空白，用于容忍折行差异。"""
    return re.sub(r'\s+', '', s)


def strip_marks(s):
    """去掉标点，用于句子指纹比对。"""
    return re.sub(r'[\s，。、；：？！,,.;:?!"\'「」『』（）()\[\]【】]', '', s)


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
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        fm[k.strip()] = v
    return fm, text[m.end():]


def parse_list(v):
    """解析 frontmatter 里的 [a, b, c]。"""
    if not v:
        return []
    v = v.strip()
    if v.startswith('[') and v.endswith(']'):
        v = v[1:-1]
    return [x.strip().strip('"').strip("'") for x in v.split(',') if x.strip()]


def split_sections(body):
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


def all_pages(directory=None):
    d = directory or CONCEPT_DIR
    return sorted(glob.glob(os.path.join(d, '*.md')))


def page_index():
    """全库页面名 -> 路径。用于出链有效性检查。"""
    idx = {}
    for d in SEARCH_DIRS:
        for p in glob.glob(os.path.join(d, '*.md')):
            idx[os.path.splitext(os.path.basename(p))[0]] = p
    return idx


# ---------- --page ----------

def check_page(page_path, quiet=False):
    """返回 (results, stats)。results = [(level, item, msg)]"""
    text = read(page_path)
    fm, body = parse_frontmatter(text)
    results = []

    def add(level, item, msg):
        results.append((level, item, msg))

    stats = {'pos': 0, 'pos_named': 0, 'links': 0, 'todo_links': 0,
             'sources': 0, 'evidence_rows': 0}

    if fm is None:
        add(FAIL, 'frontmatter', 'frontmatter 缺失（文件未以 --- 开头的 YAML 块起始）')
        return results, stats

    # --- frontmatter ---
    for k in REQUIRED_FM:
        if k not in fm:
            add(FAIL, 'frontmatter', '缺少必填字段：%s' % k)
    for k in DRIFT_FM:
        if k in fm:
            add(WARN, 'frontmatter', '存在已废弃字段：%s（01/02/03 均已移除，不应再写入）' % k)
    if fm.get('type') and fm['type'] != 'concept':
        add(FAIL, 'frontmatter', 'type 应为 concept，实际为 %s' % fm['type'])
    ct = fm.get('concept_type', '')
    # 缺字段已由 REQUIRED_FM 循环报出，这里只查取值
    if ct and ct not in CONCEPT_TYPE_ENUM:
        add(WARN, 'frontmatter', 'concept_type 取值不在枚举内：%s（应为 %s）'
            % (ct, '/'.join(sorted(CONCEPT_TYPE_ENUM))))
    if not fm.get('title'):
        add(FAIL, 'frontmatter', 'title 为空')
    if not fm.get('tags') or parse_list(fm.get('tags', '')) == []:
        add(WARN, 'frontmatter', 'tags 为空（至少需要一个有检索价值的主题标签）')

    sources = parse_list(fm.get('sources', ''))
    stats['sources'] = len(sources)
    if not sources:
        add(FAIL, 'frontmatter', 'sources 为空：概念页必须能追到来源')
    for s in sources:
        if not s:
            continue
        if not s.endswith('.md'):
            add(WARN, 'frontmatter', 'sources 条目不是 .md 路径：%s' % s)
            continue
        # sources 里写的是相对 wiki/ 的路径（sources/xxx.md），也容忍相对项目根的写法
        if not (os.path.exists(s) or os.path.exists(os.path.join('wiki', s))):
            add(WARN, 'frontmatter', 'sources 指向的页不存在：%s' % s)
    # 单源页写了多源内容的自检在「来源与证据」处做

    h1 = re.search(r'^#\s+(.+)$', body, re.M)
    if h1 and fm.get('title'):
        if norm(h1.group(1)) != norm(fm['title']):
            add(WARN, 'frontmatter', 'frontmatter title 与正文 H1 不一致：「%s」vs「%s」'
                % (fm['title'], h1.group(1).strip()))
    elif not h1:
        add(WARN, 'frontmatter', '正文缺少 H1 标题')

    # 一句话定位
    if h1:
        after = body[h1.end():]
        if not re.match(r'\s*>\s*\S', after):
            add(WARN, '结构', 'H1 下方缺少「> 一句话定位」引用块')

    # --- 章节骨架 ---
    titles = [t for t, _ in split_sections(body)]
    for old, new in RENAMED_SECTIONS.items():
        for t in titles:
            if old in t:
                add(WARN, '结构', '使用了旧节名「%s」，03 §3 已改为「%s」' % (old, new))
    missing = [s for s in SECTION_ORDER if not any(s in t for t in titles)]
    for s in missing:
        add(FAIL, '结构', '缺少章节：%s' % s)
    found_order = [next((s for s in SECTION_ORDER if s in t), None) for t in titles]
    found_order = [s for s in found_order if s]
    if found_order != sorted(found_order, key=SECTION_ORDER.index):
        add(WARN, '结构', '章节顺序与 03 §3 骨架不一致：%s' % ' · '.join(found_order))

    for kw in MUST_HAVE_BODY:
        title, content = find_section(body, kw)
        if title is None:
            continue
        plain = re.sub(r'^\s*[|\-\s]*$', '', content, flags=re.M)
        if len(norm(plain)) < 20:
            add(FAIL, kw, '章节「%s」内容过少（<20 字），疑似占位' % title)
            continue
        for w in PLACEHOLDER_WORDS:
            if re.search(r'(?<![一-龥])' + re.escape(w) + r'(?![一-龥])', content) and len(norm(content)) < 60:
                add(WARN, kw, '疑似占位语「%s」' % w)
                break

    # --- 来源标注 ---
    if HTML_CONF_RE.search(body):
        add(FAIL, '来源标注',
            '出现 HTML 注释来源（<!-- confidence: ... -->）——阅读视图不可见，等于没标。'
            '改写成行内 〔位置〕（03 §4.15）')
    positions = POS_RE.findall(body)
    stats['pos'] = len(positions)
    stats['pos_named'] = sum(1 for p in positions if p.strip() and not POS_NO_SOURCE_RE.match(p.strip()))
    for marker in PLACEHOLDER_MARKERS:
        if marker in body:
            add(FAIL, '来源标注', '出现占位符「%s」——那不是位置' % marker)
    if not positions:
        add(WARN, '来源标注', '正文没有任何 〔位置〕 标注，来源不可核')
    elif len(sources) >= 2 and stats['pos_named'] == 0:
        add(WARN, '来源标注',
            '多源页（%d 个来源）但没有任何标注带来源短名，读者无法知道该去哪份 source 核（03 §4.15）'
            % len(sources))
    elif len(sources) >= 2 and stats['pos_named'] < max(1, len(positions) // 3):
        add(WARN, '来源标注',
            '多源页仅 %d/%d 处标注带来源短名，建议提高（03 §4.15）'
            % (stats['pos_named'], len(positions)))

    # --- 失效边界必须有数字 ---
    title_b, content_b = find_section(body, '失效边界')
    if content_b is not None:
        clean_b = POS_RE.sub('', content_b)
        if not NUM_RE.search(clean_b):
            add(WARN, '失效边界',
                '一个数字都没有。只写「数字不可信」等于没写——读者既不知量级也不知该不该信。'
                '有数字就写「约 70% 多 + 无分母无测量方法」（03 §4.8）')

    # --- 来源与证据表 ---
    title_e, content_e = find_section(body, '来源与证据')
    if content_e is not None:
        rows = [ln for ln in content_e.split('\n')
                if ln.strip().startswith('|') and not re.match(r'^\s*\|[\s\-:|]+\|\s*$', ln)]
        rows = [r for r in rows if not re.search(r'\|\s*来源与位置\s*\|', r)]
        stats['evidence_rows'] = len(rows)
        if len(rows) == 0:
            add(FAIL, '来源与证据', '表格没有数据行')
        elif len(sources) >= 2 and len(rows) < len(sources):
            add(WARN, '来源与证据',
                'sources 列了 %d 个来源，表里只有 %d 行——是否有来源没登记说法？'
                % (len(sources), len(rows)))
        if re.search(r'新入库素材|补充说法|新增来源', content_e):
            add(WARN, '来源与证据', '表内出现「新入库/补充说法」式小节——应合并进同一张表，不新开节')

    # --- 相关节 ---
    bad_stub = BAD_STUB_RE.findall(body)
    if bad_stub:
        add(FAIL, '相关', '「待创建」写进了 wikilink 内部：%s —— 应写成 [待创建: [[名称]]]，'
            '否则会生成名字荒谬的页面条目' % '、'.join(bad_stub[:3]))
    title_r, content_r = find_section(body, '相关')
    if content_r is not None:
        links = WIKILINK_RE.findall(content_r)
        todos = TODO_LINK_RE.findall(content_r)
        stats['links'] = len(links)
        stats['todo_links'] = len(todos)
        if len(links) < 3:
            add(WARN, '相关', '只有 %d 个链接（建议 4–8 个）' % len(links))
        elif len(links) > 12:
            add(WARN, '相关', '有 %d 个链接，过多（建议 4–8 个）' % len(links))

    # --- 评价词 ---
    for w in EVAL_WORDS:
        if w in body:
            add(WARN, '归属', '出现主观词「%s」——确认它是素材的判断（可留）还是你自己的评价（应删）' % w)

    # --- concept_type 专项 ---
    if ct == '指标':
        title_w, content_w = find_section(body, '怎么运作')
        blob = (content_w or '') + (find_section(body, '失效边界')[1] or '')
        lack = [w for w in ['衡量', '口径', '不能证明'] if w not in blob]
        if lack:
            add(WARN, 'concept_type',
                '指标类概念未写清：%s（03 §4.11 要求回答「衡量什么 / 怎么算 / 方向 / 阈值条件 / 不能证明什么」）'
                % '、'.join(lack))
    elif ct == '方法':
        blob = body
        lack = [w for w in METHOD_WORDS if w not in blob]
        if len(lack) >= 3:
            add(WARN, 'concept_type',
                '方法类概念缺少要素：%s（03 §4.12 要求目的与输入 / 执行步骤 / 判断依据 / 案例 / 反例）'
                % '、'.join(lack))

    return results, stats


def cmd_page(page_path):
    print('== 校验 %s ==' % page_path)
    results, stats = check_page(page_path)
    if not results:
        print('OK   无问题')
    else:
        cur_item = None
        for level, item, msg in results:
            if item != cur_item:
                print()
                print('── %s ──' % item)
                cur_item = item
            print('%-4s %s' % (level, msg))

    n_fail = sum(1 for r in results if r[0] == FAIL)
    n_warn = sum(1 for r in results if r[0] == WARN)
    print()
    print('── 汇总 ──')
    print('来源 %d 个 / 位置标注 %d 处（带来源短名 %d）/ 相关链接 %d 个 / 来源表 %d 行'
          % (stats['sources'], stats['pos'], stats['pos_named'],
             stats['links'], stats['evidence_rows']))
    print('失败 %d / 警告 %d' % (n_fail, n_warn))
    if n_fail:
        print('FAIL 项必须修完再交付。修好后重跑本脚本，退出码 0 才算完成。')
    elif n_warn:
        print('无硬失败，但警告项需要逐条人工判断。')
    else:
        print('全部机检项通过。注意：脚本验不了 §0 那条——'
              '拿 5 个业务问题闭卷答一遍，能不能只靠本页答出来，只能人工做。')
    return 1 if n_fail else 0


# ---------- --all ----------

def cmd_all():
    pages = all_pages()
    if not pages:
        print('错误：%s 下没有页面' % CONCEPT_DIR)
        return 2
    print('== 全库 concept 体检（%d 页）==' % len(pages))
    print()
    print('%-34s %5s %5s  %s' % ('页面', 'FAIL', 'WARN', '主要问题'))
    print('-' * 100)
    total_fail = total_warn = 0
    detail = []
    for p in pages:
        results, _ = check_page(p)
        fails = [r for r in results if r[0] == FAIL]
        warns = [r for r in results if r[0] == WARN]
        total_fail += len(fails)
        total_warn += len(warns)
        top = fails[0][2] if fails else (warns[0][2] if warns else '')
        name = os.path.splitext(os.path.basename(p))[0]
        print('%-34s %5d %5d  %s' % (name[:34], len(fails), len(warns), top[:56]))
        if fails:
            detail.append((name, fails))
    print('-' * 100)
    print('合计：FAIL %d / WARN %d' % (total_fail, total_warn))

    # 共性问题聚类
    cnt = Counter()
    for name, fails in detail:
        for _, item, _ in fails:
            cnt[item] += 1
    if cnt:
        print()
        print('── FAIL 聚类（共性问题）──')
        for item, n in cnt.most_common():
            print('  %-14s %d 页' % (item, n))

    print()
    print('提示：旧版页面可能没有 concept_type 或使用 HTML 注释记录来源，')
    print('      FAIL 集中在 frontmatter 与来源标注属预期。迁移按 03 §4.15 与 §2 执行。')
    return 1 if total_fail else 0


# ---------- --links ----------

def find_object_mentions(text):
    """返回 (high, low)。

    high = 高置信工具名（本项目语料实际出现过的），命中即提示补出链。
    low  = 通用大写词，只是线索——可能是英文短语的构成词（Chunk Size 里的 Size），
           也可能真是一个对象。脚本不替人判断，单独归为低置信。
    """
    high = Counter()
    low = Counter()
    for name in TOOL_HINTS:
        pat = re.compile(r'(?<![A-Za-z0-9\-])' + re.escape(name) + r'(?![A-Za-z0-9\-])', re.I)
        n = len(pat.findall(text))
        if n:
            high[name] += n
    for m in re.finditer(r'(?<![A-Za-z0-9\-])([A-Z][A-Za-z0-9\-]{2,}|[A-Z]{2,})(?![A-Za-z0-9\-])', text):
        w = m.group(1)
        if w.upper() in CONCEPT_STOP or w in TOOL_HINTS:
            continue
        low[w] += 1
    return high, low


def cmd_links():
    idx = page_index()
    pages = all_pages()
    if not pages:
        print('错误：%s 下没有页面' % CONCEPT_DIR)
        return 2

    print('== 出链检查 ==')
    print('全库可解析页面：%d 个' % len(idx))
    print()

    dead_total = miss_total = low_total = badstub_total = 0
    for p in pages:
        text = read(p)
        _, body = parse_frontmatter(text)
        name = os.path.splitext(os.path.basename(p))[0]

        # 死链：目标不存在且未标 [待创建:]
        todo_targets = set(TODO_LINK_RE.findall(body))
        dead = []
        badstub = []
        for target in WIKILINK_RE.findall(body):
            t = target.strip()
            if t in idx or t in todo_targets:
                continue
            if re.match(r'^待创建\s*[：:]', t):
                badstub.append(t)
                continue
            dead.append(t)

        # 提到却没出链的具名对象
        linked = set(WIKILINK_RE.findall(body)) | todo_targets
        high, low = find_object_mentions(body)

        def unlinked(counter):
            out = [w for w in counter
                    if w not in linked
                    and not any(w in l or l in w for l in linked)
                    and w not in idx]
            out.sort(key=lambda w: -counter[w])
            return out

        hi = unlinked(high)
        lo = unlinked(low)

        if not dead and not hi and not lo and not badstub:
            continue
        print('── %s ──' % name)
        if badstub:
            badstub_total += len(badstub)
            print('  %s 「待创建」写进了 wikilink 内部 %d 处（应写成 [待创建: [[名称]]]）：'
                  % (FAIL, len(badstub)))
            for t in badstub[:6]:
                print('       [[%s]]' % t)
        if dead:
            dead_total += len(dead)
            print('  %s 死链 %d 个（目标页不存在，也没标 [待创建:]）：' % (FAIL, len(dead)))
            for t in dead[:10]:
                print('       [[%s]]' % t)
        if hi:
            miss_total += len(hi)
            print('  %s 提到却没出链的具名对象 %d 个（03 §1：首次出现处应出链）:' % (WARN, len(hi)))
            for w in hi[:12]:
                print('       %s（出现 %d 次）' % (w, high[w]))
            if len(hi) > 12:
                print('       … 另有 %d 个' % (len(hi) - 12))
        if lo:
            low_total += len(lo)
            print('  低置信线索 %d 个（可能是英文短语构成词，也可能是对象，需人工看一眼）:' % len(lo))
            print('       %s' % '、'.join('%s×%d' % (w, low[w]) for w in lo[:8]))
        print()

    print('合计：死链 %d / 「待创建」格式错误 %d / 未见出链的具名对象 %d / 低置信线索 %d'
          % (dead_total, badstub_total, miss_total, low_total))
    print()
    print('处理：死链改写成 [待创建: [[名称]]] 或补建页；具名对象在首次出现处补链接。')
    print('注意：识别是启发式（拉丁词表 + 通用大写词），中文无后缀专名（豆包、元宝这类）')
    print('      不在词表内，扫不出来，需人工扫正文与表格。')
    return 1 if badstub_total else 0


# ---------- --dup ----------

def sentences_of(body):
    """返回 [(节名, 句子)]。「相关」节跳过——链接行重复是正常的。"""
    out = []
    for title, content in split_sections(body):
        if '相关' in title:
            continue
        for s in re.split(r'[。！？\n]', content):
            s = s.strip()
            s = re.sub(r'^[\-\*\d\.\)\s|]+', '', s)
            s = POS_RE.sub('', s)
            if len(strip_marks(s)) >= 25:
                out.append((title, s))
    return out


def cmd_dup(threshold):
    pages = all_pages()
    if not pages:
        print('错误：%s 下没有页面' % CONCEPT_DIR)
        return 2

    print('== 跨页重复定义检测（句子 ≥25 字，去标点后完全一致）==')
    print('阈值：同一句出现在 ≥%d 页即报警' % threshold)
    print()

    holders = defaultdict(list)
    for p in pages:
        text = read(p)
        _, body = parse_frontmatter(text)
        name = os.path.splitext(os.path.basename(p))[0]
        seen = set()
        for section, s in sentences_of(body):
            key = strip_marks(s)
            if (key, section) in seen:
                continue
            seen.add((key, section))
            holders[key].append((name, section, s))

    dup = {k: v for k, v in holders.items() if len(v) >= threshold}
    if not dup:
        print('未发现跨页重复的完整句子。')
        print('注意：本检查只比对逐字相同的句子；换了说法的重复定义查不出来，仍需人工看。')
        return 0

    print('发现 %d 组重复句：' % len(dup))
    # 正文节的重复比来源表的重复严重，先列
    def severity(v):
        return 0 if not all('来源' in s for _, s, _ in v) else 1
    for k, v in sorted(dup.items(), key=lambda x: (severity(x[1]), -len(x[1]))):
        sections = sorted(set(s for _, s, _ in v))
        only_src = all('来源' in s for s in sections)
        tag = '来源登记重复（通常可忽略）' if only_src else '疑似重复定义，需处理'
        print()
        print('  [%s] 出现在 %d 页：%s' % (tag, len(v), ' · '.join(n for n, _, _ in v)))
        print('    所在节：%s' % ' / '.join(sections[:4]))
        print('    「%s」' % v[0][2][:70])
    print()
    print('处理（03 §4.13）：留一处展开，其余改成一句 + 出链。不删内容，是把展开点收敛到一页。')
    print('来源登记重复若属实（同一 source 确实支撑多页），保留即可。')
    return 0


# ---------- --stubs ----------

def cmd_stubs(limit):
    idx = page_index()
    wanted = Counter()
    bad = []

    for p in glob.glob('wiki/**/*.md', recursive=True):
        text = read(p)
        for m in STUB_LINK_RE.finditer(text):
            wanted[m.group(1).strip()] += 1
        for m in BAD_STUB_RE.finditer(text):
            bad.append((p, m.group(1).strip()))
        # sources 的「关键概念」节里挂的 [[X]] 也是待建信号
        if os.sep + 'sources' + os.sep in p:
            t, c = find_section(text, '关键概念')
            if c:
                for name in WIKILINK_RE.findall(c):
                    n = name.strip()
                    if n not in idx and not n.startswith('待创建'):
                        wanted[n] += 1

    if bad:
        print('%s 「待创建」写进了 wikilink 内部，会生成名字荒谬的页面条目：' % FAIL)
        for p, n in bad:
            print('     %s' % p)
            print('       现写：[[待创建：%s]]   → 应写：[待创建: [[%s]]]' % (n, n))
        print()

    print('== 待建页清单（来自 [待创建: [[X]]] 标记 + sources 的「关键概念」节）==')
    print()
    rows = [(n, c) for n, c in wanted.items() if n not in idx]
    if not rows:
        print('没有待建项。')
        return 1 if bad else 0

    rows.sort(key=lambda x: (-x[1], x[0]))
    print('%-30s %s' % ('目标名', '被引用次数'))
    print('-' * 46)
    for n, c in rows[:limit]:
        print('%-30s %d' % (n[:30], c))
    if len(rows) > limit:
        print('… 另有 %d 个，用 --limit 调整' % (len(rows) - limit))
    print()
    print('合计 %d 个未建目标。' % len(rows))
    print('处理：先按 03 §1 判「该不该建页」，再按 §4.13 判「该不该独立成页」——')
    print('      不是每个待建项都该有独立页面（有的应作为父页一节）。')
    return 1 if bad else 0


# ---------- 入口 ----------

def main():
    ap = argparse.ArgumentParser(
        description='concept 页硬自检器（Wiki Skill 页面结构校验）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--page', help='要校验的 concept 页路径')
    ap.add_argument('--all', action='store_true', help='全库体检，每页一行')
    ap.add_argument('--links', action='store_true', help='出链检查：死链 + 提到却没出链的具名对象')
    ap.add_argument('--dup', action='store_true', help='跨页重复定义检测')
    ap.add_argument('--stubs', action='store_true', help='待建页清单')
    ap.add_argument('--threshold', type=int, default=2, help='--dup 的页数阈值，默认 2')
    ap.add_argument('--limit', type=int, default=40, help='--stubs 的显示上限，默认 40')
    args = ap.parse_args()

    if not os.path.isdir(CONCEPT_DIR):
        print('错误：找不到 %s（相对路径请从项目根目录运行本脚本）' % CONCEPT_DIR, file=sys.stderr)
        return 2

    if args.page:
        if not os.path.exists(args.page):
            print('错误：找不到页面 %s' % args.page, file=sys.stderr)
            return 2
        return cmd_page(args.page)
    if args.all:
        return cmd_all()
    if args.links:
        return cmd_links()
    if args.dup:
        return cmd_dup(args.threshold)
    if args.stubs:
        return cmd_stubs(args.limit)

    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
