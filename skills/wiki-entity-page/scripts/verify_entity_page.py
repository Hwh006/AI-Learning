#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""entity 页硬自检器 —— 页型职责定义见 index.md；页面结构与校验按本 Skill

只读工具：不改动任何文件，只输出检查报告。

为什么需要它
------------
02 规范有两个病，都不是措辞问题，是「缺一个可机械执行的动作」：

1) 产出为 0：没有任何一步去问「这份素材里出现了哪些具体对象」。
   → `--scan` / `--scan-all` 从素材反扫具名对象，并对照符号表标出「已登记 / 待建页」。

2) 符号表失职：不查重、不记别名，同一对象会并存多页，引用不一致。
   → `--registry` 建立 title+aliases 索引，查出重名与别名冲突。
   → `--registry --check "名称"` 做名称解析测试：这个词落到哪一页。

3) 页面本身：七节骨架是否齐、生命周期是否答、位置能否回溯、有没有入边。
   → `--page`。

核心判据（02 §0）
----------------
「给定一个对象名（或其任一别名），全库只有一页承接它；读完这一页，知道它是什么、
什么场景用它、它现在还有效吗。」——本脚本机检前两条的可机检部分，第三条靠人工。

位置标注口径
------------
沿用 01 的分档：`§二.2.4`（结构化材料）/ `第 N 段「段首十字…」`（口语转写）/
`HH:MM`（智能纪要）。实体页的 `sources:` 指向 source 页，脚本会顺着 source 页的
source_path 找到 raw，再拿位置去 raw 里核。**位置不要手工数，用 01 的 --locate 取。**

用法
----
  python3 verify_entity_page.py --page wiki/entities/FAISS.md
  python3 verify_entity_page.py --registry
  python3 verify_entity_page.py --registry --check "Dify 平台"
  python3 verify_entity_page.py --raw raw/record_cleaned_markdown/xxx.md --scan
  python3 verify_entity_page.py --scan-all
  python3 verify_entity_page.py --context "Milvus"

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
from collections import defaultdict

# ---------- 常量：字段 ----------

REQUIRED_FM = ['type', 'title', 'aliases', 'entity_type', 'sources',
               'status', 'lifecycle', 'corroboration', 'tags']
DRIFT_FM = ['created', 'updated', 'valid_until', 'author',
            'material_type', 'reliability', 'sha256']
ENTITY_TYPE_ENUM = ['工具', '产品', '组织', '人物', '论文', '事件']
LIFECYCLE_ENUM = ['active', 'deprecated', 'unknown']
CORROBORATION_ENUM = ['单源', '多源']

# ---------- 常量：骨架（02 §3） ----------

REQUIRED_SECTIONS = ['是什么', '关键事实', '素材中的定位', '出现记录', '生命周期', '相关']
SECTION_LABEL = {
    '是什么': '是什么（定义/描述，1–3 句）',
    '关键事实': '关键事实（表格：属性|值|来源）',
    '素材中的定位': '素材中的定位（本页核心：场景|理由〔位置〕）',
    '出现记录': '出现记录（表格：素材|说法|位置）',
    '生命周期': '生命周期（必答：当前状态/依据/变动）',
    '相关': '相关（wikilink）',
}

# ---------- 常量：文本判据 ----------

QUOTE_RE = re.compile(r'「([^」]+)」')
POS_RE = re.compile(r'〔([^〕]+)〕')
FENCE_RE = re.compile(r'```.*?```', re.S)
TS_RE = re.compile(r'\d{1,2}:\d{2}')
PARA_POS_RE = re.compile(r'第\s*(\d+)\s*段\s*(?:「(.*?)」)?')
# 多源页面的位置标注常带来源短名（「文档解析探讨 第 30 段「…」」），段号是**该份原文**的段号，
# 不能拿合并后的段落表去核。这里把可选短名一起捕获，按来源分别解析。
SRC_HINT_POS_RE = re.compile(r'^([^\s〔〕，,。、；;：:0-9]{2,20})\s+第\s*(\d+)\s*段\s*(?:「(.*?)」)?')
SEC_POS_RE = re.compile(r'§\s*([一二三四五六七八九十百]+)?\s*[.、]?\s*(\d+(?:\.\d+)*)')
# 无编号章节的材料（英文网页文章等）位置写成「§英文标题路径」；开篇部分（H1 下、首个 ## 之前）
# 没有标题可指，统一写「§开篇」。两者都可能带来源短名前缀（「上下文工程新规则 §开篇」），
# 与 SRC_HINT_POS_RE 同一套约定（01 §5.3 / 02 §4.7）。
SRC_HINT_SEC_RE = re.compile(r'^(?P<hint>[^\s〔〕，,。、；;：:0-9]{2,20})\s+§\s*(?P<sec>.+)$')
SEC_PATH_RE = re.compile(r'^§\s*(?P<sec>.+)$')
OPENING_SECS = ('开篇', '前言', '全文')
# 位置标注有时裸写在表格单元格里（出现记录的位置列），做评价词/推测词扫描时也要一并剔掉——
# 「第 30 段「最好的开源知识库，没…」」里的「最好」是素材原话当定位标签，不是页面在下评价。
POS_STRIP_RE = re.compile(r'第\s*\d+(?:\s*[–—~-]\s*\d+)?\s*段(?:\s*「[^」]*」)?'
                          r'|§\s*[^〕\n]*')
CN_NUM_RE = re.compile(r'^#{1,4}\s*([一二三四五六七八九十百]+)\s*[、.，,]')

# 无源评价 / 猜测：entity 页只登记，不评价（02 §8.3）
EVAL_WORDS = ['很好用', '非常好用', '好用', '不好用', '最好', '最强', '最差', '业界领先',
              '颠覆', '首选', '垃圾', '优秀', '强大', '完美', '碾压', '吊打', '不如']
SPECULATION_WORDS = ['应该不贵', '推测', '大概', '听说', '网上', '可能有', '估计']
PLACEHOLDER_WORDS = ['暂无', '待补充', 'TODO', 'N/A', '待填']

# 泛称 / 概念名：这些不该建 entity 页（02 §1、§8.1）
GENERIC_NAMES = ['大模型', '向量数据库', '低代码平台', '知识库', '提示词', '智能体',
                 '工作流', '知识图谱', '对话系统', '检索增强生成']
CONCEPT_NAMES = ['rag', 'llm', 'nlp', 'ocr', 'asr', 'bm25', 'mmr', 'cot', 'prd',
                 'sql', 'json', 'api', 'app', 'ppt', 'pdf', 'roi', 'sop', 'poc',
                 'kpi', 'okr', 'crm', 'erp', 'saas', 'faq', 'ui', 'ux', 'it',
                 'agent', 'chatbi', 'text2sql', 'embedding', 'rerank', 'chunk',
                 'prompt', 'token', '向量', '分块', '重排序', '混合检索', '知识库运营']
# 有分歧、需人工裁定（02 §8.1 明确列出 MCP）
CONTROVERSIAL = ['mcp', 'a2a', 'function calling', 'tool use']

# ---------- 常量：扫描 ----------
#
# 三条抽取通道，精度差别很大，这是实测结论不是设计偏好：
#
# 1) 拉丁词通道（一个正则 + 一个「像不像专名」判据）。**这是本库的主力通道。**
#    一开始只用「驼峰 + 全大写」两条正则，结果漏掉了规范自己拿来当示例的对象：
#    Milvus / Qdrant / Pinecone / Zvec / Dify 都不带内部大写，RAGFlow / ChatBI / MinerU
#    是「大写块 + 词」结构——两条正则都抓不到。改成扫所有拉丁词、再用大小写信号筛：
#      全大写 → 可能是指标缩写（交给黑名单）
#      含 ≥2 个大写字母 → RAGFlow / ChatBI / PaddleOCR / DeepSeek
#      首字母大写且 ≥3 个字母 → Milvus / Zvec / Dify
#    实测覆盖 30 个已知对象全部命中（含 Milvus/Qdrant/Zvec/RAGFlow/MinerU）。
#    代价是候选总量变大（702 个），所以必须配 --min-sources 与黑名单一起用。
#
# 2) 中文机构后缀（科技/集团/大学/研究院）—— 精度高但命中极少（全库 6 个）。
#
# 3) 中文产品后缀（平台/系统/助手）—— **已弃用**。实测这条在本库几乎全是误报：
#    抓出来的 7 条候选是「通过大模型」「然后大模型」「基于大模型」「利用大模型」
#    「客服系统」「问答助手」——动词短语与泛称，没有一个是具名对象。
#
# 中文真正的产品名多数**不带任何后缀**（豆包、元宝、飞书、通义、智谱、Kimi），
# 正则抓不到，靠 SEED_CN 兜底 + 人工扫标题与表格。
# 代价必须说清：**SEED_CN 是有限的，它命中不了的长尾仍需人工发现。**

LATIN_RE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*(?:[-._][A-Za-z0-9]+)*)(?![A-Za-z0-9])')
CN_ORG_RE = re.compile(r'(?<![\u4e00-\u9fa5])([\u4e00-\u9fa5]{2,5}'
                       r'(?:科技|集团|大学|研究院|实验室|事业部))')
# 编号码：A001 / TOP10 / H100 / FP32 / B1B2B3。两位以上数字的字母数字混排，
# 基本都是测试数据编号或硬件规格，不是对象名。
CODE_RE = re.compile(r'(?:[A-Za-z]{1,4}\d{1,})+$')

VOWELS = 'aeiouAEIOU'


def looks_like_name(tok):
    """判断一个拉丁词像不像专名。这是控噪的关键，所以判据写死在这里、可单独测。"""
    letters = [c for c in tok if c.isalpha()]
    if len(letters) < 2:
        return False
    if tok.isupper():
        # 无元音的短全大写是缩写（MD / TTS），有元音的是产品或算法名（FAISS / BGE）
        return True
    ups = sum(1 for c in letters if c.isupper())
    if ups >= 2:
        return True                                   # RAGFlow / ChatBI / PaddleOCR
    if ups == 1 and letters[0].isupper() and len(letters) >= 3:
        return True                                   # Milvus / Zvec / Dify
    return False


# 中文专名兜底词表。只收**本项目语料里实测出现过的**名称（每个都核过频次），
# 不写"可能有用"的空名。命名依据见 SKILL.md「已知限制」：中文无后缀专名
# 无法用正则发现，这份表是有限的补偿，不是穷举。
SEED_CN = [
    # AI 产品 / 大模型
    '豆包', '元宝', '通义千问', '通义听悟', '文心', '智谱', '混元', '星火',
    '扣子', '飞书妙记', '腾讯文档',
    # 协作 / 办公 / 平台
    '飞书', '钉钉', '企业微信', '语雀', '石墨', '有赞',
    # 机构 / 厂商
    '阿里', '百度', '腾讯', '字节', '华为', '蚂蚁', '讯飞', '平安', '京东',
    '智源', '遥望', '用友', '金蝶', '顺丰', '贝壳',
    # 消费端（多为商业模式举例，扫出来由人判断要不要建页）
    '微信', '支付宝', '抖音', '小红书', '美团',
]

# 概念/缩写黑名单：这些属于 concept 层，不是可替换的具体实现（02 §8.1）。
# 扩充依据是实测扫出来的噪声：指标、架构、方法、测试题编号、无意义的短词。
SCAN_STOP = set("""
AI API APP PPT PDF WORD EXCEL PRD LLM MCP RAG OCR ASR TTS STT NLP NLU NER QA FAQ POS RE
SQL JSON YAML TOML XML HTML CSS UI UX DSL CRUD IDE SDK CLI GUI HTTP HTTPS URL URI FTP
TCP UDP CDN DNS SSO LDAP GID TOKEN VIP VPN PC MAC MD BI ES CC DP MS VC PE IPO KPI
ROI SKU SOP OKR JD TOB TOC SAAS PAAS IAAS B2B B2C C2C O2O P2P UGC PGC OGC AIGC AGI
CRM ERP OA WMS MES BPM ITSM CMDB DAM MDM ETL OLAP OLTP RBAC IDX CASE BADCASE
ID IT HR BD CEO CTO CPO CFO COO PM CM IP CPU GPU TPU NPU IC OS DB IO
BM25 MMR HNSW GSB COT AB ABC AAA XYZ TOP TOPK NPS CSAT SLA KOL KOC SEO SEM CPM CPC CPA ARPU
IAA IAP IOT SFT RLHF DPO GRAPHRAG REACT CLIP BERT LSTM RNN CNN GAN VAE GPT
QPS TPS RPS GPS AGENT AIAGENT MULTI MULTIAGENT WORKFLOW PIPELINE PLANNING
MVP MMP DAU MAU GMV PV UV AARRR ATM BTC MOS PROM ERROR WARN FAIL INFO DEBUG
LOG LOSS EPOCH BATCH SIZE TEMP MAX MIN AVG SUM PK FK VS BY XX XXX YY ZZ
OK NO YES TRUE FALSE NULL NONE SAME NEW OLD POC SDK DEMO
""".split())

# 中文语料里高频出现、但属于通用词的拉丁词。命中即丢——
# 它们是「英文术语」或「句首词」，不是具名对象。
COMMON_EN = set("""
Token Prompt Chunk Embedding Rerank Recall Precision Score Agent Vector Index Query Document
Chunking Fusion Router Cache Batch Stream Server Client Model Dataset Label Table Row Column
Field Filter Rank Search Retrieval Generation Context Window Session Memory Tool Skill Harness
Workflow Pipeline Framework Platform Stage Layer Schema Node Edge Graph Cluster Cost Price
Revenue Profit Growth Market User Customer Order Invoice Report Dashboard Step Phase Level
Item List Map Set Key Value Type Name Path File Data Text Image Audio Video Transformer
Multi Few Self Deep Chain Input Action Tools Skills Human Thinking Answer Relevance Pro Hub
Wifi Function Calling Metadata Confidence Reranker Reflection Intent Slot Judge Demo Research
Engineering Planner Executor Thought Observation Python Git Java Go Rust Chrome Safari Edge
Web Markdown Bad Good Nice Help Note Doc Docs Example Print Output Input Title Body Head
Left Right Open Close Start Stop End
Fix Bug Debug Log Logs Test Tests Code Coding Write Read Run Runs Make Made Get Set Put Add
First Second Third Fourth Fifth Last Next Prev Previous Total Count Number
""".split())

# 看起来像缩写（全大写 ≤5 位）但不是黑名单的，标为低置信：
# 让使用者一眼分出「LangChain 这种一眼是对象」和「BGE 这种要核一下」。
ABBR_RE = re.compile(r'^[A-Z0-9]{2,5}(?:[-.]\d+)?$')

OK, WARN, FAIL = 'OK', 'WARN', 'FAIL'


# ---------- 基础 ----------

def read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def norm(s):
    """去掉所有空白，用于容忍折行与空格差异，但不放过字符增删。"""
    return re.sub(r'\s+', '', s or '')


def norm_key(s):
    """符号表判重用的归一化：小写 + 去掉分隔符与空白。"""
    return re.sub(r'[\s·・\-_/（）()\[\]]+', '', (s or '')).lower()


def split_paras(text):
    """段 = 非空行，段号从 1 开始。"""
    return [ln.strip() for ln in text.split('\n') if ln.strip()]


def norm_title(s):
    """标题归一化：折行空格压成一个、去掉 Markdown 装饰、小写。"""
    s = re.sub(r'\s+', ' ', (s or '').strip())
    return s.strip('*_`# ').strip().lower()


def header_lines(text):
    """原文所有 Markdown 标题行：[(层级, 标题文本, 行号)]。"""
    out = []
    for i, line in enumerate(text.split('\n')):
        m = re.match(r'^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$', line)
        if m:
            out.append((len(m.group(1)), m.group(2).strip(), i))
    return out


def title_hit(text, head):
    """§标题路径里的一段能否在这份原文中找到，返回标题行号；找不到返回 None。

    必须先找**完全同名**的标题，找不到才退一步找「包含它的」标题。只按包含找会假通过：
    「§CLAUDE.md」会先撞上文中更早的「### Then: Memory in CLAUDE.md files」，
    「§References」会撞上「### Now: Rich references」——位置看着对，其实指到了另一节。
    """
    nh = norm_title(head)
    hs = header_lines(text)
    for lv, t, i in hs:
        if nh == norm_title(t):
            return i
    for lv, t, i in hs:
        if nh in norm_title(t):
            return i
    return None


def unquote(v):
    v = (v or '').strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def parse_list_value(v):
    """解析 YAML 行内列表：[a, b] → ['a','b']"""
    v = (v or '').strip()
    if v.startswith('[') and v.endswith(']'):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [unquote(x.strip()) for x in inner.split(',') if x.strip()]
    return [unquote(v)] if v else []


def parse_frontmatter(text):
    """返回 (字段字典, 正文)。列表字段返回 list，标量返回 str。

    比 01 的实现多一步：支持块状列表（`- item` 换行写法）。
    """
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.S)
    if not m:
        return None, text
    fm = {}
    lines = m.group(1).split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith('#') or ':' not in line:
            i += 1
            continue
        k, v = line.split(':', 1)
        k, v = k.strip(), v.strip()
        if v == '':
            items, j = [], i + 1
            while j < len(lines) and re.match(r'^\s*-\s+', lines[j]):
                items.append(unquote(re.sub(r'^\s*-\s+', '', lines[j]).strip()))
                j += 1
            if items:
                fm[k] = items
                i = j
                continue
        if v.startswith('['):
            fm[k] = parse_list_value(v)
        else:
            fm[k] = unquote(v)
        i += 1
    return fm, text[m.end():]


def fm_is_empty(v):
    if v is None:
        return True
    if isinstance(v, list):
        return len(v) == 0
    return str(v).strip() == ''


def split_sections(body):
    out, cur, buf = [], None, []
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


def count_table_rows(sec):
    """数表格数据行（去掉表头与分隔行）。"""
    rows = []
    for ln in (sec or '').split('\n'):
        ln = ln.strip()
        if ln.startswith('|') and not re.match(r'^\|[\s:\-|]+\|$', ln):
            rows.append(ln)
    return max(0, len(rows) - 1) if rows else 0


# ---------- 符号表：读 entities/ ----------

def load_registry(entities_dir='wiki/entities'):
    """读 wiki/entities/*.md，返回条目列表。"""
    entries = []
    if not os.path.isdir(entities_dir):
        return entries
    for path in sorted(glob.glob(os.path.join(entities_dir, '*.md'))):
        text = read(path)
        fm, body = parse_frontmatter(text)
        if fm is None:
            entries.append({'file': path, 'title': os.path.splitext(os.path.basename(path))[0],
                            'aliases': [], 'lifecycle': '', 'fm_ok': False})
            continue
        aliases = fm.get('aliases') if isinstance(fm.get('aliases'), list) else []
        entries.append({
            'file': path,
            'title': str(fm.get('title') or '').strip(),
            'aliases': [a for a in aliases if a],
            'lifecycle': str(fm.get('lifecycle') or '').strip(),
            'corroboration': str(fm.get('corroboration') or '').strip(),
            'entity_type': str(fm.get('entity_type') or '').strip(),
            'fm_ok': True,
        })
    return entries


def registry_index(entries):
    """归一化名 → [(file, kind, raw)]。"""
    idx = defaultdict(list)
    for e in entries:
        if e['title']:
            idx[norm_key(e['title'])].append((e['file'], 'title', e['title']))
        for a in e.get('aliases', []):
            idx[norm_key(a)].append((e['file'], 'alias', a))
    return idx


# ---------- 模式：--registry ----------

def cmd_registry(check, entities_dir):
    entries = load_registry(entities_dir)
    idx = registry_index(entries)

    print('== 符号表 ==')
    print('位置：%s/' % entities_dir)
    print('条目：%d 页' % len(entries))
    print()

    if not entries:
        print('符号表为空。')
        print()
        print('这在当前基线里是真实状态：素材里有 30 个跨 ≥2 份素材的技术工具，')
        print('但 wiki/entities/ 一页都没有——不是「以后会建」，是没有任何流程会走到它。')
        print()
        print('下一步：用 --scan-all 从素材反扫对象，得到待建页清单。')
        return 0

    print('── 条目 ──')
    print('%-30s %-8s %-11s %-6s %s' % ('title', 'type', 'lifecycle', '来源', 'aliases'))
    print('-' * 92)
    for e in entries:
        if not e['fm_ok']:
            print('%-30s !! frontmatter 解析失败' % e['title'])
            continue
        n_src = len(re.findall(r'\S+\.md', e.get('file', '')))  # 占位，见下行真实统计
        print('%-30s %-8s %-11s %-6s %s' % (
            e['title'][:30], e['entity_type'][:8], e['lifecycle'][:11],
            e.get('corroboration', '') or '-',
            ', '.join(e['aliases'][:4]) + ('…' if len(e['aliases']) > 4 else '')))

    fails, warns = [], []

    # 冲突：同一个归一化名落到多个文件
    print()
    print('── 去重检查（符号表第一职责）──')
    conflicts = {k: v for k, v in idx.items() if len({f for f, _, _ in v}) > 1}
    if conflicts:
        for k, hits in sorted(conflicts.items()):
            detail = '；'.join('%s（%s：「%s」）' % (os.path.basename(f), kind, raw)
                              for f, kind, raw in hits)
            fails.append('归一化名「%s」被 %d 页同时占用：%s'
                         % (k, len({f for f, _, _ in hits}), detail))
            print('FAIL  %s' % fails[-1])
    else:
        print('OK    无重名/别名冲突')

    # 别名与自身 title 重复。
    # ⚠️ 只报「逐字相同」的冗余别名：ASCII 名称的大小写变体（LangChain → langchain）、
    # 带空格的转写写法（Lang chain）都是规范明确要求记进 aliases 的，不该被当成重复。
    for e in entries:
        if not e['fm_ok']:
            continue
        dup = sorted({a for a in e['aliases'] if a and a == e['title']})
        if dup:
            warns.append('%s：aliases 与 title 逐字重复（%s）' % (e['title'], '、'.join(dup)))
            print('WARN  %s' % warns[-1])

    # 缺别名
    no_alias = [e['title'] for e in entries if e['fm_ok'] and not e['aliases']]
    if no_alias:
        print('WARN  %d 页 aliases 为空：%s' % (len(no_alias), '、'.join(no_alias[:6])))
        print('      空别名不是错——但素材里若出现别的叫法（大小写、简称），必须记进去。')

    # --check：名称解析
    if check:
        print()
        print('── 名称解析测试：「%s」──' % check)
        k = norm_key(check)
        hits = idx.get(k, [])
        if hits:
            for f, kind, raw in hits:
                print('OK    命中 %s（%s：「%s」）' % (f, kind, raw))
                print('      → 全库唯一，可直接引用')
            if len({f for f, _, _ in hits}) > 1:
                print('FAIL  该名称落到多页——违反「全库只有一页承接它」')
        else:
            print('MISS  未命中任何条目。')
            near = [e['title'] for e in entries
                    if e['fm_ok'] and (k in norm_key(e['title']) or norm_key(e['title']) in k)]
            if near:
                print('      近似条目（检查是否该合并进别名）：%s' % '、'.join(near[:6]))
            else:
                print('      → 若确认是具名对象，应新建一页（单源也建）。')

        # 大小写不敏感的文件系统上，新建「仅大小写不同」的文件名会静默覆盖已有页
        want = check.strip() + '.md'
        for e in entries:
            bn = os.path.basename(e['file'])
            if bn.lower() == want.lower() and bn != want:
                msg = ('「%s」与已有文件 %s 仅大小写不同。本机文件系统大小写不敏感，'
                       '按此名新建会**直接覆盖**该页（不报错）。请合并进已有页'
                       % (check.strip(), bn))
                fails.append(msg)
                print('FAIL  %s' % msg)

    print()
    print('── 汇总 ──')
    print('失败 %d / 警告 %d' % (len(fails), len(warns)))
    return 1 if fails else 0


# ---------- 模式：--scan ----------

COMMON_EN_L = {w.lower() for w in COMMON_EN}


def _better_display(a, b):
    """同一对象的两种写法，留大写字母多的那个：gpt4→GPT4、Deepseek→DeepSeek。"""
    ua = sum(1 for c in a if c.isupper())
    ub = sum(1 for c in b if c.isupper())
    return a if ua >= ub else b


def scan_text(text):
    """从一段文本里抽候选对象名，返回 {归一化名: (显示名, 类型, 置信)}。

    置信分两档：一眼是对象的（LangChain、PaddleOCR）为高置信；
    像缩写的（BGE、LoRA）为低置信，需要人工核一眼再决定。
    """
    found = {}

    def put(key, name, typ, conf):
        prev = found.get(key)
        if prev is None:
            found[key] = (name, typ, conf)
            return
        name2 = _better_display(prev[0], name) if name != prev[0] else prev[0]
        conf2 = '高置信' if '高置信' in (prev[2], conf) else '低置信'
        found[key] = (name2, prev[1], conf2)

    for m in LATIN_RE.findall(text):
        if len(m) < 2 or not looks_like_name(m):
            continue
        head = re.split(r'[-._]', m)[0]
        alpha = re.match(r'^[A-Za-z]+', head)
        alpha = alpha.group(0).upper() if alpha else ''
        # 整词、词头、或词头的字母部分命中黑名单都算概念/指标（TOP3 / QPS / ABC3）
        if (m.upper() in SCAN_STOP or alpha in SCAN_STOP
                or m.lower() in COMMON_EN_L or head.lower() in COMMON_EN_L):
            continue
        if CODE_RE.match(m) and sum(c.isdigit() for c in m) >= 2:
            continue
        put(norm_key(m), m, '技术/产品', '低置信' if ABBR_RE.match(m) else '高置信')

    for m in CN_ORG_RE.findall(text):
        if m in CONCEPT_NAMES or m in GENERIC_NAMES:
            continue
        put(norm_key(m), m, '组织', '高置信')

    for m in SEED_CN:
        if m in text:
            put(norm_key(m), m, '中文专名', '高置信')

    return found


def cmd_scan(raw_paths, registered_idx, limit, single, min_files):
    files_of, occ, display, types, confs = {}, {}, {}, {}, {}
    for p in raw_paths:
        text = read(p)
        for k, (name, t, conf) in scan_text(text).items():
            files_of.setdefault(k, set()).add(p)
            occ[k] = occ.get(k, 0) + len(re.findall(re.escape(name), text))
            display.setdefault(k, name)
            types.setdefault(k, t)
            confs.setdefault(k, conf)

    rows = []
    for k, files in files_of.items():
        reg = registered_idx.get(norm_key(display[k]), [])
        rows.append((k, display[k], types[k], confs[k], len(files),
                     occ.get(k, 0), sorted(os.path.basename(f) for f in files), reg))

    rows.sort(key=lambda r: (0 if not r[7] else 1, -r[4], -r[5], r[1]))

    shown = [r for r in rows if r[4] >= min_files]
    hidden = len(rows) - len(shown)
    n_reg = sum(1 for r in shown if r[7])

    print('== 素材对象反扫 ==')
    if single:
        print('素材：%s' % raw_paths[0])
    else:
        print('素材：%d 份（%s）' % (len(raw_paths), os.path.commonpath(raw_paths).rstrip('/') + '/'))
    print('候选对象：%d 个（其中已登记 %d，待建页 %d）'
          % (len(shown), n_reg, len(shown) - n_reg))
    if hidden:
        print('另有 %d 个只出现在 %d 份以下素材，未列出（--min-sources 1 可看全量）'
              % (hidden, min_files))
    print()

    print('%-22s %-11s %-6s %-5s %-5s %-8s %s'
          % ('名称', '类型', '置信', '份数', '次数', '状态', '首现素材'))
    print('-' * 104)
    for k, name, t, conf, nf, no, hits, reg in shown[:limit]:
        status = '已登记' if reg else '待建页'
        if reg and len({f for f, _, _ in reg}) > 1:
            status = '冲突!'
        print('%-22s %-11s %-6s %-5d %-5d %-8s %s' % (
            name[:22], t[:11], conf, nf, no, status, hits[0][:42]))

    if len(shown) > limit:
        print()
        print('… 另有 %d 个，用 --limit 调整显示上限。' % (len(shown) - limit))

    print()
    print('⚠️  必做的两步（不做会建出 face.md 那种页面）：')
    print('   1. 回原文核对对象名。ASR 会把工具名转错，真实案例：')
    print('      「就这几家，face、MySQL、Elasticsearch」——face 实为 FAISS，MySQL 实为 Milvus。')
    print('      用 --context "名字" 看它在原文里长什么样。')
    print('   2. 逐个查重：--registry --check "名字"。命中即合并，并把别的叫法记进 aliases。')
    print()
    print('提醒：候选 ≠ 该建页。泛称、概念（RAG/MCP）、机构举例（抖音/美团这类）都要按')
    print('      02 §8.1 判断后再决定。这份清单的作用是「防止漏掉长尾对象」，不是待办列表。')
    return 0


# ---------- 模式：--context ----------

def cmd_context(name, raw_paths, width):
    print('== 上下文核对：「%s」==' % name)
    # ASCII 名要卡词边界，否则 --context "face" 会把 interface / surface 一起match 出来
    pat = (re.compile(r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])')
           if name.isascii() else re.compile(re.escape(name)))
    total, files = 0, 0
    for p in raw_paths:
        text = read(p)
        idxs = [m.start() for m in pat.finditer(text)]
        if not idxs:
            continue
        files += 1
        total += len(idxs)
        print()
        print('── %s（%d 处）' % (p, len(idxs)))
        for i in idxs[:3]:
            s, e = max(0, i - width), min(len(text), i + len(name) + width)
            print('   …%s…' % text[s:e].replace('\n', '⏎'))
        if len(idxs) > 3:
            print('   …（另有 %d 处）' % (len(idxs) - 3))
    print()
    if total == 0:
        print('原文中找不到「%s」。' % name)
        print('若这是在页面上看到的对象名，很可能是 ASR 转写错误——'
              '回原文找发音相近的真实名字。')
        return 2
    print('合计 %d 处，分布在 %d 份素材。' % (total, files))
    print('核对要点：这个写法在原文里到底指哪个对象？是产品本名，还是转写噪声？')
    return 0


# ---------- 模式：--page ----------

def find_words(text, words):
    """找词并给出上下文，重叠的只报一次（「非常好用」命中时不再重复报「好用」）。"""
    spans = []
    for w in words:
        for m in re.finditer(re.escape(w), text):
            spans.append((m.start(), m.end(), w))
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    accepted, out = [], []
    for s, e, w in spans:
        if any(s < ae and as_ < e for as_, ae, _ in accepted):
            continue
        accepted.append((s, e, w))
        out.append((w, text[max(0, s - 18): e + 18].replace('\n', ' ')))
    return out


def resolve_raws(fm, sources_root):
    """顺着 entity 页的 sources → source 页 → source_path → raw，拿到可核对的原文。"""
    out = []
    srcs = fm.get('sources')
    if not isinstance(srcs, list):
        srcs = parse_list_value(str(srcs or ''))
    for s in srcs:
        s = (s or '').strip()
        if not s:
            continue
        base = os.path.basename(s)
        cands = [s, os.path.join(sources_root, base), os.path.join('wiki', s)]
        page = next((c for c in cands if os.path.exists(c)), None)
        if not page:
            out.append((s, None, None, 'source 页不存在'))
            continue
        sfm, _ = parse_frontmatter(read(page))
        rp = (sfm or {}).get('source_path', '')
        if not rp or not os.path.exists(rp):
            out.append((s, page, None, 'source 页未登记可读的 source_path'))
            continue
        out.append((s, page, rp, None))
    return out


def cmd_page(page_path, sources_root, wiki_root):
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

    prose = FENCE_RE.sub('', body)   # 去掉示例/模板代码块后的正文

    # ---- 1. 字段 ----
    for k in REQUIRED_FM:
        if k not in fm:
            add(FAIL, 'frontmatter', '缺少必填字段：%s' % k)
        elif fm_is_empty(fm[k]) and k not in ('aliases',):
            add(FAIL, 'frontmatter', '字段为空：%s' % k)
    for k in DRIFT_FM:
        if k in fm:
            add(WARN, 'frontmatter', '存在本类型不该有的字段：%s（source 页字段或已废弃字段）' % k)
    if fm.get('type') and fm['type'] != 'entity':
        add(FAIL, 'frontmatter', 'type 应为 entity，实际为 %s' % fm['type'])
    et = str(fm.get('entity_type') or '')
    if et and et not in ENTITY_TYPE_ENUM:
        add(WARN, 'frontmatter', 'entity_type 取值不在枚举内：%s（应为 %s）'
            % (et, '/'.join(ENTITY_TYPE_ENUM)))
    lc = str(fm.get('lifecycle') or '').lower()
    if lc and lc not in LIFECYCLE_ENUM:
        add(FAIL, 'frontmatter', 'lifecycle 取值不在枚举内：%s（应为 %s）。'
            '这是 entity 区别于 concept 的唯一硬边界，不能自由发挥'
            % (fm.get('lifecycle'), '/'.join(LIFECYCLE_ENUM)))
    cb = str(fm.get('corroboration') or '')
    if cb and cb not in CORROBORATION_ENUM:
        add(WARN, 'frontmatter', 'corroboration 取值不在枚举内：%s（应为 %s）'
            % (cb, '/'.join(CORROBORATION_ENUM)))

    title = str(fm.get('title') or '').strip()

    # 文件名 = 通用名
    stem = os.path.splitext(os.path.basename(page_path))[0]
    if title and norm_key(stem) != norm_key(title):
        add(WARN, 'frontmatter', '文件名与 title 不一致：「%s」vs「%s」。命名依据应是对象的通用名'
            % (stem, title))

    # H1 与 title 一致
    h1 = re.search(r'^#\s+(.+)$', body, re.M)
    if h1 and title and norm(h1.group(1)) != norm(title):
        add(WARN, 'frontmatter', 'title 与正文 H1 不一致：「%s」vs「%s」'
            % (title, h1.group(1).strip()))
    elif not h1:
        add(WARN, 'frontmatter', '正文缺少 H1 标题')

    # 一句话定位
    loc_line = None
    for ln in body.split('\n'):
        s = ln.strip()
        if s.startswith('>'):
            loc_line = s.lstrip('>').strip()
            break
    if not loc_line:
        add(WARN, '结构', 'H1 下缺少「一句话定位」引用块（`> {类型/出品方} + {干什么用}`）')
    elif len(loc_line) > 46:
        add(WARN, '结构', '一句话定位偏长（%d 字，规范要求 ≤40）：%s'
            % (len(loc_line), loc_line[:40]))

    # ---- 2. 泛称 / 概念误判 ----
    tkey = norm_key(title)
    if any(title == g for g in GENERIC_NAMES):
        add(FAIL, '定位', '「%s」是泛称不是具名对象，不该建 entity 页（02 §1）。'
            '泛称属于 concept 层' % title)
    if title.lower() in CONCEPT_NAMES or tkey in {norm_key(c) for c in CONCEPT_NAMES}:
        add(FAIL, '定位', '「%s」是概念/原理，不是可替换的具体实现（02 §8.1）。应建 concept 页'
            % title)
    if title.lower() in CONTROVERSIAL:
        add(WARN, '定位', '「%s」属于规范明确标注「需单独判断」的类型（见 02 §8.1）。'
            '本规范暂按 concept 处理，若有分歧写入 review.md，不要擅自选定' % title)

    # ---- 3. 骨架 ----
    sec_titles = [t for t, _ in split_sections(body)]
    missing = []
    for kw in REQUIRED_SECTIONS:
        if find_section(body, kw)[0] is None:
            missing.append(kw)
    for kw in missing:
        near = [t for t in sec_titles if kw[:2] in t]
        extra = ('（发现近似章节「%s」，但标题须含「%s」）' % (near[0], kw)) if near else ''
        add(FAIL, '结构', '缺少章节：%s%s' % (SECTION_LABEL.get(kw, kw), extra))

    # ---- 4. 逐节内容 ----
    if find_section(body, '关键事实')[0]:
        _, sec = find_section(body, '关键事实')
        if count_table_rows(sec) == 0:
            add(WARN, '关键事实', '表格没有数据行')
        for ln in sec.split('\n'):
            ln = ln.strip()
            if not ln.startswith('|'):
                continue
            cells = [c.strip() for c in ln.strip('|').split('|')]
            if len(cells) < 3:
                add(WARN, '关键事实', '表格行不足三列（属性|值|来源）：%s' % ln[:40])
                continue
            src = cells[2] if len(cells) >= 3 else ''
            if not src or src in ('—', '-', '/', '无'):
                add(WARN, '关键事实', '「%s」缺少来源' % cells[0][:20])
            elif any(w in src for w in SPECULATION_WORDS):
                add(WARN, '关键事实', '来源像猜测而非素材位置：「%s」→「%s」' % (cells[0][:14], src[:20]))

    if find_section(body, '素材中的定位')[0]:
        _, sec = find_section(body, '素材中的定位')
        items = [ln.strip() for ln in sec.split('\n')
                 if re.match(r'^\s*[-*]\s+\S', ln)]
        if not items:
            add(FAIL, '素材中的定位', '本节为空——它是本页核心，素材里最富的就是选型表/推荐表')
        for it in items:
            has_pos = bool(POS_RE.search(it))
            has_inf = 'INFERRED' in it
            if not has_pos and not has_inf:
                add(WARN, '素材中的定位', '条目没有位置标注也没有 INFERRED 标记：%s' % it[:36])
            if has_inf and not re.search(r'由|推得|推出', it):
                add(WARN, '素材中的定位', 'INFERRED 未说明推理依据（应写「INFERRED——由…推得」）：%s' % it[:36])

    if find_section(body, '出现记录')[0]:
        _, sec = find_section(body, '出现记录')
        rows = count_table_rows(sec)
        if rows == 0:
            add(WARN, '出现记录', '表格没有数据行（单源也要写一行）')
        if cb == '单源' and rows > 1:
            add(FAIL, '出现记录', 'corroboration 写「单源」但有 %d 行记录，自相矛盾' % rows)
        if cb == '多源' and rows < 2:
            add(FAIL, '出现记录', 'corroboration 写「多源」但只有 %d 行记录，自相矛盾' % rows)
        if cb == '单源' and '单源' not in sec:
            add(WARN, '出现记录', '单源页面应在正文点明「单源，其余素材未提及」')

    if find_section(body, '生命周期')[0]:
        _, sec = find_section(body, '生命周期')
        s = norm(sec)
        if not s:
            add(FAIL, '生命周期', '本节为空。它是必答项——未知也要写「未知」')
        elif any(w in sec for w in PLACEHOLDER_WORDS):
            add(FAIL, '生命周期', '本节用了占位语（%s）。符号表条目要明说状态与依据，不能糊过去'
                % '、'.join(w for w in PLACEHOLDER_WORDS if w in sec))
        elif not re.search(r'当前状态|状态[:：]', sec):
            add(WARN, '生命周期', '没有「当前状态」一项（规范要求：当前状态 / 依据 / 最近变动）')

    if find_section(body, '相关')[0]:
        _, sec = find_section(body, '相关')
        links = re.findall(r'\[\[([^\]]+)\]\]', sec)
        if not links:
            add(FAIL, '相关', '没有任何 wikilink。entity 是被引用方，没有出链它进不了导航')
        elif len(links) < 2:
            add(WARN, '相关', '只有 %d 个 wikilink（规范建议 3–8 个）' % len(links))
        # 判断「待创建」必须看**整行**——链接本身当然不含这三个字
        pending = [ln for ln in sec.split('\n') if '待创建' in ln]
        for l in links:
            target = re.sub(r'^待创建[:：]\s*', '', l).strip()
            if not target:
                continue
            if any('[[' + l + ']]' in ln for ln in pending):
                continue
            found = (glob.glob(os.path.join(wiki_root, '**', target + '.md'), recursive=True)
                     or glob.glob(os.path.join(wiki_root, '**', '*' + target + '*.md'),
                                  recursive=True))
            if not found:
                add(WARN, '相关', '链接目标不存在且未标「[待创建: [[%s]]]」' % target)

    # ---- 5. 无源评价 / 猜测 ----
    # 〔〕 里装的是位置标注，段首短名本身就是素材原话（如「最好的开源知识库，没…」），
    # 那不是在替页面下评价。评价词只查去掉位置标注之后的正文。
    prose_nopos = POS_STRIP_RE.sub(' ', POS_RE.sub(' ', prose))
    for w, ctx in find_words(prose_nopos, EVAL_WORDS):
        add(WARN, '评价', '出现评价词「%s」：…%s…（entity 只登记，评价进 wiki/notes/）' % (w, ctx))
    for w, ctx in find_words(prose_nopos, SPECULATION_WORDS):
        add(WARN, '推测', '出现推测词「%s」：…%s…' % (w, ctx))

    # ---- 6. 素材外标记的隔离 ----
    out_marks = [p for p in POS_RE.findall(prose) if '素材外' in p]
    if out_marks and '⚠️' not in prose:
        add(WARN, '素材外', '用了 %d 处 〔素材外〕 但没有加 ⚠️ 免责行。'
            '规范要求隔离标记 + 限定为可核验硬事实 + 明确声明素材未提供' % len(out_marks))

    # ---- 7. 别名：素材里有大小写变体却没记 ----
    raws = resolve_raws(fm, sources_root)
    raw_ok = [(s, page, rp) for s, page, rp, err in raws if rp]
    raw_texts = [(s, read(rp)) for s, page, rp in raw_ok]
    raw_file = {s: rp for s, page, rp in raw_ok}
    if title and title.isascii() and isinstance(fm.get('aliases'), list) and not fm['aliases']:
        joined = '\n'.join(t for _, t in raw_texts)
        variants = {m for m in re.findall(r'(?<![A-Za-z0-9])' + re.escape(title) + r'(?![A-Za-z0-9])',
                                          joined, re.I)}
        if len(variants) > 1:
            add(WARN, 'aliases', '素材里出现大小写变体 %s，建议记进 aliases（否则可能并存两页）'
                % '、'.join(sorted(variants)))

    # ---- 8. 位置标注机检 ----
    pos_stats = {'ok': 0, 'fail': 0, 'warn': 0, 'out': len(out_marks)}
    merged = '\n'.join(t for _, t in raw_texts)
    if not raws:
        add(WARN, 'sources', 'sources 为空，本页的位置标注无法回溯到原文')
    else:
        for s, page, rp, err in raws:
            if err:
                add(WARN, 'sources', '「%s」%s' % (s, err))

    if raw_texts:
        para_cache = {}
        for s, t in raw_texts:
            para_cache[s] = split_paras(t)
        all_paras = []
        for s, t in raw_texts:
            all_paras.extend(para_cache[s])

        # 位置既可能写在 〔〕 里（素材中的定位），也可能裸写
        # （关键事实的来源列、出现记录的位置列），两种都要核。
        bare = re.sub(r'〔[^〕]*〕', ' ', prose)
        # 既支持编号章节号（§二.2），也支持英文标题路径（§Now: Design interfaces，见 01 §5.3）；
        # 同一单元格里用「、」并列多个位置时要拆开，否则整串当一个位置核不上。
        bare_secs = re.findall(r'§\s*[^〕\n、|，,；;]+', bare)
        pos_list = POS_RE.findall(prose) + bare_secs

        for pos in pos_list:
            p = pos.strip()
            if '素材外' in p:
                continue
            if 'INFERRED' in p or '推定' in p:
                continue
            hm = SRC_HINT_POS_RE.match(p)
            if hm:
                hint = hm.group(1)
                n = int(hm.group(2))
                head = (hm.group(3) or '').rstrip('….· ').strip()
            else:
                hint = ''
                m = PARA_POS_RE.search(p)
                if m:
                    n = int(m.group(1))
                    head = (m.group(2) or '').rstrip('….· ').strip()
                else:
                    n, head = None, ''
            if n is not None:
                # 候选段落表：带来源短名就只查那一份；否则先按「该段段首命中」猜是哪一份，
                # 猜不中才退回合并表（旧行为——多源页面几乎必错，只作兜底）
                if hint:
                    cands = [(s, para_cache[s]) for s, _ in raw_texts
                             if hint in os.path.splitext(os.path.basename(raw_file.get(s, '')))[0]
                             or hint in os.path.splitext(os.path.basename(s))[0]]
                else:
                    cands = []
                if not cands:
                    hits = [(s, para_cache[s]) for s, _ in raw_texts
                            if head and 1 <= n <= len(para_cache[s])
                            and norm(para_cache[s][n - 1]).startswith(norm(head))]
                    cands = hits if len(hits) == 1 else [(None, all_paras)]

                seg = None
                for _, plist in cands:
                    if 1 <= n <= len(plist) and (not head or norm(plist[n - 1]).startswith(norm(head))):
                        seg = plist[n - 1]
                        break
                if seg is not None:
                    pos_stats['ok'] += 1
                elif 1 <= n <= len(cands[0][1]):
                    pos_stats['warn'] += 1
                    add(WARN, '位置标注', '段首与原文不符：写「%s」，该段实际段首「%s」'
                        % (head[:12], cands[0][1][n - 1][:12]))
                else:
                    pos_stats['fail'] += 1
                    add(FAIL, '位置标注', '段号越界：第 %d 段（可读原文共 %d 段）' % (n, len(all_paras)))
                continue
            sm = SEC_POS_RE.search(p)
            if sm:
                cn, num = sm.group(1), sm.group(2)
                hit = False
                for _, t in raw_texts:
                    # 中文章号各家写法不一：`## 二、…` 与 `## 第三章　…` 都要认
                    if cn and not re.search(
                            r'^#{1,4}\s*(?:第)?' + re.escape(cn) + r'(?:章|部分|节)?\s*[、.，,:：\s]',
                            t, re.M):
                        continue
                    if re.search(r'^#{1,4}[^\n]*?' + re.escape(num), t, re.M):
                        hit = True
                        break
                if hit:
                    pos_stats['ok'] += 1
                else:
                    pos_stats['warn'] += 1
                    add(WARN, '位置标注', '章节号 %s 在可读原文里找不到对应标题，请人工确认' % p[:20])
                continue
            ts = TS_RE.search(p)
            if ts:
                if TS_RE.search(merged):
                    pos_stats['ok'] += 1
                else:
                    pos_stats['fail'] += 1
                    add(FAIL, '位置标注', '时间戳 %s 在原文中不存在' % ts.group(0))
                continue
            # §标题路径 / §开篇（无编号章节的材料，见 01 §5.3）
            pm = SRC_HINT_SEC_RE.match(p) or SEC_PATH_RE.match(p)
            if pm:
                sec = (pm.group('sec') or '').strip()
                # 一个标注里可能并列多个位置（「§A、§B」），逐个核，全中才算通过；
                # 每个位置可能带补充说明（「§标题 · 图注」「§标题（说明）」），只核标题部分。
                parts = [x.strip().lstrip('§').strip()
                         for x in re.split(r'[、,，;；]', sec) if x.strip()]
                hits = []
                # 带来源短名时只在那一份原文里找（与段号分支同一约定）——
                # 否则「A 文档 §References」可能被 B 文档里恰好同名的标题蒙混过去。
                scoped = raw_texts
                if pm.groupdict().get('hint'):
                    h = pm.group('hint')
                    narrowed = [(s, t) for s, t in raw_texts
                                if h in os.path.splitext(os.path.basename(raw_file.get(s, '')))[0]
                                or h in os.path.splitext(os.path.basename(s))[0]]
                    if narrowed:
                        scoped = narrowed
                for s1 in parts:
                    if s1 in OPENING_SECS:
                        hits.append(any(re.search(r'^#\s+\S', t, re.M) for _, t in scoped))
                    else:
                        head = re.split(r'\s*[·/／（(]\s*', s1)[0].strip()
                        hits.append(any(title_hit(t, head) is not None for _, t in scoped))
                if parts and all(hits):
                    pos_stats['ok'] += 1
                else:
                    bad = next((s1 for s1, h in zip(parts, hits) if not h), sec)
                    pos_stats['warn'] += 1
                    add(WARN, '位置标注',
                        '标题路径「%s」在可读原文的标题行里找不到，请人工确认' % bad[:26])
                continue
            pos_stats['warn'] += 1
            add(WARN, '位置标注', '无法识别的位置格式：%s' % p[:30])

    # ---- 9. 入边 ----
    if title:
        names = [title] + ([a for a in fm['aliases'] if a] if isinstance(fm.get('aliases'), list) else [])
        pats = [re.compile(r'\[\[\s*' + re.escape(n) + r'\s*(?:\||\]\])', re.I) for n in names if n]
        inbound = []
        for f in glob.glob(os.path.join(wiki_root, '**', '*.md'), recursive=True):
            if os.path.normpath(f) == os.path.normpath(page_path):
                continue
            if os.path.normpath(os.path.dirname(f)) == os.path.normpath(os.path.join(wiki_root, 'entities')):
                continue
            t = read(f)
            if any(p.search(t) for p in pats):
                inbound.append(f)
        if not inbound:
            add(WARN, '入边', '全库没有任何内容页链接到「%s」（02 §1：entity 可按元数据检索；'
                '建完仍须回填有实际内容关系的出链）' % title)
    else:
        inbound = []

    # ---- 输出 ----
    print()
    print('── 七节骨架（02 §3）──')
    for kw in REQUIRED_SECTIONS:
        mark = 'OK  ' if find_section(body, kw)[0] else 'FAIL'
        print('  %s %s' % (mark, SECTION_LABEL.get(kw, kw)))
    print('  %s 一句话定位（H1 下的 > 引用块）' % ('OK  ' if loc_line else 'WARN'))

    print()
    print('── 检查项 ──')
    for level, item, msg in results:
        print('%-4s [%s] %s' % (level, item, msg))
    if not results:
        print('OK   无问题')

    print()
    print('── 位置标注 ──')
    if not raw_texts:
        print('跳过（sources 未能解析到可读原文）')
    else:
        print('可读原文 %d 份；位置通过 %d，失败 %d，需人工确认 %d，〔素材外〕标记 %d'
              % (len(raw_texts), pos_stats['ok'], pos_stats['fail'], pos_stats['warn'], pos_stats['out']))

    print()
    print('── 入边 ──')
    print('被 %d 个内容页引用%s' % (len(inbound),
          ('：' + '、'.join(os.path.basename(f) for f in inbound[:5])) if inbound else ''))

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
        print('全部机检项通过。注意：脚本验不了 02 §0 的最后一问——'
              '「读完这一页，知道它是什么、什么场景用它、它现在还有效吗」，那靠人工。')
    return 1 if n_fail else 0


# ---------- 入口 ----------

def main():
    ap = argparse.ArgumentParser(
        description='entity 页硬自检器（Wiki Skill 页面结构校验）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--page', help='要校验的 entity 页路径')
    ap.add_argument('--registry', action='store_true', help='列出符号表并查重（含别名冲突）')
    ap.add_argument('--check', help='配合 --registry：做名称解析测试')
    ap.add_argument('--scan', action='store_true', help='配合 --raw：从一份素材扫具名对象')
    ap.add_argument('--scan-all', action='store_true', help='扫 raw/ 全部素材，按跨源频次排序')
    ap.add_argument('--context', help='在原文中打印某名称的上下文（核对 ASR 错字）')
    ap.add_argument('--raw', help='原文路径')
    ap.add_argument('--raw-root', default='raw', help='--scan-all 的素材根目录，默认 raw')
    ap.add_argument('--entities-dir', default='wiki/entities', help='符号表目录，默认 wiki/entities')
    ap.add_argument('--sources-root', default='wiki/sources', help='source 页目录，默认 wiki/sources')
    ap.add_argument('--wiki-root', default='wiki', help='wiki 根目录，默认 wiki')
    ap.add_argument('--limit', type=int, default=40, help='--scan 的显示上限，默认 40')
    ap.add_argument('--min-sources', type=int, default=0,
                    help='候选至少要出现在几份素材里才列出。默认：--scan-all 为 2，--scan 为 1')
    ap.add_argument('--width', type=int, default=80, help='--context 的上下文宽度，默认 80')
    args = ap.parse_args()

    if args.registry:
        return cmd_registry(args.check, args.entities_dir)

    if args.context:
        paths = ([args.raw] if args.raw
                 else sorted(glob.glob(os.path.join(args.raw_root, '**', '*.md'), recursive=True)))
        if args.raw and not os.path.exists(args.raw):
            print('错误：找不到原文 %s' % args.raw, file=sys.stderr)
            return 2
        return cmd_context(args.context, paths, args.width)

    if args.scan_all:
        paths = sorted(glob.glob(os.path.join(args.raw_root, '**', '*.md'), recursive=True))
        if not paths:
            print('错误：%s 下没有 .md 素材' % args.raw_root, file=sys.stderr)
            return 2
        reg = registry_index(load_registry(args.entities_dir))
        minf = args.min_sources or 2
        return cmd_scan(paths, reg, args.limit, single=False, min_files=minf)

    if args.scan:
        if not args.raw:
            print('错误：--scan 需要同时提供 --raw', file=sys.stderr)
            return 2
        if not os.path.exists(args.raw):
            print('错误：找不到原文 %s' % args.raw, file=sys.stderr)
            return 2
        reg = registry_index(load_registry(args.entities_dir))
        minf = args.min_sources or 1
        return cmd_scan([args.raw], reg, args.limit, single=True, min_files=minf)

    if args.page:
        if not os.path.exists(args.page):
            print('错误：找不到页面 %s' % args.page, file=sys.stderr)
            return 2
        return cmd_page(args.page, args.sources_root, args.wiki_root)

    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
