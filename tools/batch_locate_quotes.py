#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量定位 source 页摘录。

为什么需要它
------------
`skills/wiki-source-page/scripts/verify_source_page.py --locate` 一次只处理一条摘录。
一份信息密集的素材可达 30 条以上摘录，逐条调用不现实，手数段号又会出错。
本脚本一次算完整页：读页面里「证据摘录」的每一行摘录，按原文段落定位，
生成符合 01 规范 §5.3 的位置标注。

段号口径与 verify_source_page.py 一致：「段」= 原文里的一个非空行，空行不计，段号从 1 开始。

两种位置标注自动切换（01 §5.3）：
- 录音清洗稿（`raw/record_cleaned_markdown/`）：`〔第 14 段「段首十字…」〕`
- 智能纪要（`raw/Audio_transcript/`，自带 `@说话人 N MM:SS`）：`〔@说话人 1 00:26〕`
  原文里出现说话人时间戳标题段时自动改用这一档，不再输出段序号。

用法
----
1) 干跑：只报告每条摘录命中的段位与建议标注，不改文件
   python3 tools/batch_locate_quotes.py --page wiki/sources/xxx.md

2) 写入：把算好的位置标注补进页面（已有且有效的位置保留，缺失或错误的重算）
   python3 tools/batch_locate_quotes.py --page wiki/sources/xxx.md --write

只读原文；--write 只改传入的 source 页。
"""

import argparse
import os
import re
import sys

QUOTE_RE = re.compile(r'「([^」]+)」')
POS_RE = re.compile(r'〔([^〕]+)〕')
HEAD_RE = re.compile(r'^第\s*(\d+)\s*段\s*(?:「(.*?)」)?')
# 智能纪要（raw/Audio_transcript/）自带说话人与时间戳；01 §5.3 规定这类材料
# 的位置标注用「说话人 + 时间戳」，比段序号信息量高，所以优先认这一档。
TS_HEAD_RE = re.compile(r'^@\s*说话人\s*(\d+)\s+(\d{1,2}:\d{2})$')
POS_TS_RE = re.compile(r'@\s*说话人\s*(\d+)\s+(\d{1,2}:\d{2})')


def read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def norm(s):
    """去掉所有空白字符，容忍折行与空格差异，但不放过任何字符增删。"""
    return re.sub(r'\s+', '', s)


def split_paras(text):
    return [ln.strip() for ln in text.split('\n') if ln.strip()]


def locate(paras, quote):
    nq = norm(quote)
    if len(nq) < 4:
        return []
    return [i for i, p in enumerate(paras, 1) if nq in norm(p)]


def timestamp_index(paras):
    """给智能纪要用：算出每段的内容段归属哪个说话人+时间戳。

    时间戳单独占一段（`@说话人 1  00:25`），它后面的段才是内容。
    返回 (ts_of, by_stamp)：
      ts_of[i]     第 i 段归属的 (说话人, 时间戳)，时间戳标题段本身不归任何戳
      by_stamp[t]  该戳下的内容段号列表
    """
    ts_of, cur = {}, None
    for i, p in enumerate(paras, 1):
        m = TS_HEAD_RE.match(p.strip())
        if m:
            cur = (m.group(1), m.group(2))
            continue
        ts_of[i] = cur
    by_stamp = {}
    for i, t in ts_of.items():
        if t:
            by_stamp.setdefault(t, []).append(i)
    return ts_of, by_stamp


def label_for(paras, n, ts_of=None):
    t = (ts_of or {}).get(n)
    if t:
        return '〔@说话人 %s %s〕' % (t[0], t[1])
    return '〔第 %d 段「%s…」〕' % (n, paras[n - 1][:10])


def existing_positions_valid(paras, line, ts_of=None, by_stamp=None):
    """行内已有位置是否都成立：每个位置指向的段里必须能找到该行的某条摘录。"""
    positions = POS_RE.findall(line)
    if not positions:
        return False
    clean = POS_RE.sub('', line)
    quotes = QUOTE_RE.findall(clean)
    if not quotes:
        return False
    for pos in positions:
        p = pos.strip()
        tm = POS_TS_RE.search(p)
        if tm:
            idxs = (by_stamp or {}).get((tm.group(1), tm.group(2)))
            if not idxs:
                return False
            if not any(any(norm(q) in norm(paras[i - 1]) for q in quotes) for i in idxs):
                return False
            continue
        m = HEAD_RE.match(p)
        if not m:
            return False
        n = int(m.group(1))
        if n < 1 or n > len(paras):
            return False
        seg = paras[n - 1]
        if not any(norm(q) in norm(seg) for q in quotes):
            return False
        head = m.group(2)
        if head:
            h = head.rstrip('…').rstrip('.').rstrip('·').strip()
            if h and not norm(seg).startswith(norm(h)):
                return False
    return True


def raw_from_frontmatter(page_text):
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', page_text, re.S)
    if not m:
        return None
    mm = re.search(r'^source_path:\s*(.+)$', m.group(1), re.M)
    if not mm:
        return None
    return mm.group(1).strip().strip('"').strip("'")


def main():
    ap = argparse.ArgumentParser(description='批量定位 source 页摘录（source Skill 配套）')
    ap.add_argument('--page', required=True, help='source 页路径')
    ap.add_argument('--raw', help='原文路径，缺省读 frontmatter 的 source_path')
    ap.add_argument('--write', action='store_true', help='把位置标注写回页面；不加则只报告')
    args = ap.parse_args()

    if not os.path.exists(args.page):
        print('错误：找不到页面 %s' % args.page, file=sys.stderr)
        return 2
    page = read(args.page)
    raw_path = args.raw or raw_from_frontmatter(page)
    if not raw_path or not os.path.exists(raw_path):
        print('错误：找不到原文 %s（可用 --raw 指定）' % raw_path, file=sys.stderr)
        return 2

    paras = split_paras(read(raw_path))
    ts_of, by_stamp = timestamp_index(paras)
    locator = '智能纪要时间戳' if by_stamp else '段序号'
    print('原文：%s（%d 段，标位方式：%s）' % (raw_path, len(paras), locator))
    print('页面：%s' % args.page)

    in_quotes_section = False
    out_lines = []
    n_total = n_kept = n_filled = n_fail = 0
    failures = []

    for line in page.split('\n'):
        stripped = line.strip()
        if re.match(r'^##\s+', stripped):
            in_quotes_section = '证据摘录' in stripped
            out_lines.append(line)
            continue
        if not in_quotes_section or not re.match(r'^\s*(?:[-*]\s*)?「', stripped):
            out_lines.append(line)
            continue

        n_total += 1
        clean = POS_RE.sub('', line)
        quotes = QUOTE_RE.findall(clean)
        if not quotes:
            out_lines.append(line)
            continue

        if existing_positions_valid(paras, line, ts_of, by_stamp):
            n_kept += 1
            out_lines.append(line)
            continue

        hits = []
        for q in quotes:
            for i in locate(paras, q):
                if i not in hits:
                    hits.append(i)
        hits.sort()

        if not hits:
            n_fail += 1
            failures.append((quotes[0][:50], line))
            out_lines.append(line)
            continue

        labels = ''.join(label_for(paras, i, ts_of) for i in hits)
        if len(hits) > 1:
            print('  注意：本条命中 %d 段 %s，已全部写入。摘录：%s'
                  % (len(hits), hits, quotes[0][:36]))
        newline = POS_RE.sub('', line).rstrip() + labels
        n_filled += 1
        out_lines.append(newline)

    print()
    print('摘录行 %d：已有有效位置 %d，本次补/改 %d，无法定位 %d'
          % (n_total, n_kept, n_filled, n_fail))
    if failures:
        print()
        print('以下摘录在原文中搜不到，必须改回原话或删除：')
        for q, _ in failures:
            print('  ✗ %s…' % q)

    if args.write:
        with open(args.page, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out_lines))
        print()
        print('已写回 %s' % args.page)
    else:
        print()
        print('（干跑，未写文件；确认无误后加 --write）')

    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main())
