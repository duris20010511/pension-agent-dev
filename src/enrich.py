# -*- coding: utf-8 -*-
"""
조각 보강 — ②색인·④찾기 진단에서 나온 두 문제를 고친다.

문제 1. 투자설명서 100종은 법정 서식이라 본문이 거의 동일하다.
        환매·신탁업자·운용제한 조항은 펀드마다 글자 하나 안 다르다.
        그래서 "솔로몬 국공채 환매" 로 찾아도 엉뚱한 펀드의 같은 조항이 걸린다.
   → 조각마다 '어느 펀드/문서의 것인지' 제목을 앞에 붙여서 색인한다.

문제 2. 같은 보일러플레이트가 수십 개 파일에 있어 검색 결과를 채운다.
   → 몇 개 파일에 걸쳐 반복되는지 세어두고, 검색 시 가중치를 낮춘다.
"""
import os
import re
from collections import Counter

FUND_RE = re.compile(r'[가-힣A-Za-z0-9·\-]{2,40}?(?:증권자?투자신탁|증권투자신탁|투자신탁)'
                     r'(?:제?\d+호)?(?:\([^)]{1,20}\)|\[[^\]]{1,20}\])?')


def fund_name(text):
    """투자설명서에서 펀드명을 뽑는다. 운용사마다 표지 형식이 달라 명시 항목 → 빈도순으로 대체한다."""
    m = re.search(r'집합투자기구\s*(?:의\s*)?명칭\s*\n\s*(.+)', text)
    if m:
        cand = m.group(1).strip()
        if 2 < len(cand) < 80 and '투자신탁' in cand:
            return cand
    names = [x.group(0).strip() for x in FUND_RE.finditer(text[:60000])]
    names = [n for n in names if 6 < len(n) < 70]
    if names:
        return Counter(names).most_common(1)[0][0]
    return ''


# 로고·푸터·머리말 등 제목이 될 수 없는 줄
NOISE_RE = re.compile(
    r'^(?:MIRAE|Mirae\s*Asset|미래에셋증권|미래에셋|고객용|사내용|바로가기|감사합니다|'
    r'Contents|목\s*차|\d{4}\.\d{1,2}|[\d\s.\-·]+)$', re.I)


def doc_title(text):
    """제도·세제 문서의 제목. 첫 페이지에서 로고·푸터를 걷어낸 첫 의미 줄."""
    body = re.sub(r'^#.*$', '', text, flags=re.M)
    best = ''
    for ln in body.split('\n')[:60]:
        s = ln.strip()
        if not s or s.startswith('[페이지') or s.startswith('[슬라이드') or s.startswith('[표]'):
            continue
        s = re.sub(r'^\d+\s*', '', s)
        s = re.sub(r'Mirae\s*Asset\s*Securities', '', s, flags=re.I).strip()
        s = re.sub(r'^[\[\(【]?\s*업무\s*매뉴얼\s*[\]\)】]?\s*', '', s).strip()
        if len(s) < 4 or NOISE_RE.match(s):
            continue
        # 질문 리스트 머리표는 제목이 아니다
        if s.startswith('■') or s.startswith('①') or s.startswith('○'):
            continue
        best = s[:60]
        break
    return best


def add_titles(chunks, extract_root):
    """조각에 title 을 달고, 색인용 텍스트(index_text)를 만든다."""
    titles = {}
    for sub in ('docs', 'prospectus'):
        d = os.path.join(extract_root, sub)
        for f in os.listdir(d):
            text = open(os.path.join(d, f), encoding='utf-8').read()
            titles[f] = fund_name(text) if sub == 'prospectus' else doc_title(text)

    for c in chunks:
        t = titles.get(c['file'], '')
        c['title'] = t
        head = f"{t} " if t else ""
        # 제목을 두 번 넣어 가중치를 준다 (BM25 는 빈도 기반)
        c['index_text'] = f"{head}{head}{c['text']}"
    return chunks


def mark_boilerplate(chunks, min_files=8):
    """여러 파일에 반복되는 조각을 표시한다. 본문 앞 400자 지문으로 판단."""
    def fp(c):
        body = re.sub(r'\s+', '', re.sub(r'^\[.*?\]$', '', c['text'], flags=re.M))
        return body[:400]

    seen = {}
    for c in chunks:
        seen.setdefault(fp(c), set()).add(c['file'])

    n = 0
    for c in chunks:
        files = len(seen.get(fp(c), ()))
        c['dup_files'] = files
        c['boiler'] = files >= min_files
        if c['boiler']:
            n += 1
    return chunks, n
