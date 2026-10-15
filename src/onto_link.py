# -*- coding: utf-8 -*-
"""
온톨로지를 답변 경로에 잇는다 — 질문이 지나는 온톨로지 경로로 근거 조각을 고르고, 펀드에 상품 분류를 붙인다.

  조각 꼬리표   data/ontology_chunks.json — 제도문서 조각 579개마다 생애주기 단계·노드(계좌·재원·인출 유형·
               세목·사건)·규칙(R-xx). 분류자 두 명이 따로 붙여 둘이 일치한 꼬리표만 남겼다(build_onto_chunks.py).
  상품 분류     data/ontology_funds.json — 펀드 100종의 자산 유형·위험등급·퇴직연금 위험자산 여부(R-14)와 근거
  질문 경로     ontology.path_for — 질문에서 읽은 고객 정보로 07 §7 의 경로(개체 → 빠진 속성 → 요건 → 인출 순서 →
               세금 → 근거)를 만든다

검색 순위 조정(rerank) — 경로에 나온 노드·규칙과 겹치는 조각을 몇 칸 앞으로 당긴다. 하이브리드 검색이 고른
후보(근거 수의 3배) 안에서만 움직이므로 새 조각을 끌어오지는 않는다. AGENT_ONTO=0 이면 끈다(짝비교용).

이 모듈은 API 를 부르지 않는다.
"""
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENABLED = os.environ.get('AGENT_ONTO', '1') != '0'
_CHUNKS = None
_FUNDS = None

# 경로 단계별 무게 — 질문에서 직접 읽은 개체가 가장 무겁고, 세금·근거 단계에서 덧붙는 노드는 가볍다
# 첫 측정(개체 2·요건 1.5·순서 1·세금 0.5·근거 0.5, 1점당 2칸, 최대 8칸)은 R@10 86.6 → 85.6 으로 떨어졌다 —
# 세금·근거 단계의 넓은 노드가 정답 조각을 밀어냈다. 질문에서 직접 읽은 개체와 요건 규칙만 쓰고 폭을 줄인다.
STEP_WEIGHT = {k: float(v) for k, v in (x.split(':') for x in os.environ.get(
    'AGENT_ONTO_WEIGHTS', '개체 인식:1,요건 판정:1').split(','))}
SHIFT_PER_POINT = float(os.environ.get('AGENT_ONTO_SHIFT', '1'))   # 겹침 1점마다 앞으로 당기는 칸 수
MAX_SHIFT = float(os.environ.get('AGENT_ONTO_MAX', '3'))           # 한 조각이 당겨지는 최대 칸 수
# 계좌 노드 → 같은 제도 노드(온톨로지 '속한다' 엣지와 같은 짝)
ACCOUNT_PLAN = {'account.dc': 'plan.dc', 'account.irp': 'plan.irp', 'account.savings': 'plan.savings'}
# 연금계좌(IRP·연금저축)가 아닌 제도 — 이 제도만 말한 질문에는 연금계좌 인출 규칙 노드를 쓰지 않는다
NON_PENSION = ('plan.db', 'plan.old', 'plan.severance', 'plan.irp_corp')
PENSION_ONLY = ('wd.other', 'wd.pension', 'R-04', 'R-05', 'R-06', 'R-10', 'fund.free', 'fund.credit')

# 질문의 생애주기 단계 — 조각 꼬리표의 stages 와 맞춰 본다
_STAGE_Q = [
    ('stage.early', r'중도\s*인출|해지|깨(?:면|서)|부득이'),
    ('stage.payout', r'수령|받으면|받을\s*때|개시|연금으로\s*받|빼면|인출'),
    ('stage.pay', r'넣|납입|입금|세액\s*공제|공제|환급|연말\s*정산'),
    ('stage.transfer', r'이전|옮기|옮겨|실물|이체|갈아타'),
    ('stage.invest', r'상품|펀드|ETF|TDF|디폴트|운용|위험자산|매수|매도'),
    ('stage.join', r'가입|개설|만들'),
    ('stage.tax', r'세금|세율|과세|떼|원천징수|종합소득'),
    ('stage.protect', r'예금자\s*보호|압류|상속|승계'),
]


def _load(name):
    p = os.path.join(ROOT, 'data', name)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return None


def chunk_table():
    global _CHUNKS
    if _CHUNKS is None:
        d = _load('ontology_chunks.json') or {}
        _CHUNKS = d.get('chunks', {}) if isinstance(d, dict) else {}
    return _CHUNKS


def fund_table():
    global _FUNDS
    if _FUNDS is None:
        d = _load('ontology_funds.json') or {}
        _FUNDS = {f['file']: f for f in d.get('funds', [])} if isinstance(d, dict) else {}
    return _FUNDS


def label(n):
    """노드 id → 07 의 한글 이름(시연 화면용). 규칙 번호·모르는 id 는 그대로."""
    try:
        import ontology
        return ontology.load()['index'].get(n, {}).get('label') or n
    except Exception:
        return n


def chunk_key(c):
    return f"{c.get('file', '')}#{c.get('idx', '')}"


def chunk_tags(c):
    """조각의 온톨로지 꼬리표 {'stages','nodes','rules'} — 색인이 다시 만들어져 본문이 바뀌었으면 None."""
    t = chunk_table().get(chunk_key(c))
    if not t:
        return None
    if t.get('h') and t['h'] != hashlib.md5((c.get('text') or '').encode('utf-8')).hexdigest()[:10]:
        return None
    return t


def question_profile(q):
    """질문 → ({노드·규칙: 무게}, {단계}, 경로 단계 목록). 개체를 하나도 못 읽으면 빈 무게."""
    try:
        import ontology
        import taxcalc
        steps = ontology.path_for(taxcalc.parse(q or ''), q)
    except Exception:
        return {}, set(), []
    w = {}
    for s in steps:
        sw = STEP_WEIGHT.get(s.get('step'))
        if not sw:
            continue
        for n in s.get('nodes') or []:
            if n.startswith(('attr.', 'doc')):
                continue
            w[n] = max(w.get(n, 0.0), sw)
    # 같은 계좌를 가리키는 이름이 질문 쪽(account.*)과 조각 꼬리표 쪽(plan.*)으로 갈렸다 — 꼬리표는 plan.dc 107개·
    # account.dc 5개. 계좌 노드를 같은 제도 노드로도 펼쳐 DC·IRP 전용 근거(doc14·doc46~50)가 개체 일치 점수를 받게 한다
    for a, pl in ACCOUNT_PLAN.items():
        if a in w:
            w[pl] = max(w.get(pl, 0.0), w[a])
    # 연금계좌가 아닌 제도(DB·(구)개인연금저축) 질문에 연금계좌 인출 규칙이 붙어 개인 연금계좌의 해지·연금외수령 조각을
    # 끌어오지 않게 한다(검수 10-15: 'DB형인데 해지' 에 doc41·doc39 가 DB 중도인출 불가 조각과 똑같이 당겨졌다)
    entities = next((s.get('nodes') or [] for s in steps if s.get('step') == '개체 인식'), [])
    if any(n in entities for n in NON_PENSION) and not any(n in entities for n in ('account.irp', 'account.savings')):
        for n in PENSION_ONLY:
            w.pop(n, None)
    stages = {sid for sid, rx in _STAGE_Q if re.search(rx, q or '')} if os.environ.get('AGENT_ONTO_STAGE', '0') == '1' else set()
    # 개체를 하나도 못 읽은 질문(상품·절차 일반 질문)은 경로가 기본 노드뿐이라 순위를 건드리지 않는다
    if not entities:
        return {}, stages, steps
    return w, stages, steps


def overlap(tags, weights, stages):
    if not tags:
        return 0.0
    got = sum(weights.get(n, 0.0) for n in set(tags.get('nodes', [])) | set(tags.get('rules', [])))
    if stages & set(tags.get('stages', [])):
        got += 1.0
    return got


def rerank(hits, q):
    """하이브리드 후보 [(조각, 점수)] 를 질문의 온톨로지 경로와 겹치는 만큼 앞으로 당긴다 → (새 순서, 설명 줄 또는 '')."""
    if not ENABLED or not hits or not chunk_table():
        return hits, ''
    weights, stages, steps = question_profile(q)
    if not weights:
        return hits, ''
    scored = []
    moved = 0
    for rank, (c, sc) in enumerate(hits):
        tags = chunk_tags(c) if (c.get('file') or '').startswith('doc') else None
        o = overlap(tags, weights, stages)
        shift = min(MAX_SHIFT, SHIFT_PER_POINT * o)
        if shift >= 1:
            moved += 1
        scored.append((rank - shift, rank, c, sc))
    scored.sort(key=lambda x: (x[0], x[1]))
    out = [(c, sc) for _, _, c, sc in scored]
    top = ', '.join(label(k) for k, _ in sorted(weights.items(), key=lambda x: -x[1])[:6])
    return out, f'온톨로지 경로 [{top}] 와 겹치는 조각 {moved}개를 앞으로' if moved else ''


def fund_line(c):
    """펀드 요약 카드에 붙일 상품 분류 한 줄 — 온톨로지 상품 노드와 R-14(위험자산 70%) 판정."""
    f = fund_table().get(c.get('file', ''))
    if not f:
        return ''
    parts = [f.get('asset_class') or '']
    if f.get('risk_grade'):
        parts.append(f'위험등급 {f["risk_grade"]}등급')
    ra = f.get('risk_asset')
    if f.get('accounts') == ['연금저축']:
        # 가입자격이 연금저축계좌로 한정된 펀드 — DC·IRP 위험자산 70% 한도(R-14)를 붙이면 퇴직연금에서 살 수 있는 상품처럼 읽힌다
        parts.append('가입자격: 연금저축계좌 전용(투자설명서) — 퇴직연금(DC·IRP) 위험자산 한도 대상 아님')
    elif ra and ra != '확인 필요':
        parts.append(f'퇴직연금(DC·IRP) {ra}' + (' — 위험자산은 적립금의 70% 이내' if ra == '위험자산' else ''))
    elif ra:
        parts.append('퇴직연금 위험자산 여부: 문서로 확인 필요')
    return '[상품 분류] ' + ' · '.join(p for p in parts if p)


def evidence_rules(c):
    """근거 카드에 붙일 규칙 번호 — 조각 꼬리표의 rules."""
    t = chunk_tags(c)
    return list(t.get('rules', [])) if t else []
