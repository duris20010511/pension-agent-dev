# -*- coding: utf-8 -*-
"""
펀드 카드 — 투자설명서 100종의 핵심 사실을 펀드마다 한 장(약 800자)으로 묶는다.

왜 — 상품 질문의 검색을 진단하니(2026-10-08, 투자설명서가 정답인 34문항, 조각 8개)
정답 문장을 가져온 건 17문항뿐이었고, 놓친 17문항 중 15문항은 **펀드는 맞게 찾고 페이지를
잘못 가져왔다.** 정답은 대부분 앞쪽 간이투자설명서(위험등급·보수·투자목적·수익률, 3~11쪽)에
있는데 검색은 뒤쪽 본문의 비슷한 문장을 가져왔다. 그래서 펀드마다 그 핵심 사실을 쪽수와 함께
한 장에 모아, 질문에 펀드가 나오면 이 카드를 근거로 넣는다. 긴 페이지 여러 장보다 토큰도 적다.

사실은 LLM 이 투자설명서를 읽고 뽑았다(workflow: fund-cards-extract-all). 틀린 숫자를 막기 위해
모든 사실에 원문 인용·쪽수를 받았고, 여기서 기계적으로 대조해 통과한 것만 카드에 쓴다.
  1) 인용이 파일에 글자 그대로(공백 무시) 있어야 한다
  2) 인용이 적힌 쪽(±1)에 있어야 한다 — 다른 쪽에서 찾으면 그 쪽으로 고친다
  3) 값의 숫자가 그 쪽 글에 있어야 한다

    python fund_cards.py data/fund_facts_raw.json      # 검증 → data/fund_cards.json
"""
import os
import re
import sys
import io
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chunker import load_pages

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROS = os.path.join(ROOT, 'extracted', 'prospectus')
CARDS = os.path.join(ROOT, 'data', 'fund_cards.json')

ORDER = ['운용사', '위험등급', '유형', '투자목적', '투자전략', '주요자산', '환헤지', '구조',
         '클래스보수', '환매방법', '환매수수료', '규모', '수익률', '설정일', '주의사항']
_NUM = re.compile(r'\d+(?:[.,]\d+)+|\d{2,}')


def norm(s):
    return re.sub(r'\s+', '', s or '')


def _pages(file):
    return {p: norm(t) for p, t in load_pages(os.path.join(PROS, file))}


def _class_on_page(value, ptext):
    """'수수료미징구-온라인-퇴직연금(Cpe(퇴직연금)): …' → 클래스 코드(Cpe)나 이름(수수료미징구-온라인-퇴직연금)이 쪽에 있는가."""
    head = value.split(':')[0]
    cands = re.findall(r'\(([A-Za-z][A-Za-z0-9\-]{0,8})', head)          # 괄호 안 클래스 코드
    name = re.sub(r'\(.*', '', head).strip()
    if name:
        cands.append(name)
    return any(norm(c) and norm(c) in ptext for c in cands)


def verify(fund):
    """검증을 통과한 사실과 버린 사실(이유 포함)을 돌려준다."""
    pages = _pages(fund['file'])
    whole = ''.join(pages.values())
    kept, dropped = [], []
    for f in fund['facts']:
        q = norm(f['quote'])
        if len(q) < 2 or q not in whole:
            dropped.append((f, '인용이 원문에 없음'))
            continue
        page = f['page']
        near = [p for p in (page, page - 1, page + 1) if p in pages and q in pages[p]]
        if not near:
            found = [p for p, t in pages.items() if q in t]
            if len(found) != 1:
                dropped.append((f, f'인용 쪽 불일치(적힌 쪽 {page}, 실제 {found[:5]})'))
                continue
            near = found
        page = near[0]
        ptext = pages[page]
        nums = [n for n in _NUM.findall(f['value']) if not re.fullmatch(r'(19|20)\d{2}', n)]
        miss = [n for n in nums if norm(n) not in ptext and norm(n.replace(',', '')) not in ptext]
        if miss:
            dropped.append((f, f'값의 숫자가 그 쪽에 없음 {miss[:3]}'))
            continue
        if f['field'] == '클래스보수' and not _class_on_page(f['value'], ptext):
            # 표에서 숫자만 인용하면 어느 클래스의 값인지는 인용으로 확인되지 않는다 — 클래스명이 같은 쪽에 있어야 한다
            dropped.append((f, '클래스명이 그 쪽에 없음'))
            continue
        kept.append({**f, 'page': page})
    return kept, dropped


def card_text(name, facts):
    """카드 본문. 항목마다 한 줄, 끝에 쪽수."""
    by = {}
    for f in facts:
        by.setdefault(f['field'], []).append(f)
    lines = [f'[펀드 요약] {name}']
    for field in ORDER:
        fs = by.get(field)
        if not fs:
            continue
        vals = '; '.join(dict.fromkeys(f['value'] for f in fs))
        pgs = ', '.join(str(p) for p in sorted({f['page'] for f in fs}))
        lines.append(f'- {field}: {vals} (p.{pgs})')
    return '\n'.join(lines)


def build(raw):
    out, report = [], []
    for fund in raw['funds']:
        kept, dropped = verify(fund)
        out.append({'file': fund['file'], 'fund_name': fund['fund_name'], 'facts': kept,
                    'card': card_text(fund['fund_name'], kept)})
        report.append((fund['file'], len(kept), len(dropped), dropped))
    return out, report


# ---------------------------------------------------------------- 펀드 이름 라우팅
# 카드를 검색에만 맡기면 질문에 펀드명이 분명히 나와도 상위 10개에 못 드는 경우가 많았다
# (정답 펀드 카드 33/53 — "라이프사이클 7090", "다올전단채", "KB스타 코스닥150" 등).
# 투자설명서 페이지 6,520개와 경쟁하기 때문이다. 그래서 질문에 나온 펀드를 이름으로 찾아
# 그 카드를 근거 맨 앞에 둔다. 펀드명은 띄어쓰기가 제각각이라 글자 3개 단위로 맞추고,
# 여러 펀드 이름에 흔한 조각('증권'·'투자신탁')은 IDF 로 가중치를 낮춘다.
_ROUTE = None


def _grams(s, n=3):
    s = norm(s).lower()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _route_index(cards):
    import math
    names = {c['file']: re.sub(r'[\[\]()（）]', '', c['fund_name']) for c in cards if c['facts']}
    g = {f: _grams(n) for f, n in names.items()}
    df = {}
    for gs in g.values():
        for x in gs:
            df[x] = df.get(x, 0) + 1
    n = len(g)
    idf = {x: math.log(n / d) for x, d in df.items()}
    return g, idf


def mentioned_funds(question, cards=None, top=4, min_score=9.0, rel=0.4):
    """질문에 이름이 나온 펀드 파일들(점수 높은 순, 최대 top 개)."""
    global _ROUTE
    if _ROUTE is None:
        _ROUTE = _route_index(cards if cards is not None else load_cards())
    g, idf = _ROUTE
    q = _grams(question)
    scores = {f: sum(idf[x] for x in q & gs) for f, gs in g.items()}
    if not scores:
        return []
    best = max(scores.values())
    if best < min_score:
        return []
    picked = sorted((s, f) for f, s in scores.items() if s >= max(min_score, best * rel))
    return [f for s, f in sorted(picked, reverse=True)[:top]]


def load_cards():
    if not os.path.exists(CARDS):
        return []
    return json.load(io.open(CARDS, encoding='utf-8'))


def card_chunks(cards=None):
    """색인에 넣을 카드 조각."""
    out = []
    for c in (cards if cards is not None else load_cards()):
        if not c['facts']:
            continue
        out.append({'file': c['file'], 'page': None, 'idx': -1, 'text': c['card'],
                    'section': '펀드 요약', 'kind': 'card', 'source': f"{c['file']} 요약",
                    'card_pages': sorted({f['page'] for f in c['facts']})})
    return out


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raw = json.load(io.open(sys.argv[1], encoding='utf-8'))
    cards, report = build(raw)
    tot_k = sum(r[1] for r in report)
    tot_d = sum(r[2] for r in report)
    print(f'펀드 {len(cards)}종 · 사실 {tot_k + tot_d}개 중 통과 {tot_k}개, 버림 {tot_d}개')
    reasons = {}
    for _, _, _, ds in report:
        for f, why in ds:
            reasons[why.split('(')[0].split(' [')[0]] = reasons.get(why.split('(')[0].split(' [')[0], 0) + 1
    print('버린 이유:', reasons)
    if len(sys.argv) > 2 and sys.argv[2] == '--show':
        for c in cards[:3]:
            print('\n' + c['card'])
        for f, n_k, n_d, ds in report[:3]:
            for fact, why in ds[:5]:
                print('  버림', f, fact['field'], fact['value'][:50], '|', why)
    os.makedirs(os.path.dirname(CARDS), exist_ok=True)
    json.dump(cards, io.open(CARDS, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'저장 {CARDS}')
