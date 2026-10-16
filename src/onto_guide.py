# -*- coding: utf-8 -*-
"""
온톨로지 길잡이 — 질문과 이어진 개념 구분만 골라 프롬프트에 넣는다.

왜 — 모델이 쓴 답의 오류는 대부분 개념을 헷갈린 것이었다(연금수령연차 ↔ 실제수령연차, 1,500만 원 전액,
납입한도 ↔ 공제한도 …). 이 구분을 길잡이로 주면 줄지만, 모든 질문에 통째로 넣으면 질문과 상관없는 내용이
답에 섞이고 프롬프트가 길어진다. 그래서 길잡이를 온톨로지 노드·규칙에 붙여 두고, 질문이 지나는 노드와
이어진 것만 고른다.

고르는 길
  ① 질문 → 노드·규칙   data/onto_guide.json 의 고객 표현 사전(lexicon·rule_lexicon) + ontology.path_for 경로
  ② 한 단계 넓히기     읽은 노드와 엣지로 이어진 노드, 읽은 노드에 걸린 규칙(applies_to) — 무게를 낮춰
  ③ 개념 점수          개념에 붙은 노드·규칙이 ①에 있으면 2점, ②에만 있으면 1점(0.5) — 2점 이상 위에서 MAX 개
  ④ 프롬프트           고른 개념 문장 + 근거 문서 번호, 그리고 항상 넣는 지시 한 줄

모든 개념 문장은 taxcalc.RULES 원문(대조됨)으로 확인한다 — test_rules 가 키가 살아 있는지 본다.
이 모듈은 API 를 부르지 않는다.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get('AGENT_GUIDE_PATH') or os.path.join(ROOT, 'data', 'onto_guide.json')   # 짝비교 때 다른 판을 가리킨다
MAX = int(os.environ.get('AGENT_GUIDE_MAX', '4'))
_G = None


def load():
    global _G
    if _G is None:
        g = json.load(open(PATH, encoding='utf-8'))
        g['_lex'] = [(n, re.compile(rx)) for n, rx in g['lexicon'].items()]
        g['_rlex'] = [(r, re.compile(rx)) for r, rx in g['rule_lexicon'].items()]
        _G = g
    return _G


def _neighbors(nodes):
    """읽은 노드 → (엣지로 이어진 노드, 노드에 걸린 규칙) — 한 단계만."""
    try:
        import ontology
        o = ontology.load()
    except Exception:
        return set(), set()
    near, rules = set(), set()
    for e in o.get('edges', []):
        if e.get('from') in nodes and not str(e.get('to', '')).startswith('R-'):
            near.add(e['to'])
        if e.get('to') in nodes and not str(e.get('from', '')).startswith('R-'):
            near.add(e['from'])
        for r in [e.get('rule')] + list(e.get('rules') or []):
            if r and (e.get('from') in nodes or e.get('to') in nodes):
                rules.add(r)
    for r in o.get('rules', []):
        if set(r.get('applies_to') or []) & nodes:
            rules.add(r['id'])
    return near - nodes, rules


def _norm(q):
    try:
        import taxcalc
        return taxcalc.normalize(taxcalc.norm_ws(q or ''))
    except Exception:
        return q or ''


def question_nodes(q, with_intent=False):
    """질문 → (노드 집합, 규칙 집합[, 경로 의도]). 고객 표현 사전 + 온톨로지 경로(path_for)."""
    g = load()
    qn = _norm(q)
    nodes = {n for n, rx in g['_lex'] if rx.search(qn)}
    rules = {r for r, rx in g['_rlex'] if rx.search(qn)}
    intent = ''
    try:
        import onto_link
        _, _, steps = onto_link.question_profile(qn)
        intent = next((s.get('intent') for s in steps if s.get('intent')), '') or ''
        for s in steps:
            if s.get('step') in ('개체 인식', '요건 판정', '세금 계산'):
                for n in s.get('nodes') or []:
                    (rules if n.startswith('R-') else nodes).add(n)
    except Exception:
        pass
    return (nodes, rules, intent) if with_intent else (nodes, rules)


def select(q, limit=None):
    """질문 → [(개념, 점수, 이유)] 점수 순, 2점 이상만 limit 개.

    점수: 질문에서 읽은 규칙 2 · 주제 노드(재원·인출·세목·사건) 1.5 · 계좌 노드 0.5(계좌 이름만으로는 어떤 개념인지
    모른다 — 'IRP 옮기기'에 공제 한도가 붙었다) · 한 단계 이웃 0.25(최대 1)."""
    g = load()
    nodes, rules, intent = question_nodes(q, with_intent=True)
    near, near_rules = _neighbors(nodes)
    ctx = set(g.get('context_nodes') or [])
    qn = _norm(q)
    out = []
    for c in g['concepts']:
        # 검수(10-15): 'DB형인데 해지할 수 있어요?'에 연금계좌 재원 3가지·16.5%(G01)가 1위로 붙었다 — 경로가 연금계좌가 아닌
        # 제도(DB·DC·(구)개인연금저축·ISA)로 정한 질문에는 연금계좌 전용 개념(not_for)을 고르지 않는다
        if intent and intent in (c.get('not_for') or ()):
            continue
        if c.get('requires') and not set(c['requires']) & nodes:
            continue                    # 제도 전용 개념(DB·DC·(구)개인연금저축)은 그 제도를 말한 질문에만
        cn, cr = set(c.get('nodes') or []), set(c.get('rules') or [])
        dr, dn = cr & rules, cn & nodes
        hop = ((cn & near) | (cr & near_rules)) - dr - dn
        score = 2 * len(dr) + 1.5 * len(dn - ctx) + 0.5 * len(dn & ctx) + min(1.0, 0.25 * len(hop))
        # 노드·규칙이 없는 개념(옮기기·건강보험료 …)은 개념 고유의 고객 표현(match)으로 잇는다
        hit = bool(c.get('match')) and re.search(c['match'], qn) is not None
        score += 2.5 if hit else 0
        if score >= 2:
            out.append((c, score, (['표현'] if hit else []) + sorted(dr | dn) + sorted(hop)))
    # 고객 표현(match)으로 걸린 개념을 먼저 넣고, 남은 칸을 노드·규칙 점수로 채운다 — 퇴직금 같은 넓은 말에 붙는
    # 일반 개념(G01·G03·G04·G10, 5점 안팎)이 4칸을 다 차지해 질문에 딱 맞는 개념(IRP 가입대상·DB 근속기간)이
    # 밀려났다(10-15 개념 늘리기 검토). 표현으로 걸린 개념끼리는 점수 순.
    out.sort(key=lambda x: (-('표현' in x[2]), -x[1]))
    return out[:limit or MAX]


def quotes(c):
    """개념의 원문 [(문서 파일, 원문)] — taxcalc.RULES 키(원문 대조됨) + sources(문서 원문 그대로)."""
    import taxcalc
    out = [taxcalc.RULES[k] for k in c.get('quotes') or [] if k in taxcalc.RULES]
    return out + [(s['doc'], s['quote']) for s in c.get('sources') or []]


def _docs(c):
    try:
        ds = []
        for f, _ in quotes(c):
            d = f.replace('.txt', '')
            if d not in ds:
                ds.append(d)
        return ds
    except Exception:
        return []


def text(q):
    """프롬프트에 붙일 길잡이와 추적 한 줄 — (문자열, 추적). 고른 개념이 없으면 지시 줄만."""
    g = load()
    picked = select(q)
    lines = ['', '', '# 연금 개념 길잡이 (온톨로지에서 이 질문과 이어진 개념만 골랐다 — 받은 문서 기준)']
    for c, _, _ in picked:
        ds = _docs(c)
        lines.append('- ' + c['text'] + (f" (근거: {'·'.join(ds)})" if ds else ''))
    lines.append(g['always'])
    nodes, rules = question_nodes(q)
    trace = ('개념 길잡이: 노드 [' + ', '.join(sorted(nodes)[:8]) + '] · 규칙 [' + ', '.join(sorted(rules)[:8]) + '] → 개념 ['
             + ', '.join(c['title'] for c, _, _ in picked) + ']')
    return '\n'.join(lines), trace
