# -*- coding: utf-8 -*-
"""
연금 온톨로지(D) — 07_연금온톨로지.md 의 클래스·관계·규칙·재원×인출 표를 코드에서 쓴다.

왜 — 1차 멘토링에서 "연금 도메인 이해가 부족해 보인다"는 지적을 받았다. 답이 맞아도
'왜 그 세금인지'를 구조로 보여주지 못했다. 07 문서로 구조를 정리했고, 이 모듈은 그 구조를
시연 화면(그래프)과 상담 흐름(질문이 지나는 경로)에서 그대로 쓰게 한다.

원칙
  - 규칙 번호·이름은 taxcalc.RULEBOOK 과 같다(test_ontology 가 검사). 숫자는 taxcalc 공용 함수로만 낸다
    — 식을 여기 다시 쓰면 규칙이 바뀔 때 두 곳을 고쳐야 한다.
  - 07 에 없는 것은 넣지 않는다. 07 이 '문서 확인 필요'로 남긴 칸(부득이 × 퇴직금)은 그대로 둔다.
  - 문서 노드의 독자·기준일은 문서 꼬리표(src/doc_meta.py · data/doc_meta.json)가 있으면 붙이고,
    없으면 비운 채로 동작한다(다른 모듈이 만드는 중이라 없을 수 있다).

함수
  load()                   ontology.json + RULEBOOK + 문서 꼬리표를 합친 dict
  tax_route(source, mode)  재원 × 인출 유형 → {'tax', 'rule_ids', 'text', ...}   (07 §6 표)
  rule_docs(rid)           규칙의 근거 문서 목록(원문 대조된 문서 먼저)
  graph()                  시연 그림용 {'nodes', 'edges'} — 노드 60개 이하
  path_for(parsed, q=None) 질문이 온톨로지를 지나는 단계(07 §7) — taxcalc.parse 결과를 받는다

path_for 의 금액은 답변의 계산 블록(taxcalc.compute)과 같아야 한다 — 시연 화면에서 경로 숫자와 답 숫자가
다르면 안 된다. 그래서 재원 금액 채우기는 taxcalc.by_source 와 같은 규칙을 따르고, 인출 순서가 정해지지
않으면(어느 재원에서 얼마가 빠지는지 모르면) 금액을 내지 않고 빠진 재원 금액을 되묻는다.
연금 수령액(연 단위) 질문은 규칙 경로만 보여 주고 금액은 taxcalc 계산 블록이 낸다.

이 모듈은 clova·answer 를 부르지 않는다 — test_ontology.py 가 API 없이 검사한다.
"""
import json
import math
import os
import re

import taxcalc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONTO_PATH = os.path.join(ROOT, 'data', 'ontology.json')
META_PATH = os.path.join(ROOT, 'data', 'doc_meta.json')
DOCS_DIR = os.path.join(ROOT, 'extracted', 'docs')
GRAPH_MAX = 60

_CACHE = None


# ---------------------------------------------------------------------------
# 문서 꼬리표 — 있으면 쓰고 없으면 건너뛴다
# ---------------------------------------------------------------------------
def _file(d):
    """12 · 'doc12' · 'doc12.txt' → 'doc12.txt'."""
    s = str(d).strip()
    if s.isdigit():
        return f'doc{s}.txt'
    return s if s.endswith('.txt') else s + '.txt'


def _doc_id(d):
    return _file(d)[:-4]


def _norm_meta(raw):
    """JSON 모양이 확정 전이라 {'docs': {...}} · {file: {...}} · [{'file': ...}] 모두 받는다."""
    if isinstance(raw, dict) and isinstance(raw.get('docs'), (dict, list)):
        raw = raw['docs']
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict) and re.match(r'^doc\d+(?:\.txt)?$', str(k)):
                out[_file(k)] = v
    elif isinstance(raw, list):
        for v in raw:
            if isinstance(v, dict) and v.get('file'):
                out[_file(v['file'])] = v
    return out


def _read_meta():
    """{file: 꼬리표}. doc_meta 모듈의 meta(file) 를 먼저 쓰고, 안 되면 JSON 을 직접 읽는다. 둘 다 없으면 {}."""
    files = sorted(f for f in (os.listdir(DOCS_DIR) if os.path.isdir(DOCS_DIR) else []) if re.match(r'^doc\d+\.txt$', f))
    try:
        import doc_meta                      # 다른 에이전트가 만드는 중 — 없거나 덜 됐으면 아래로
        out = {}
        for f in files:
            try:
                m = doc_meta.meta(f)
            except Exception:
                m = None
            if isinstance(m, dict) and m:
                m = dict(m)
                try:
                    m.setdefault('label', doc_meta.label(f))
                except Exception:
                    pass
                out[f] = m
        if out:
            return out
    except Exception:
        pass
    try:
        with open(META_PATH, encoding='utf-8') as fh:
            return _norm_meta(json.load(fh))
    except Exception:
        return {}


def _fallback_title(f):
    """꼬리표가 없을 때의 문서 이름 — 근거 표기(sources.doc_title)와 같은 이름을 쓴다."""
    try:
        from enrich import doc_title as first_line
        from sources import doc_title
        with open(os.path.join(DOCS_DIR, f), encoding='utf-8') as fh:
            return doc_title({'file': f, 'title': first_line(fh.read(6000))})
    except Exception:
        return ''


def _scope_note(f):
    try:
        from doc_policy import SCOPE
        if f in SCOPE:
            return '질문에 과학기술인공제회가 나올 때만 근거로 쓴다'
    except Exception:
        pass
    return ''


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------
def load(refresh=False, meta=None):
    """온톨로지 전체. meta 를 주면(시험용 {file: 꼬리표}) 그것을 쓰고 캐시하지 않는다."""
    global _CACHE
    if _CACHE is not None and not refresh and meta is None:
        return _CACHE
    with open(ONTO_PATH, encoding='utf-8') as fh:
        o = json.load(fh)
    book = {rid: (keys, summary, how) for rid, _, keys, summary, how in taxcalc.RULEBOOK}
    for r in o['rules']:
        keys, summary, how = book.get(r['id'], ([], '', ''))
        r['summary'], r['how'] = summary, how            # 요약·쓰임은 RULEBOOK 이 원본
        r['quote_keys'] = list(keys)
        r['quote_files'] = list(dict.fromkeys(taxcalc.RULES[k][0] for k in keys if k in taxcalc.RULES))
        r['docs'] = [_file(d) for d in r['docs']]
    for r in o['pending_rules']:
        r['docs'] = [_file(d) for d in r['docs']]
        r['quote_files'] = []
    for c in o['tax_matrix']['cells']:
        c['docs'] = [_file(d) for d in c['docs']]
    for s in o['stages']:
        s['docs'] = [_file(d) for d in s['docs']]
    o['index'] = {n['id']: n for n in o['nodes']}
    o['attr_index'] = {a['id']: a for a in o['attributes']}
    o['rule_index'] = {r['id']: r for r in o['rules'] + o['pending_rules']}
    o['docs'] = _doc_nodes(o, _read_meta() if meta is None else meta)
    o['meta']['doc_meta'] = any(d.get('audience') for d in o['docs'].values())
    if meta is None:
        _CACHE = o
    return o


def _doc_nodes(o, meta):
    """문서 노드 {file: {...}} — 규칙 근거·단계에 나오는 문서 전부. 꼬리표가 있으면 독자·기준일을 붙인다."""
    files = []
    for r in o['rules'] + o['pending_rules']:
        files += r['quote_files'] + r['docs']
    for s in o['stages']:
        files += s['docs']
    for c in o['documents']['conflicts']:
        files += [_file(d) for d in c['docs']]
    out = {}
    for f in dict.fromkeys(files):
        m = meta.get(f) or {}
        aud, as_of = m.get('audience', ''), m.get('as_of', '')
        label = m.get('label') or (f'{aud}용 · 기준일 {as_of or "미상"}' if aud else '')
        out[f] = {
            'id': _doc_id(f), 'file': f, 'title': m.get('title') or _fallback_title(f),
            'audience': aud, 'as_of': as_of, 'label': label,
            'stages': [s['id'] for s in o['stages'] if f in s['docs']],
            'rules': [r['id'] for r in o['rules'] if f in r['quote_files'] or f in r['docs']],
            'quoted': [r['id'] for r in o['rules'] if f in r['quote_files']],
            'scope': _scope_note(f),
        }
    return out


# ---------------------------------------------------------------------------
# 재원 × 인출 유형 → 세금 (07 §6)
# ---------------------------------------------------------------------------
_SRC_ALIAS = {'free': 'free', '①': 'free', 'fund.free': 'free', '과세제외금액': 'free',
              'retire': 'retire', '②': 'retire', 'fund.retire': 'retire', '이연퇴직소득': 'retire', '퇴직금': 'retire',
              'credit': 'credit', '③': 'credit', 'fund.credit': 'credit'}
_MODE_ALIAS = {'연금수령': '연금수령', 'wd.pension': '연금수령',
               '부득이': '부득이', '부득이한 사유': '부득이', '부득이한 사유 인출': '부득이', 'wd.unavoidable': '부득이',
               '연금외수령': '연금외수령', 'wd.other': '연금외수령'}
MODE_NODE = {'연금수령': 'wd.pension', '부득이': 'wd.unavoidable', '연금외수령': 'wd.other'}
SRC_NODE = {'free': 'fund.free', 'retire': 'fund.retire', 'credit': 'fund.credit'}
SRC_ORDER = ('free', 'retire', 'credit')


def tax_route(source, mode):
    """source: free|retire|credit, mode: 연금수령|부득이|연금외수령
    → {'tax', 'rule_ids', 'text'(07 표 칸 원문), 'plain'(쉬운 말), 'docs', 'nodes'}."""
    src = str(source).strip()
    # '① 과세제외금액'·'② 퇴직금(이연퇴직소득)'처럼 이름 전체가 와도 번호로 알아본다
    s = _SRC_ALIAS.get(src) or _SRC_ALIAS.get(src[:1])
    m = _MODE_ALIAS.get(str(mode).strip())
    if not s or not m:
        raise ValueError(f'tax_route: 모르는 재원·인출 유형 ({source!r}, {mode!r})')
    o = load()
    c = next(c for c in o['tax_matrix']['cells'] if c['source'] == s and c['mode'] == m)
    docs = list(c['docs'])
    for rid in c['rule_ids']:
        docs += [d['file'] for d in rule_docs(rid)[:1]]
    nodes = [SRC_NODE[s], MODE_NODE[m]] + ([c['tax_node']] if c['tax_node'] else [])
    # evidence — 07 이 '문서 확인 필요'로 남긴 칸의 문서 근거 후보(원문 그대로). 세목을 정하는 데는 쓰지 않는다
    return {'source': s, 'mode': m, 'tax': c['tax'], 'rule_ids': list(c['rule_ids']), 'text': c['text'],
            'plain': c['plain'], 'docs': list(dict.fromkeys(docs)), 'nodes': nodes,
            'evidence': [dict(e) for e in c.get('evidence', [])]}


def _rid(rid):
    m = re.match(r'^\s*R\s*-?\s*(\d{1,2})\s*([A-Z]?)\s*$', str(rid), re.I)
    return f'R-{int(m.group(1)):02d}{m.group(2).lower()}' if m else str(rid)


def rule_docs(rid):
    """규칙의 근거 문서 [{'file','doc','title','audience','as_of','label','quoted','scope'}].
    taxcalc.RULES 에서 원문을 글자 그대로 대조한 문서(quoted=True)를 먼저, 그다음 07 §5 근거 순.
    모르는 번호면 []. 'r-5'·'R5'·' R-07A ' 처럼 적어도 R-05·R-07a 로 읽는다."""
    o = load()
    r = o['rule_index'].get(_rid(rid))
    if not r:
        return []
    out = []
    for f in dict.fromkeys(r['quote_files'] + r['docs']):
        d = o['docs'].get(f, {})
        out.append({'file': f, 'doc': _doc_id(f), 'title': d.get('title', ''), 'audience': d.get('audience', ''),
                    'as_of': d.get('as_of', ''), 'label': d.get('label', ''),
                    'quoted': f in r['quote_files'], 'scope': d.get('scope', '')})
    return out


# ---------------------------------------------------------------------------
# 그래프 (시연 화면) — 노드 60개 이하로 요약
# ---------------------------------------------------------------------------
TYPE_LABEL = {'actor': '주체', 'plan': '제도', 'account': '계좌', 'fund': '재원', 'withdrawal': '인출 유형',
              'tax': '세목', 'product': '상품', 'event': '사건', 'alert': '알림', 'rule': '규칙', 'doc': '문서'}


def graph():
    """{'nodes': [{'id','label','type',...}], 'edges': [{'from','to','label'}], 'legend': [...]}.
    graph: true 인 개념 노드 + 규칙 15개 + 원문 대조된 근거 문서(남는 자리만큼)."""
    o = load()
    ctype = {c['id']: c['type'] for c in o['classes']}
    nodes = [{'id': n['id'], 'label': n['label'], 'type': ctype[n['class']]} for n in o['nodes'] if n.get('graph')]
    for r in o['rules']:
        nodes.append({'id': r['id'], 'label': f"{r['id']} {r['name']}", 'short': r['id'], 'type': 'rule',
                      'how': r['how'], 'summary': r['summary']})
    # 문서는 규칙 원문이 실제로 들어 있는 문서만, 많이 쓰이는 순으로 남는 자리까지
    room = max(0, GRAPH_MAX - len(nodes))
    docs = sorted((d for d in o['docs'].values() if d['quoted']),
                  key=lambda d: (-len(d['quoted']), -len(d['rules']), int(d['id'][3:])))[:room]
    for d in docs:
        nodes.append({'id': d['id'], 'label': d['id'], 'type': 'doc', 'title': d['title'],
                      'audience': d['audience'], 'as_of': d['as_of'], 'doc_label': d['label']})
    ids = {n['id'] for n in nodes}

    edges, seen = [], set()

    def add(a, b, label, **extra):
        if a in ids and b in ids and a != b and (a, b, label) not in seen:
            seen.add((a, b, label))
            edges.append(dict({'from': a, 'to': b, 'label': label}, **extra))

    for e in o['edges']:
        add(e['from'], e['to'], e['label'])
    for r in o['rules']:
        for t in r['applies_to']:
            add(r['id'], t, '적용된다')
        if any(o['attr_index'][c['attr']]['of'] == 'Actor' for c in r['conditions'] if c['attr'] in o['attr_index']):
            add(r['id'], 'actor.member', '조건이다')
    for d in docs:
        for rid in d['rules']:
            add(d['id'], rid, '근거다')
    # 재원 × 인출 유형 → 세목: 같은 (재원, 세목) 칸은 인출 유형을 이어 붙여 엣지 하나로
    pair = {}
    for c in o['tax_matrix']['cells']:
        if c['tax_node']:
            pair.setdefault((SRC_NODE[c['source']], c['tax_node']), []).append(c['mode'])
    for (a, b), modes in pair.items():
        add(a, b, '과세된다(' + '·'.join(modes) + ')')
    legend = [{'type': t, 'label': TYPE_LABEL[t]} for t in TYPE_LABEL if any(n['type'] == t for n in nodes)]
    return {'nodes': nodes, 'edges': edges, 'legend': legend}


# ---------------------------------------------------------------------------
# 질문이 지나는 길 (07 §7)
# ---------------------------------------------------------------------------
STEPS = ('개체 인식', '빠진 속성 확인', '요건 판정', '인출 순서', '세금 계산', '근거 연결', '응답')

_ACCT_RX = [('account.irp', r'IRP|개인형'), ('account.savings', r'연금저축'),
            ('account.dc', r'(?<![A-Za-z])DC(?![A-Za-z])|확정기여'), ('plan.db', r'(?<![A-Za-z])DB(?![A-Za-z])|확정급여'),
            ('plan.isa', r'ISA'), ('plan.old', r'\(구\)\s*개인연금|구\s*개인연금')]
_EVENT_RX = [('event.care', r'요양'), ('event.disaster', r'재난|천재지변'), ('event.rehab', r'파산|회생'),
             ('event.house', r'주택\s*(?:을\s*)?(?:구입|사)'), ('event.lease', r'전세|월세|전월세')]
_RULE_SUMMARY = {rid: summary for rid, _, _, summary, _ in taxcalc.RULEBOOK}


# ---- 입력 정리 — parse 결과가 JSON 을 거쳤거나(set·tuple → list) 화면에서 손으로 넣은 값이어도 깨지지 않게.
# 검토: 글자 금액('1800')·NaN 에서 예외, 음수 인출에 '-1억 9,995만 원', '0년차'를 그대로 써서 한도 ÷ 11 이 나왔다.
def _num(v, lo=0.0, hi=None, allow_lo=False):
    """숫자로 읽을 수 있고 범위 안이면 float, 아니면 None(빠진 값). bool·NaN·무한대·범위 밖은 버린다."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        try:
            v = float(v.replace(',', '').strip())
        except ValueError:
            return None
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    if v < lo or (v == lo and not allow_lo) or (hi is not None and v > hi):
        return None
    return float(v)


def _int(v, lo, hi):
    x = _num(v, lo, hi, allow_lo=True)
    return int(x) if x is not None and x == int(x) else None


def _age(a):
    """만 나이(int) · 연대 ('대', 60) · None. JSON 을 거친 ['대', 60] 도 받는다."""
    if isinstance(a, (tuple, list)) and len(a) == 2 and a[0] == '대':
        d = _int(a[1], 50, 90)
        return ('대', d) if d is not None and d % 10 == 0 else None
    return _int(a, 1, 120)


def _clean(parsed):
    """path_for 가 쓰는 값만 꺼내 모양·범위를 맞춘다. 0년차·0 이하 금액·NaN·글자는 '없음'으로 본다."""
    p = parsed if isinstance(parsed, dict) else {}
    out = {k: bool(p.get(k)) for k in ('early', 'unavoidable', 'future', 'old', 'retire_payout', 'year_assumed',
                                        'income_unknown_kind')}
    for k in ('withdraw', 'balance', 'isa', 'rtax'):
        out[k] = _num(p.get(k))
    out['retire_eff'] = _num(p.get('retire_eff'), 0.0, 100.0, allow_lo=True)
    out['year_n'] = _int(p.get('year_n'), 1, 99)            # 연금수령연차는 1년차부터 센다(doc39)
    out['actual_n'] = _int(p.get('actual_n'), 1, 99)
    out['year_assumed'] = out['year_assumed'] and out['year_n'] is not None
    out['age'] = _age(p.get('age'))
    inc = p.get('income')
    out['income'] = None
    if isinstance(inc, (tuple, list)) and len(inc) == 2 and inc[0] in ('총급여', '종합소득') and _num(inc[1]) is not None:
        out['income'] = (inc[0], _num(inc[1]))
    fund = p.get('fund') if isinstance(p.get('fund'), dict) else {}
    out['fund'] = {}
    for k in SRC_ORDER:
        v = _num(fund.get(k), allow_lo=True)
        if v is not None:
            out['fund'][k] = v
    fm = p.get('fund_mentioned')
    out['fund_mentioned'] = {x for x in fm if x in SRC_ORDER} if isinstance(fm, (set, frozenset, list, tuple)) else set()
    out['payout'] = []
    for x in p.get('payout') if isinstance(p.get('payout'), list) else []:
        if isinstance(x, dict) and _num(x.get('v')) is not None:
            out['payout'].append(dict(x, v=_num(x['v']), period=x.get('period') if x.get('period') in ('monthly', 'yearly') else None))
    out['contrib'] = []
    for c in p.get('contrib') if isinstance(p.get('contrib'), list) else []:
        if isinstance(c, dict) and c.get('acct') in ('savings', 'irp') and _num(c.get('v'), allow_lo=True) is not None:
            out['contrib'].append(dict(c, v=_num(c['v'], allow_lo=True)))
    out['isa_dest'] = p.get('isa_dest') if p.get('isa_dest') in ('savings', 'irp') else None
    return out


def _age_ok(age):
    """연금수령 나이 요건(R-04, 만 55세 이상)인가 → True / False / None(모름).
    나이 구간은 taxcalc 세율표로 판단한다 — 연금소득세율(R-07)이 있는 나이가 곧 연금수령 나이라 55를 여기 다시 적지 않는다."""
    if age is None:
        return None
    if isinstance(age, tuple):
        return True if taxcalc.rate_for_age(age) else None      # 50대는 55세 전후가 섞여 모른다
    return taxcalc.age_rate(age) is not None


def _no_limit(n):
    """연금수령연차 n 에 연금수령한도가 없는가(11년차부터, R-05). 기준 연차는 taxcalc.pension_limit 이 정한다.
    한도가 있는 해(10년차 이하)에는 실제수령연차도 10년차 이하다 — 퇴직소득세 감면 구간(R-09)이 한 칸으로 정해진다."""
    return n is not None and taxcalc.pension_limit(1.0, n) is None


def _age_txt(age):
    return f'{age[1]}대' if isinstance(age, tuple) else f'{age}세'


def _age_num(a):
    return a[1] if isinstance(a, tuple) else a


def _payout_yearly(p):
    return sum(x['v'] * (12 if x.get('period') == 'monthly' else 1) for x in (p.get('payout') or []))


def _context(p, q):
    """parse 결과에서 경로를 정하는 값들 — 의도, 재원, 인출 유형, 규칙 사슬."""
    age = p['age']
    w = p['withdraw']
    pays = p['payout']
    mentioned = set(p['fund_mentioned']) | set(p['fund'])
    early = bool(p['early'] or (_age_ok(age) is False and not p['future'] and (w is not None or pays)))
    if p['unavoidable'] and (w is not None or early or mentioned or pays):
        intent = 'unavoidable'
    elif early:
        intent = 'early'
    elif w is not None:
        intent = 'withdraw'
    elif pays:
        intent = 'payout'
    elif p['contrib'] or p['income'] or p['isa'] or p['income_unknown_kind']:
        intent = 'deduction'
    elif mentioned:
        intent = 'withdraw'
    else:
        intent = 'general'

    assumed = []
    if mentioned:
        sources = [s for s in SRC_ORDER if s in mentioned]
    elif intent == 'payout':
        sources = ['retire'] if p['retire_payout'] else ['credit']
        assumed.append(f'재원이 질문에 없어 {taxcalc.FUND_NAME[sources[0]]}으로 봄')
    elif intent in ('early', 'unavoidable'):
        sources = ['free', 'credit']          # 퇴직금 얘기가 없으면 ①·③ — ① 금액을 모르면 전부 ③(최대)
    elif intent == 'withdraw':
        sources = ['credit']
        assumed.append(f'재원 구성이 질문에 없어 전부 {taxcalc.FUND_NAME["credit"]}으로 봄')
    else:
        sources = []

    if intent == 'withdraw':
        rules = ['R-04', 'R-05', 'R-06']
        if 'retire' in sources:
            rules += ['R-09', 'R-10']
        if 'credit' in sources:
            rules += ['R-07', 'R-08', 'R-10']
    elif intent == 'payout':
        rules = ['R-04', 'R-07', 'R-08'] + (['R-09'] if 'retire' in sources else [])
    elif intent == 'early':
        rules = ['R-04', 'R-06', 'R-10', 'R-12']
    elif intent == 'unavoidable':
        rules = ['R-11', 'R-13', 'R-06']
    elif intent == 'deduction':
        rules = ['R-01', 'R-02'] + (['R-03'] if p['isa'] else [])
    else:
        rules = []
    rules = sorted(dict.fromkeys(rules))

    accounts = []
    if q:
        accounts = [nid for nid, rx in _ACCT_RX if re.search(rx, q, re.I)]
    else:
        d = p['isa_dest']                      # 질문에 계좌가 하나만 나오면 그 계좌(taxcalc.parse 규칙)
        if d:
            accounts.append('account.' + d)
        if p['isa']:
            accounts.append('plan.isa')
        if p['old']:
            accounts.append('plan.old')
    events = [nid for nid, rx in _EVENT_RX if q and re.search(rx, q)]
    if p['isa']:
        events.append('event.isa_maturity')
    return {'p': p, 'q': q, 'age': age, 'an': _age_num(age), 'w': w, 'pays': pays, 'intent': intent, 'early': early,
            'sources': sources, 'mentioned': mentioned, 'rules': rules, 'accounts': accounts,
            'events': events, 'assumed': assumed, 'need': []}


def _amounts(ctx):
    """재원별 금액·평가액을 채운다 — taxcalc.by_source 와 같은 규칙으로(어긋나면 경로 숫자와 답변 숫자가 달라진다).
      평가액이 없고 재원 금액이 다 있으면 평가액 = 합계 / 퇴직금만 있으면 평가액 = 퇴직금
      평가액이 있고 언급된 재원 하나만 금액이 없으면 뺄셈 / 55세 전 해지에 ① 금액만 알면 나머지는 ③
    → (f, B, 설명들). ctx['need'] 에 금액을 더 알아야 하는 재원을 적고, 채워서 생긴 재원을 ctx['sources'] 에 더한다.
    검토: 예전에는 언급된 재원만 배분해 '① 500만 원 + 2,000만 원 해지'를 세금 0원(잔액 부족)으로, '퇴직금 1,000만 원 +
    평가액 1억에서 1,800만 원'을 '잔액보다 800만 원 많음'으로 냈다(taxcalc 는 247만 5천 원 · 금액 보류)."""
    p, w, intent = ctx['p'], ctx['w'], ctx['intent']
    won, FN = taxcalc.won, taxcalc.FUND_NAME
    f = dict(p['fund'])
    B = p['balance']
    notes, need = [], []
    if intent not in ('withdraw', 'early', 'unavoidable'):
        return f, B, notes
    shown = set(ctx['sources'])                # 언급된 재원(없으면 질문 유형으로 정한 재원) — 경로에 보여 줄 재원
    srcs = set(shown)
    if intent != 'withdraw':
        # 해지·부득이 질문에 재원 얘기가 없으면 ①·③ 을 '보여 주기'만 한다 — 평가액을 ③ 으로 채우면
        # ① 을 0원으로 단정한 셈이라(taxcalc 는 '최대' 로만 낸다) 금액 채우기는 언급된 재원으로만 한다
        srcs = set(ctx['mentioned'])
    early = intent == 'early'
    if B is None and len(f) >= 2 and all(k in f for k in srcs):
        B = sum(f.values())
        notes.append(f'평가액 = 재원 합계 {won(B)}')
    elif B is None and set(f) == {'retire'} and srcs == {'retire'}:
        B = f['retire']                         # 'IRP에 퇴직금 1억 원 들어 있어요' — 다른 재원 얘기가 없다
        f['credit'] = 0.0
        notes.append(f'평가액 = 퇴직금 {won(B)}(다른 재원 없음)')
    if B is not None:
        known = sum(f.values())
        miss = [k for k in ('retire', 'credit') if k in srcs and k not in f]
        if len(miss) == 1 and B - known >= 0:
            f[miss[0]] = B - known
            if known:
                notes.append(f'{FN[miss[0]]} = 평가액 − 나머지 재원 = {won(f[miss[0]])}')
    free_given = 'free' in f
    if early and free_given and 'retire' not in srcs and 'credit' not in f:
        f['credit'] = max(0.0, (B if B is not None else w or 0.0) - f['free'])
        notes.append(f'나머지 {won(f["credit"])}은 {FN["credit"]}으로 봄')
    if not ctx['mentioned'] and intent == 'withdraw' and 'credit' not in f and w is not None \
            and _no_limit(p['year_n']):
        f['credit'] = w                         # 11년차부터 한도가 없어 평가액 없이도 '전부 ③' 전제로 계산된다
    if 'free' not in f:
        f['free'] = 0.0
        if 'free' not in srcs and ctx['mentioned'] and (f.get('retire') or f.get('credit')):
            notes.append(f'{FN["free"]}은 없다고 봄')
    if 'retire' not in f and 'retire' not in srcs and (free_given or f.get('credit')):
        f['retire'] = 0.0
    if not early and 'credit' not in f and f.get('retire') is not None and B is None and w is not None \
            and w <= f['retire'] + f['free']:
        f['credit'] = 0.0                       # 인출이 퇴직금 안에서 끝나면 ③ 금액은 몰라도 된다

    # 아직 모르는 재원 금액 — 인출 순서(R-06)상 실제로 쓰이는 것만
    if 'free' in ctx['mentioned'] and not free_given:
        need.append('free')                     # '공제 안 받은 돈도 있어요' — 금액을 모르면 0원으로 치지 않는다
    if 'retire' in srcs and f.get('retire') is None:
        need.append('retire')
    covered = w is not None and f.get('retire') is not None and w <= f['free'] + f['retire']
    if f.get('credit') is None and not covered and ('credit' in srcs or f.get('retire') is not None):
        need.append('credit')                   # 인출이 아는 재원(①·②)을 넘으면 ③ 금액이 있어야 배분된다
    if intent in ('early', 'unavoidable') and not ctx['mentioned']:
        need = ['free']                         # 재원 얘기가 없는 해지 — 공제 안 받은 돈만 알면 나머지는 ③
    elif intent == 'withdraw' and not ctx['mentioned']:
        need = []                               # '전부 ③' 전제 — ③ 금액은 평가액(빠지면 평가액을 묻는다)
    ctx['need'] = need
    ctx['sources'] = [s for s in SRC_ORDER if s in shown or f.get(s)]
    return f, B, notes


def _order_known(f, w):
    """인출 w 가 ①→②→③ 어디서 얼마 빠지는지 정해지는가(taxcalc.by_source 의 order_known 과 같은 조건)."""
    if f.get('free') is None or f.get('retire') is None:
        return False
    return f.get('credit') is not None or w <= f['free'] + f['retire']


def _attr_status(aid, ctx, f, B):
    """속성 하나 → ('have'|'derived'|'assumed'|'missing', 설명)."""
    p, won = ctx['p'], taxcalc.won
    a = load()['attr_index'][aid]
    if aid == 'attr.age':
        if ctx['age'] is None:
            return 'missing', ''
        if _age_ok(ctx['age']) is None and ctx['intent'] in ('withdraw', 'payout'):
            return 'missing', f"{_age_txt(ctx['age'])} — 만 55세 이상인지"
        return 'have', _age_txt(ctx['age'])
    if aid == 'attr.income':
        if p['income']:
            return 'have', f"{p['income'][0]} {won(p['income'][1])}"
        return 'missing', ''
    if aid == 'attr.contrib':
        if p['contrib']:
            s = sum(c['v'] for c in p['contrib'] if c['acct'] == 'savings')
            i = sum(c['v'] for c in p['contrib'] if c['acct'] == 'irp')
            return 'have', ' · '.join(([f'연금저축 {won(s)}'] if s else []) + ([f'IRP {won(i)}'] if i else [])) or '0원'
        if p['isa']:
            return 'assumed', '올해 다른 납입은 없다고 봄'
        return 'missing', ''
    if aid == 'attr.isa':
        return ('have', won(p['isa'])) if p['isa'] else ('missing', '')
    if aid == 'attr.balance':
        if p['balance'] is not None:
            return 'have', won(p['balance'])
        if B is not None:
            return 'derived', ''                # 어떻게 정했는지는 _amounts 의 설명(가정)에 이미 있다
        return 'missing', ''
    if aid == 'attr.year_n':
        n = p['year_n']
        if n is None:
            return 'missing', ''
        return ('assumed' if p['year_assumed'] else 'have'), f'{n}년차'
    if aid == 'attr.actual_n':
        if p['actual_n'] is not None:
            return 'have', f"{p['actual_n']}년차"
        n = p['year_n']
        if n is not None and not _no_limit(n):
            # 실제 수령한 해는 연금 개시가 가능해진 해보다 많을 수 없다 → 10년차 이하(30% 감면)
            return 'derived', f'연금수령연차 {n}년차라 10년차 이하로 봄'
        return 'missing', ''
    if aid == 'attr.retire_tax':
        if p['retire_eff'] is not None:
            return 'have', f"실효세율 {p['retire_eff']:g}%"
        if p['rtax'] is not None:
            return 'have', f"퇴직소득세 {won(p['rtax'])}"
        return 'missing', ''
    if aid == 'attr.fund_amount':
        if ctx['need']:
            return 'missing', ' · '.join(taxcalc.FUND_NAME[s] for s in ctx['need'])
        return 'have', ' · '.join(f'{taxcalc.FUND_NAME[s]} {won(f[s])}' for s in ctx['sources'] if f.get(s) is not None)
    if aid == 'attr.withdraw':
        if ctx['w'] is not None:
            return 'have', won(ctx['w'])
        if ctx['pays']:
            return 'have', f'연 {won(_payout_yearly(p))}'
        return 'missing', ''
    if aid == 'attr.reason':
        return ('have', '부득이한 사유') if p['unavoidable'] else ('missing', '')
    if a.get('assume'):
        return 'assumed', a['assume']
    return 'assumed', ''


def _needed(ctx):
    """규칙 사슬의 조건 속성(07 '조건이다') 중 이 질문에 필요한 것 → [(속성 id, 규칙 id)]."""
    o = load()
    p = ctx['p']
    out = []
    for rid in ctx['rules']:
        for c in o['rule_index'][rid]['conditions']:
            aid = c['attr']
            if c.get('waived_by') in ctx['sources']:
                continue                                   # 퇴직금이 있으면 가입기간 5년 요건 없음(R-04)
            if ctx['intent'] == 'early' and rid == 'R-04' and aid == 'attr.period':
                continue                                   # 55세 전이면 어차피 연금외수령
            if aid == 'attr.fund_amount' and len(ctx['sources']) < 2 and not ctx['need']:
                continue
            if aid == 'attr.retire_tax' and 'retire' not in ctx['sources']:
                continue
            if aid == 'attr.balance' and _no_limit(p['year_n']) and not ctx['need']:
                continue                                   # 11년차부터 한도가 없어 평가액이 필요 없다(R-05)
            if aid == 'attr.homeless' and 'event.house' not in ctx['events'] and 'event.lease' not in ctx['events']:
                continue
            if aid == 'attr.open_date' and (rid == 'R-15' or not p['year_assumed']):
                continue                                   # 개설일은 연차를 가정할 때만 밝힌다(2013.3.1 전이면 6년차부터)
            if aid == 'attr.actual_n' and ctx['intent'] not in ('withdraw', 'payout'):
                continue
            out.append((aid, rid))
    if ctx['intent'] in ('early', 'unavoidable') and not any(a == 'attr.fund_amount' for a, _ in out):
        out.append(('attr.fund_amount', 'R-06'))
    if ctx['intent'] in ('withdraw', 'early', 'unavoidable') and ctx['w'] is None and not ctx['pays']:
        out.append(('attr.withdraw', 'R-06'))              # '퇴직금 있는데 세금은?' — 얼마를 빼는지가 먼저다
    seen, uniq = set(), []
    for aid, rid in out:
        if aid not in seen:
            seen.add(aid)
            uniq.append((aid, rid))
    return uniq


def _calc(ctx, f, B):
    """숫자를 낼 수 있으면 taxcalc.withdraw_tax 결과, 아니면 None.
    인출 순서가 정해지고(빠진 재원 금액 없음) 요건·한도를 판정할 수 있을 때만 — taxcalc.by_source 와 같은 문턱."""
    p, w = ctx['p'], ctx['w']
    if w is None or ctx['intent'] not in ('withdraw', 'early') or ctx['need'] or not _order_known(f, w):
        return None
    eff = p['retire_eff']
    if eff is None and p['rtax'] is not None and f.get('retire'):
        eff = p['rtax'] / f['retire'] * 100           # 퇴직소득세 ÷ 퇴직금 = 실효세율(taxcalc.by_source 와 같은 환산)
    kw = dict(free=f.get('free') or 0.0, retire=f.get('retire') or 0.0, credit=f.get('credit') or 0.0, eff=eff,
              age=ctx['age'])
    if ctx['intent'] == 'early':
        return taxcalc.withdraw_tax(w, early=True, **kw)
    n = p['year_n']
    if n is None or (not _no_limit(n) and B is None) or _age_ok(ctx['age']) is not True:
        return None                                    # 한도·나이 요건(R-04·R-05)을 판정할 수 없다
    if p['actual_n'] is None and _no_limit(n) and f.get('retire'):
        return None                                    # 감면율(실제수령연차)을 모른다
    return taxcalc.withdraw_tax(w, balance=B, year_n=n, actual_n=p['actual_n'], **kw)


def _deduction_lines(p):
    """세액공제(R-01·R-02·R-03) → (줄들, calc 또는 None, 노드들). 금액은 taxcalc 공용 함수로만 낸다."""
    won, pct = taxcalc.won, taxcalc.pct
    lines, nodes, calc = [], ['R-01', 'R-02'], None
    s = sum(c['v'] for c in p['contrib'] if c['acct'] == 'savings')
    i = sum(c['v'] for c in p['contrib'] if c['acct'] == 'irp')
    rate = taxcalc.credit_rate(p['income'][1], p['income'][0]) if p['income'] else None
    if p['isa']:
        # ISA 만기 전환 연도 — 전환액도 일반 한도(연금저축 600·합산 900)를 함께 채우고, 그 위에 추가분(R-03).
        # 공제 대상은 실제로 넣은 돈(전환액 + 올해 납입)을 넘지 않는다 — taxcalc.isa_block(doc4 산식)과 같은 계산.
        isa, extra = p['isa'], taxcalc.isa_extra(p['isa'])
        by = []
        for d in ([p['isa_dest']] if p['isa_dest'] else ['savings', 'irp']):
            g = taxcalc.eligible(s + (isa if d == 'savings' else 0), i + (isa if d == 'irp' else 0))
            tot = min(g + extra, isa + s + i)
            line = (f"ISA 전환액을 {'연금저축계좌' if d == 'savings' else 'IRP'}로 옮기면 공제 대상 = 일반 한도분 {won(g)}"
                    f" + 추가분 {won(extra)}" + (f' = {won(tot)}' if tot == g + extra else f' → 실제 납입액 상한 {won(tot)}'))
            row = {'dest': d, 'eligible': won(tot)}
            if rate:
                row['refund'] = won(tot * rate / 100)
                line += f' → 세액공제 {won(tot)} × {pct(rate)} = {row["refund"]}'
            if isa + s + i > tot:
                line += f' · 공제 못 받는 {won(isa + s + i - tot)}은 나중에 꺼낼 때 비과세(① 과세제외금액)'
            lines.append(line)
            by.append(row)
        nodes += ['R-03', 'fund.free']
        if rate:
            calc = {'rate': pct(rate), 'by_dest': by}
            if len(by) == 1:
                calc.update(total=by[0]['refund'], eligible=by[0]['eligible'])
    elif p['contrib']:
        elig = taxcalc.eligible(s, i)
        if rate:
            refund = elig * rate / 100
            lines.append(f'세액공제 = 공제 대상 {won(elig)} × {pct(rate)} = {won(refund)}')
            calc = {'total': won(refund), 'eligible': won(elig), 'rate': pct(rate)}
        else:
            lines.append(f'공제 대상 {won(elig)}')
        if s + i > elig:
            lines.append(f'공제 못 받는 납입 {won(s + i - elig)}은 나중에 꺼낼 때 비과세(① 과세제외금액)')
            nodes.append('fund.free')
    else:
        lines.append('공제 대상은 올해 납입액을 받은 뒤 계산 (공제 대상 × 공제율)')
    if not rate:
        lines.append('세액공제액은 소득(총급여·종합소득금액)을 받은 뒤 계산 — 공제율이 소득으로 정해진다(R-02)')
    return lines, calc, nodes


def path_for(parsed, q=None):
    """질문이 온톨로지를 지나는 단계 [{'step','detail','nodes',...}] (07 §7).
    parsed = taxcalc.parse(q) 결과(질문 문자열을 그대로 줘도 된다). q 를 같이 주면 계좌·사건 이름을 글에서 읽는다.
    parsed 가 None 이고 q 만 있으면 q 를 parse 한다. 모양이 이상한 값은 빠진 값으로 본다(예외를 내지 않는다).
    '빠진 속성 확인' 단계에는 'missing'(되묻기 목록)·'assumed'(대신 채운 값)가 붙는다."""
    if isinstance(parsed, str):
        q, parsed = parsed, None
    q = q if isinstance(q, str) and q.strip() else None
    if parsed is None and q:
        parsed = taxcalc.parse(q)
    p = _clean(parsed)
    o = load()
    won = taxcalc.won
    ctx = _context(p, q)
    f, B, amount_notes = _amounts(ctx)
    intent, srcs = ctx['intent'], ctx['sources']
    name = lambda s: o['index'][SRC_NODE[s]]['label']      # noqa: E731 — 07 의 재원 이름

    # 1. 개체 인식
    ents, nodes1 = [], []
    if ctx['age'] is not None:
        ents.append('가입자.나이 = ' + (f"{ctx['an']}대" if isinstance(ctx['age'], tuple) else str(ctx['an'])))
        nodes1.append('actor.member')
    if p['income']:
        ents.append(f"가입자.{p['income'][0]} = {won(p['income'][1])}")
        nodes1.append('actor.member')
    if ctx['accounts']:
        ents.append('계좌 = ' + ' · '.join(o['index'][a]['label'] for a in ctx['accounts']))
        nodes1 += ctx['accounts']
    if ctx['mentioned']:
        ents.append('재원 = {' + ', '.join(name(s) for s in SRC_ORDER if s in ctx['mentioned']) + '}')
        nodes1 += [SRC_NODE[s] for s in SRC_ORDER if s in ctx['mentioned']]
    for s in SRC_ORDER:
        if s in p['fund']:
            ents.append(f'{name(s)}.금액 = {won(p["fund"][s])}')
    if p['balance'] is not None:
        ents.append(f"계좌.평가액 = {won(p['balance'])}")
    if p['year_n'] is not None:
        ents.append(f"계좌.연금수령연차 = {p['year_n']}" + (' (가정)' if p['year_assumed'] else ''))
    if p['retire_eff'] is not None:
        ents.append(f"재원.퇴직소득세 실효세율 = {p['retire_eff']:g}%")
    if ctx['w'] is not None:
        ents.append(f"인출 금액 = {won(ctx['w'])}" + (' (55세 전·해지)' if ctx['early'] else ''))
    if ctx['pays']:
        ents.append(f'연금 수령액 = 연 {won(_payout_yearly(p))}')
    if p['contrib']:
        ents.append('납입 = ' + _attr_status('attr.contrib', ctx, f, B)[1])
    if p['isa']:
        ents.append(f"ISA 만기 전환액 = {won(p['isa'])}")
    if ctx['events']:
        ents.append('사건 = ' + ' · '.join(o['index'][e]['label'] for e in ctx['events']))
        nodes1 += ctx['events']
    steps = [{'step': STEPS[0], 'detail': ' · '.join(ents) or '계산에 쓸 값이 질문에 없음',
              'nodes': list(dict.fromkeys(nodes1)), 'intent': intent}]

    # 2. 빠진 속성 확인 — 규칙 사슬의 조건 속성 중 질문에 없는 것
    missing, assumed, have = [], list(ctx['assumed']) + amount_notes, []
    for aid, rid in _needed(ctx):
        st, txt = _attr_status(aid, ctx, f, B)
        a = o['attr_index'][aid]
        lab = a['label'] + (f'({txt})' if st == 'missing' and txt else '')
        if st == 'missing':
            ask = a.get('ask', a['label'])
            if aid == 'attr.fund_amount' and txt:          # 어느 재원 금액이 빠졌는지 짚어서 묻는다
                ask = f'{txt} 금액' + (' (연말정산 때 공제받지 않은 납입액)' if ctx['need'] == ['free'] else '')
            elif aid == 'attr.age' and txt:
                ask = f"{a.get('ask', a['label'])} ({txt})"
            missing.append({'attr': aid, 'label': lab, 'ask': ask, 'rule': rid})
        elif st == 'assumed':
            assumed.append(f"{a['label']}: {txt}" if txt else a['label'])
        elif st == 'derived':
            if txt:
                assumed.append(f"{a['label']}: {txt}")
            have.append(a['label'])
        else:
            have.append(f"{a['label']}({txt})" if txt else a['label'])
    if missing:
        why = ' · '.join(dict.fromkeys(f"{m['rule']} {taxcalc.RULE_NAME.get(m['rule'], '')}" for m in missing))
        det = '없음: ' + ' · '.join(m['label'] for m in missing) + f' → 되묻기 (필요한 규칙: {why})'
    elif ctx['rules']:
        det = '필요한 값이 모두 있음' + (f" ({' · '.join(have)})" if have else '')
    else:
        det = '해당 규칙 없음 — 계산할 값이 없는 질문'
    steps.append({'step': STEPS[1], 'detail': det, 'nodes': [m['attr'] for m in missing],
                  'missing': missing, 'assumed': list(dict.fromkeys(assumed))})

    # 3. 요건 판정
    req, nodes3, lim = [], [], None
    n = p['year_n']
    if intent in ('withdraw', 'payout'):
        ok = _age_ok(ctx['age'])
        r4 = f"R-04: 만 55세 이상 {'✓' if ok else '✗' if ok is False else '(나이 확인 필요)'}" \
            + (f" ({_age_txt(ctx['age'])})" if ctx['age'] is not None else '')
        r4 += ' · 이연퇴직소득이 있어 가입 5년 요건 면제 ✓' if 'retire' in srcs else ' · 가입 5년 이상(질문에 없음 — 충족으로 봄)'
        req.append(r4)
        nodes3 += ['R-04', 'wd.pension']
        if intent == 'withdraw':
            if _no_limit(n):
                req.append(f'R-05: 연금수령연차 {n}년차 — 11년차부터는 연금수령한도가 없음')
            elif n is not None and B is not None:
                lim = taxcalc.pension_limit(B, n)
                t = f'R-05: 연금수령한도 = {won(B)} ÷ (11 − {n}) × 120% = {won(lim)}'
                if ctx['w'] is not None:
                    over = ctx['w'] - lim
                    t += (f' → 인출 {won(ctx["w"])} 중 {won(over)}은 한도 밖(연금외수령)' if over > 0
                          else f' → 인출 {won(ctx["w"])}은 한도 안')
                req.append(t)
            else:
                req.append('R-05: 한도 이내인가 — 평가액·연금수령연차가 있어야 계산할 수 있음')
            nodes3 += ['R-05']
    elif intent == 'early':
        req.append('R-04: 55세 전 인출·해지 → 연금수령 요건을 못 채워 연금외수령')
        req.append('R-12: 연금저축은 사유 없이 인출 가능, DC·IRP는 법정 사유가 있어야 중도인출(없으면 해지)')
        nodes3 += ['R-04', 'R-12', 'wd.other']
    elif intent == 'unavoidable':
        req.append('R-11: 부득이한 사유면 중도 인출이어도 연금소득세 5.5~3.3% (사유 확인 서류 필요)')
        req.append('R-13: 법정 사유 — ' + o['rule_index']['R-13']['condition'])
        nodes3 += ['R-11', 'R-13', 'wd.unavoidable'] + ctx['events']
    elif intent == 'deduction':
        # 기준 금액(5,500만·4,500만 …)은 RULEBOOK 요약에서 가져온다 — 판정은 taxcalc.credit_rate 가 한다
        req.append('R-01: ' + _RULE_SUMMARY['R-01'])
        if p['income']:
            kind, v = p['income']
            req.append(f"R-02: {kind} {won(v)} → 공제율 {taxcalc.pct(taxcalc.credit_rate(v, kind))} "
                       f"(기준: {_RULE_SUMMARY['R-02']})")
        else:
            req.append(f"R-02: 공제율은 소득으로 정해짐 — 소득이 질문에 없음 (기준: {_RULE_SUMMARY['R-02']})")
        nodes3 += ['R-01', 'R-02', 'account.savings', 'account.irp']
        if p['isa']:
            req.append(f"R-03: ISA 만기 전환 {won(p['isa'])} → 추가 공제 대상 {won(taxcalc.isa_extra(p['isa']))}"
                       f" ({_RULE_SUMMARY['R-03']})")
            nodes3 += ['R-03', 'plan.isa']
    # 상품 운용 — DC·IRP(퇴직연금)에 펀드·ETF 를 담는 질문이면 위험자산 한도(R-14)를 요건으로 짚는다
    if q and re.search(r'펀드|ETF|TDF|위험\s*자산|주식\s*형|주식\s*혼합|투자\s*신탁', q) and \
            ({'account.irp', 'account.dc'} & set(ctx['accounts']) or re.search(r'퇴직\s*연금', q)):
        req.append('R-14: ' + _RULE_SUMMARY['R-14'] + ' (적격 TDF 는 100%)')
        nodes3 += ['R-14', 'product.performance']
    steps.append({'step': STEPS[2], 'detail': ' / '.join(req) or '해당 없음', 'nodes': list(dict.fromkeys(nodes3))})

    # 4. 인출 순서 (R-06)
    res = _calc(ctx, f, B)
    if intent in ('withdraw', 'early', 'unavoidable') and srcs:
        chain = ' → '.join(name(s) for s in srcs)
        if res:
            rows = [f'{name(k)} {won(t)}' + (f' (한도 안 {won(i)} · 밖 {won(ov)})' if ov and i else
                                             ' (전액 연금외수령)' if ov else '')
                    for k, t, i, ov in res['rows']]
            det = 'R-06: ' + ' → '.join(rows) + (f' (계좌 잔액보다 {won(res["short"])} 많음)' if res['short'] else '')
        elif len(srcs) > 1:
            det = f'R-06: {chain} 순으로 빠짐 — 앞 재원에서 먼저 빠지고 남으면 다음으로 (고객이 고를 수 없음)'
        else:
            det = f'R-06: {chain}에서 빠짐'
        steps.append({'step': STEPS[3], 'detail': det, 'nodes': ['R-06'] + [SRC_NODE[s] for s in srcs]})
    else:
        steps.append({'step': STEPS[3], 'detail': '해당 없음' + (' — 납입·공제 질문' if intent == 'deduction' else ''),
                      'nodes': []})

    # 5. 세금 계산 — 재원 × 인출 유형 표(07 §6)로 세목을 정하고, 값이 다 있으면 taxcalc 로 금액까지
    tax_lines, nodes5, calc = [], [], None
    if intent in ('withdraw', 'payout', 'early', 'unavoidable'):
        if intent == 'early':
            modes = {s: ['연금외수령'] for s in srcs}
        elif intent == 'unavoidable':
            modes = {s: ['부득이'] for s in srcs}
        elif intent == 'payout' or _no_limit(n):
            modes = {s: ['연금수령'] for s in srcs}
        elif res:
            modes = {}
            for k, _, i, ov in res['rows']:
                modes[k] = (['연금수령'] if i else []) + (['연금외수령'] if ov else [])
        else:
            modes = {s: ['연금수령', '연금외수령'] for s in srcs}   # 한도를 몰라 둘 다
        for s in srcs:
            parts = []
            for m in modes.get(s, []):
                tr = tax_route(s, m)
                tag = {'연금수령': '한도 안', '연금외수령': '한도 밖'}.get(m) if intent == 'withdraw' and len(modes[s]) > 1 else None
                parts.append((f'{tag}: ' if tag else f'{m}: ') + tr['text'])
                nodes5 += tr['nodes']
            if parts:
                tax_lines.append(f'{name(s)} — ' + ' / '.join(parts))
        if res and res['total'] is not None:
            calc = {'total': won(res['total']), 'limit': won(res['limit']) if res['limit'] is not None else None,
                    'rows': [{'source': k, 'take': won(t), 'inside': won(i), 'over': won(ov)} for k, t, i, ov in res['rows']],
                    'lines': res['lines']}
            tax_lines.append(f'올해 세금 합계 = {calc["total"]} (taxcalc.withdraw_tax)')
        elif intent == 'early' and ctx['w'] is not None and 'free' not in p['fund'] and not f.get('retire') \
                and not f.get('credit') and 'retire' not in ctx['mentioned']:
            # ① 금액을 모르는 55세 전 해지 — 전부 ③ 으로 본 값이 최대 세금(taxcalc.by_source 와 같은 조건).
            # 퇴직금이 섞였으면 퇴직소득세가 사람마다 달라 최대도 말할 수 없다
            mx = taxcalc.withdraw_tax(ctx['w'], credit=ctx['w'], early=True)
            calc = {'total': won(mx['total']), 'limit': None, 'upper_bound': True, 'rows': [], 'lines': mx['lines']}
            tax_lines.append(f'공제 안 받은 돈을 모르면 — 전부 {taxcalc.FUND_NAME["credit"]}이라 볼 때(최대) '
                             f'{won(ctx["w"])} × 16.5% = {calc["total"]} (taxcalc.withdraw_tax)')
        elif missing:
            tax_lines.append('금액은 빠진 속성을 받은 뒤 계산')
        elif intent == 'payout':
            tax_lines.append('연 수령액의 세금은 답변의 계산 블록(taxcalc)이 낸다 — 여기서는 규칙 경로만')
    elif intent == 'deduction':
        tax_lines, calc, nodes5 = _deduction_lines(p)
    step5 = {'step': STEPS[4], 'detail': ' / '.join(tax_lines) or '해당 없음', 'nodes': list(dict.fromkeys(nodes5))}
    if calc:
        step5['calc'] = calc
    steps.append(step5)

    # 6. 근거 연결 — 규칙 → 원문 대조된 문서(꼬리표가 있으면 독자·기준일)
    used = list(dict.fromkeys(ctx['rules'] + [r for st in steps for r in st['nodes'] if re.match(r'^R-\d\d$', r)]))
    used = sorted(used)
    ev, nodes6 = [], []
    for rid in used:
        ds = rule_docs(rid)
        top = [d for d in ds if d['quoted']][:2] or ds[:1]
        ev.append(f"{rid} {taxcalc.RULE_NAME.get(rid, '')} → " + ', '.join(
            d['doc'] + (f" ({d['label']})" if d['label'] else '') for d in top))
        nodes6 += [rid] + [d['doc'] for d in top]
    steps.append({'step': STEPS[5], 'detail': ' / '.join(ev) or '해당 없음', 'nodes': list(dict.fromkeys(nodes6))})

    # 7. 응답 — 빠진 값이 있으면 되묻기, 아니면 결과·근거·가정·다음 행동
    if missing:
        det = '되묻기: ' + ' · '.join(m['ask'] for m in missing) + ' — 또는 가정을 밝히고 계산'
    elif intent == 'general':
        det = '규칙 설명(계산 없음) · 근거 문서'
    else:
        det = '결과 · 근거 · 가정' + (f"({' · '.join(steps[1]['assumed'])})" if steps[1]['assumed'] else '') + ' · 다음 행동'
        if intent == 'withdraw' and lim is not None and ctx['w'] is not None and ctx['w'] > lim:
            det += '(올해 한도까지 + 내년 나머지로 나눠 받는 경우와 비교)'
    steps.append({'step': STEPS[6], 'detail': det, 'nodes': []})
    return steps


# ---------------------------------------------------------------------------
# 시연 화면 '세금 지도' — 그래프(노드 60개) 대신 사람이 읽는 층 (10-13)
#   재원 3 × 인출 방식 3 = 9칸(07 §6 표, 계산 엔진이 쓰는 tax_matrix 그대로) + 생애주기 단계별 규칙 + 문서 제목.
#   문서 번호(doc39) 대신 제목·독자·기준일을 보인다. 숫자·세목은 여기서 새로 정하지 않는다.
# ---------------------------------------------------------------------------
MODE_INFO = {'연금수령': ('연금수령', '55세 이후 · 한도 안'), '부득이': ('부득이한 사유', '요양·파산 등'),
             '연금외수령': ('연금외수령', '55세 전 해지 · 한도 초과')}
# 단계 → 그 단계 질문에 쓰이는 규칙(RULEBOOK 번호). 단계와 규칙을 잇는 관계는 07 에 없어 화면용으로만 정한다
STAGE_RULES = {
    'stage.join': [], 'stage.pay': ['R-01', 'R-02', 'R-03'], 'stage.invest': ['R-14'], 'stage.transfer': ['R-15'],
    'stage.early': ['R-04', 'R-06', 'R-10', 'R-11', 'R-12', 'R-13'],
    'stage.payout': ['R-04', 'R-05', 'R-06', 'R-07', 'R-08', 'R-09'], 'stage.tax': ['R-07', 'R-08', 'R-09', 'R-10'],
    'stage.protect': [], 'stage.employer': [],
}
# 단계 → 세금 지도에서 함께 밝힐 인출 방식 열
STAGE_MODES = {'stage.early': ['부득이', '연금외수령'], 'stage.payout': ['연금수령'],
               'stage.tax': ['연금수령', '부득이', '연금외수령']}
# 칸마다 그 칸의 세금을 말하는 원문만 고르는 낱말 — 규칙 원문(taxcalc.RULES)에서 고른다
_CELL_WORDS = {('free', None): ('비과세', '과세되지'), ('retire', '연금수령'): ('70%', '감면'),
               ('retire', '연금외수령'): ('그대로', '100%'), ('credit', '연금수령'): ('5.5%', '1,500'),
               ('credit', '부득이'): ('5.5%', '저율'), ('credit', '연금외수령'): ('16.5%',)}
# 칸 머리 한 줄 — 각 칸의 plain(07 §6) 을 줄인 것. 새 세율을 만들지 않는다
_CELL_SHORT = {('retire', '연금수령'): '퇴직소득세 30~50% 감면', ('retire', '연금외수령'): '퇴직소득세 100%',
               ('credit', '연금수령'): '연금소득세 5.5~3.3%', ('credit', '부득이'): '연금소득세 5.5~3.3%',
               ('credit', '연금외수령'): '기타소득세 16.5%'}


def _brief(o, f):
    d = o['docs'].get(f) or {}
    return {'file': f, 'id': _doc_id(f), 'title': d.get('title') or _fallback_title(f) or _doc_id(f),
            'audience': d.get('audience', ''), 'as_of': d.get('as_of', ''), 'label': d.get('label', '')}


def overview():
    """{'sources','modes','cells','stages','docs'} — 시연 화면 '세금 지도'와 문서 제목 표."""
    o = load()
    tm = o['tax_matrix']
    cells = []
    for c in tm['cells']:
        words = _CELL_WORDS.get((c['source'], c['mode'])) or _CELL_WORDS.get((c['source'], None)) or ()
        quotes = []
        for rid in c['rule_ids']:
            for k in (o['rule_index'].get(rid) or {}).get('quote_keys', []):
                f, text = taxcalc.RULES[k]
                if any(w in text for w in words) and all(q['text'] != text for q in quotes):
                    quotes.append({'file': f, 'text': text, 'title': _brief(o, f)['title']})
        if not quotes:              # '문서 확인 필요' 칸 — 근거 후보(evidence)를 후보라고 밝혀 보인다
            quotes = [{'file': e['file'], 'text': e['text'], 'title': _brief(o, e['file'])['title'], 'candidate': True}
                      for e in c.get('evidence', [])]
        files = list(dict.fromkeys([q['file'] for q in quotes] + list(c['docs'])))
        short = '비과세' if c['tax'] == '비과세' else _CELL_SHORT.get((c['source'], c['mode']), c['tax'])
        cells.append({'source': c['source'], 'mode': c['mode'], 'tax': c['tax'], 'short': short, 'plain': c['plain'],
                      'rule_ids': list(c['rule_ids']), 'quotes': quotes[:3], 'docs': [_brief(o, f) for f in files][:4]})
    stages = [{'id': s['id'], 'label': s['label'], 'group': s.get('group', ''),
               'rule_ids': STAGE_RULES.get(s['id'], []), 'modes': STAGE_MODES.get(s['id'], []),
               'docs': [_brief(o, f) for f in s['docs']]} for s in o['stages']]
    return {'sources': [{'key': k, 'label': taxcalc.FUND_NAME[k]} for k in tm['sources']],
            'modes': [{'key': m['key'], 'label': MODE_INFO[m['key']][0], 'hint': MODE_INFO[m['key']][1]}
                      for m in tm['modes']],
            'cells': cells, 'stages': stages,
            'docs': {f: _brief(o, f) for f in sorted(o['docs'], key=lambda x: int(re.sub(r'\D', '', x) or 0))}}


_NODE_SRC = {v: k for k, v in SRC_NODE.items()}
_NODE_MODE = {v: k for k, v in MODE_NODE.items()}
_INTENT_STAGE = {'early': 'stage.early', 'withdraw': 'stage.payout', 'payout': 'stage.payout',
                 'deduction': 'stage.pay', 'isa': 'stage.pay', 'unavoidable': 'stage.early', 'invest': 'stage.invest'}


def question_cells(q):
    """질문이 세금 지도에서 지나는 칸 → {'cells': [[재원, 인출 방식], ...], 'stage', 'rule_ids', 'intent', 'steps'}.
    답변의 '온톨로지 경로'와 같은 path_for 의 단계 노드에서 읽는다. 모델을 부르지 않는다."""
    steps = path_for(None, q or '')
    nodes = list(dict.fromkeys(n for s in steps for n in s.get('nodes', [])))
    # '세금 계산' 단계의 노드는 [재원, (인출 유형), 세목, …, 재원, 세목 …] 순서다 — 재원마다 뒤따르는 세목과
    # 단계 안의 인출 유형으로 칸을 고른다(세목이 맞는 칸만: 07 §6 표의 tax_node)
    tax_step = next((s for s in steps if s['step'] == STEPS[4]), {'nodes': []})
    tnodes = tax_step.get('nodes', [])
    modes_all = [_NODE_MODE[n] for n in tnodes if n in _NODE_MODE]
    tax_of = {(c['source'], c['mode']): c['tax_node'] for c in load()['tax_matrix']['cells']}
    cells, cur, seg = [], None, []

    def flush():
        if cur:
            ms = [_NODE_MODE[n] for n in seg if n in _NODE_MODE] or modes_all
            taxes = {n for n in seg if str(n).startswith('tax.')}
            for m in dict.fromkeys(ms):
                if not taxes or tax_of.get((cur, m)) in taxes:
                    cells.append([cur, m])
    for n in tnodes:
        if n in _NODE_SRC:
            flush()
            cur, seg = _NODE_SRC[n], []
        else:
            seg.append(n)
    flush()
    intent = next((s.get('intent') for s in steps if s.get('intent')), '') or ''
    return {'cells': cells, 'stage': _INTENT_STAGE.get(intent, ''),
            'rule_ids': [n for n in nodes if re.match(r'^R-\d\d$', str(n))], 'intent': intent,
            'steps': [{'step': s['step'], 'detail': s['detail']} for s in steps]}
