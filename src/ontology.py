# -*- coding: utf-8 -*-
"""
연금 온톨로지(D) — 07_연금온톨로지.md 의 클래스·관계·규칙·재원×인출 표를 코드에서 쓴다.

왜 — 1차 멘토링에서 "연금 도메인 이해가 부족해 보인다"는 지적을 받았다. 답이 맞아도
'왜 그 세금인지'를 구조로 보여주지 못했다. 07 문서로 구조를 정리했고, 이 모듈은 그 구조를
시연 화면(그래프)과 상담 흐름(질문이 지나는 경로)에서 그대로 쓰게 한다.

원칙
  - 규칙 번호·이름은 taxcalc.RULEBOOK 과 같다(test_ontology 가 검사). 숫자는 taxcalc 공용 함수로만 낸다
    — 식을 여기 다시 쓰면 규칙이 바뀔 때 두 곳을 고쳐야 한다.
  - 07 에 없는 것은 넣지 않는다. 07 과 받은 문서가 다르면 문서를 따르고 07 글자를 남긴다(md07·md07_tax·meta.deviations)
    — 검수(10-15): '부득이 × 퇴직금'(07 '문서 확인 필요')은 doc21·doc5·doc1 로 풀었다.
  - 계좌·제도 종류를 먼저 본다(검수 10-15). ①②③ 재원 × 인출 방식 표는 연금계좌(IRP·연금저축) 규칙이다 —
    질문이 DB·DC·퇴직금제도·기업형IRP·(구)개인연금저축·ISA 만 말하면 ontology.json plan_rules(문서 원문 대조)로 답하고
    재원·세금 칸·나이 되묻기를 만들지 않는다(DB 해지 질문에 '55세 전 해지 → 연금외수령 16.5%'를 냈던 오류).
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
        if r.get('summary_add'):
            # 검수(10-15): RULEBOOK 요약만으로는 빠지는 조건(R-12 의 DB·DC, R-07 종신, R-11 6개월·한도 …) —
            # ontology.json 이 문서 원문(quotes)과 함께 덧붙인다(RULEBOOK 은 taxcalc 가 맡는다)
            r['summary'] = f"{summary} — {r['summary_add']}" if summary else r['summary_add']
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
    named = {(e['from'], e['to']) for e in o['edges']}      # 'R-12 —중도인출 불가→ DB' 처럼 이름 붙은 관계가 있으면 그것만
    for r in o['rules']:
        for t in r['applies_to']:
            if (r['id'], t) not in named:
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
    # ②·③ 이 같은 세목(연금소득세)으로 가도 세율 바탕이 다르다 — 엣지에 세율 기준을 붙여 ② 를 5.5~3.3% 로 읽지 않게
    # (9차 검수 906, ontology.json tax_matrix.edge_rates — 원문 대조됨)
    rate = {(r['from'], r['to']): r['rate'] for r in o['tax_matrix'].get('edge_rates', [])}
    for (a, b), modes in pair.items():
        add(a, b, '과세된다(' + '·'.join(modes) + (f" — {rate[(a, b)]}" if (a, b) in rate else '') + ')')
    # 노드마다 출처 — 아래 문서 줄(남는 자리만큼)에 없는 문서도 노드를 누르면 어느 문서 어느 문장에서 나왔는지 보인다(10-18)
    src = _sources(o)
    for n in nodes:
        if n['type'] != 'doc':
            s = src.get(n['id'], [])
            n['sources'], n['n_sources'] = s[:SRC_MAX], len(s)
    legend = [{'type': t, 'label': TYPE_LABEL[t]} for t in TYPE_LABEL if any(n['type'] == t for n in nodes)]
    return {'nodes': nodes, 'edges': edges, 'legend': legend}


SRC_MAX = 5     # 노드 하나에 보일 출처 수(나머지는 '외 N건')


def _sources(o):
    """{노드 id: [{'file','id','title','as_of','text','via'}]} — 노드가 나온 문서 원문.
    앞에 둔 것이 먼저 보인다: 규칙 노드는 자기 원문(RULEBOOK·ontology.json quotes) → 개념 노드 자체 원문 →
    이름 붙은 관계의 원문(원문이 없으면 그 관계의 규칙 원문 한 줄) → 세금 표 칸 원문 → 노드에 걸리는 규칙 원문 한 줄."""
    lab = {n['id']: n['label'] for n in o['nodes']}
    out = {}

    def put(nid, f, text, via):
        if not f or not text:
            return
        lst = out.setdefault(nid, [])
        if any(x['text'] == text for x in lst):
            return
        b = _brief(o, _file(f))
        lst.append({'file': b['file'], 'id': b['id'], 'title': b['title'], 'as_of': b['as_of'], 'text': text, 'via': via})

    def rule_quotes(rid):
        r = o['rule_index'].get(rid) or {}
        return [taxcalc.RULES[k] for k in r.get('quote_keys', []) if k in taxcalc.RULES] + \
            [(q['doc'], q['quote']) for q in r.get('quotes', []) if q.get('quote')]

    name = {r['id']: f"{r['id']} {r['name']}" for r in o['rules']}
    for r in o['rules']:
        for f, t in rule_quotes(r['id']):
            put(r['id'], f, t, f"{name[r['id']]} 원문")
    for n in o['nodes']:
        put(n['id'], n.get('doc'), n.get('quote'), '개념 정의')
        for q in n.get('quotes') or []:
            put(n['id'], q.get('doc'), q.get('quote'), '개념 정의')
    for e in o['edges']:
        via = f"{lab.get(e['from'], e['from'])} —{e['label']}→ {lab.get(e['to'], e['to'])}"
        qs = ([(e['doc'], e['quote'])] if e.get('doc') and e.get('quote') else []) + \
            [(q['doc'], q['quote']) for q in e.get('quotes') or [] if q.get('quote')]
        if not qs:
            rids = list(dict.fromkeys(([e['rule']] if e.get('rule') else []) + list(e.get('rules') or [])))
            qs = [rule_quotes(rid)[0] for rid in rids if rule_quotes(rid)]
            via += f" ({'·'.join(rids)})" if rids else ''
        for nid in (e['from'], e['to']):
            for f, t in qs:
                put(nid, f, t, via)
    for c, v in zip(o['tax_matrix']['cells'], overview()['cells']):
        via = f"세금 지도 {taxcalc.FUND_NAME[c['source']]} × {c['mode']} → {v['short']}"
        for q in v['quotes'][:1]:
            for nid in (SRC_NODE[c['source']], c['tax_node']):
                if nid:
                    put(nid, q['file'], q['text'], via)
    for r in o['rules']:
        rq = rule_quotes(r['id'])[:1]
        targets = list(r['applies_to'])
        if any(o['attr_index'][c['attr']]['of'] == 'Actor' for c in r['conditions'] if c['attr'] in o['attr_index']):
            targets.append('actor.member')
        for t in targets:
            for f, tx in rq:
                put(t, f, tx, name[r['id']])
    return out


# ---------------------------------------------------------------------------
# 질문이 지나는 길 (07 §7)
# ---------------------------------------------------------------------------
STEPS = ('개체 인식', '빠진 속성 확인', '요건 판정', '인출 순서', '세금 계산', '근거 연결', '응답')

# 계좌·제도 — 질문 글에서 읽는다. 검수(10-15): '개인연금저축'을 연금저축계좌로, '기업형 IRP'를 개인형 IRP로,
# '퇴직금제도'를 퇴직금(재원)으로 읽어 연금계좌 규칙(①②③ 재원·16.5%)을 잘못 붙였다.
_ACCT_RX = [('plan.irp_corp', r'기업형\s*IRP'),
            ('account.irp', r'(?<!기업형)(?<!기업형\s)IRP|개인형'),
            ('account.savings', r'(?<!개인)연금\s*저축'),
            ('account.dc', r'(?<![A-Za-z])DC(?![A-Za-z])|확정\s*기여'), ('plan.db', r'(?<![A-Za-z])DB(?![A-Za-z])|확정\s*급여'),
            ('plan.isa', r'ISA'),
            ('plan.old', r'\(구\)\s*개인연금|구\s*개인연금|개인연금\s*저축(?!\s*계좌)'),
            ('plan.severance', r'퇴직금\s*제도|중간\s*정산')]
# 연금계좌(소득세법 — doc20 '연금계좌(IRP·연금저축)') — ①②③ 재원 × 인출 방식 표(tax_matrix)가 적용되는 계좌
PENSION_ACCTS = ('account.irp', 'account.savings')
# 질문이 이 제도만 말하면(연금계좌를 말하지 않으면) 제도 규칙(plan_rules)으로 답한다 — 앞이 먼저
PLAN_SCOPES = ('plan.old', 'plan.db', 'plan.severance', 'account.dc', 'plan.irp_corp', 'plan.isa')
# 해외이주(세법상 부득이한 사유)는 「해외이주법」 해외이주신고확인서가 있어야 인정된다(doc20). 영주권·장기체류·'해외로 이사'는
# 그 자체로는 사유가 아니라(doc20 '단순 장기체류나 영주권 취득만으로는 부득이한 사유로 인정되지 않는다') 사건으로 읽지 않고,
# _EMIG_COND_RX 로 따로 잡아 '신고확인서가 있으면 부득이 / 없으면 일반 인출' 조건으로 보인다(9차 검수 908)
_EMIGRATE_RX = r'해외\s*이주|이민|(?:해외|외국|국외)(?:로|으로)?\s*이주'
_EMIG_COND_RX = r'영주권|장기\s*체류|(?:해외|외국|국외)(?:로|으로)?\s*이사'
_EVENT_RX = [('event.care', r'요양'), ('event.disaster', r'재난|천재지변'), ('event.rehab', r'파산|회생'),
             ('event.house', r'주택\s*(?:을\s*)?(?:구입|사|매입|마련)|집\s*(?:을\s*)?(?:사|구입|마련)'),
             ('event.lease', r'전세|월세|전월세|임차\s*보증금'),
             ('event.loan', r'담보\s*대출|대출\s*(?:원리금\s*)?(?:을\s*)?(?:상환|갚)'),
             ('event.emigrate', _EMIGRATE_RX), ('event.death', r'사망|돌아가(?:시|셨|신)|별세')]
# 세법상 부득이한 사유(doc1·doc5) — 이 사건 없이 '부득이' 낱말만 있고 주택·전세·담보대출이면 고객 전제가 틀린 것(doc20 표 X)
_UNAVOIDABLE_EVENTS = ('event.care', 'event.disaster', 'event.rehab', 'event.emigrate', 'event.death')
_NOT_UNAVOIDABLE_EVENTS = ('event.house', 'event.lease', 'event.loan')
_LIMIT_EVENTS = ('event.care', 'event.disaster')          # 연금소득세 적용 한도가 있는 사유(doc5)
_RULE_SUMMARY = {rid: summary for rid, _, _, summary, _ in taxcalc.RULEBOOK}

_EARLY_AGE_Q = re.compile(r'55\s*세\s*(?:되기\s*)?전|55\s*세도\s*안|55\s*세\s*미만')
_NOT_CANCEL_Q = re.compile(r'해지\s*(?:는\s*)?(?:하지\s*)?(?:말고|않고|안\s*하고|없이)')
_WD_VERB_Q = re.compile(r'중도\s*인출|중간\s*정산|해지|인출|빼|찾|꺼내|미리\s*(?:받|당겨)|깨(?:면|서|고)')
_STARTED_NO_Q = re.compile(r'개시\s*(?:를|을)?\s*(?:안|않|하지\s*않|신청\s*(?:을\s*)?(?:안|않|전|하기\s*전))|개시\s*(?:신청\s*)?전|개시하기\s*전')
_STARTED_YES_Q = re.compile(r'연금\s*(?:을|를)?\s*(?:받고\s*있|받는\s*중|수령\s*중)|개시\s*(?:를|을)?\s*(?:했|한\s*(?:뒤|후|다음))'
                            r'|개시\s*(?:이후|후)|지급\s*중|연금\s*(?:을\s*)?받은\s*지')
_PERIOD_Q = re.compile(r'가입(?:한\s*지|\s*기간(?:이|은)?|한\s*지는)?\s*(\d{1,2})\s*년(?!\s*차)|(\d{1,2})\s*년\s*(?:전에|째)\s*가입')
_ISA_DAYS_Q = re.compile(r'만기\D{0,8}?(\d{1,3})\s*(일|개월|달|년)\s*(?:이|가|이나|정도|쯤)?\s*(?:지났|넘었|지나|넘|경과|됐|되었|째|만에|후)')
_DAY_UNIT = {'일': 1, '개월': 30, '달': 30, '년': 365}
# 퇴직급여를 어디로·어떻게 받는지(퇴직 때) 묻는 말 — 연금계좌에 이미 넣어 둔 퇴직금 얘기면 아니다
_RETIRE_ROUTE_Q = re.compile(
    r'(?:퇴직금|퇴직\s*급여)\S*\s*(?:은|는|이|가|을|를)?\s*(?:어디|어느|어떤\s*계좌)'
    r'|어디로\s*(?:가|받|옮|이전|들어)'
    r'|(?:IRP|개인형)\S*\s*(?:로|으로)\s*(?:꼭|반드시|무조건)'
    r'|(?:이전|옮기|옮겨)\s*(?:안|않|하지\s*않)'
    r'|의무\s*(?:적으로\s*)?이전'
    r'|그냥\s*(?:받|일시금|현금)'
    r'|일시금\s*(?:으로)?\s*(?:받|수령|찾)'
    r'|(?:퇴직금|퇴직\s*급여)\S*\s*(?:은|는|을|를)?\s*(?:IRP|개인형|연금\s*저축|일반\s*계좌)\S*\s*(?:로|으로)\s*(?:받|수령|바로|직접)', re.I)
_RETIRE_EVENT_Q = re.compile(r'퇴직\s*(?:하면|할\s*때|하고|했는데|했어요|시에?|후에?)(?!\s*연금)')
_RETIRE_HELD_Q = re.compile(r'(?:IRP|개인형|연금\s*저축|연금\s*계좌)\S*\s*(?:에|에는|안에)?\s*(?:있는|들어\s*있|넣어\s*둔|넣은|이체한|옮긴|입금한|받아\s*둔)'
                            r'|(?:넣어\s*둔|들어\s*있는|이체한|옮긴|입금한)\s*퇴직금', re.I)
# 제도 이름 '퇴직금제도'의 '퇴직금'은 재원(②)이 아니다 — taxcalc 의 재원 언급 사전이 걸러 내지 못해 여기서 지운다
_RETIRE_WORD_Q = re.compile(r'퇴직금(?!\s*제도)|퇴직\s*급여(?!\s*제도)|이연\s*퇴직')
# 퇴직금을 연금계좌에 '넣을 수 있나'·'(계좌)로 받을 수 있나' — 입금 길을 묻는 말(9차 검수 910). '넣은·넣어 둔·이체한'은
# 이미 넣은 돈 얘기(_RETIRE_HELD_Q)라 빼고, 인출·연금 수령·공제 말이 함께 오면 그 갈래가 먼저다(_context)
_RETIRE_DEPOSIT_Q = re.compile(
    r'(?:퇴직금|퇴직\s*급여)(?!\s*제도)\S*\s*(?:(?:받은|나온|받을)\s*(?:거|것|돈|걸)\S*\s*)?'
    r'(?:(?:IRP|개인형|연금\s*저축|연금\s*계좌)\S*\s*)?'
    r'(?:넣(?!은|어\s*(?:둔|뒀|놓|놨))|입금(?!한|된|돼)|이체(?!한|된|돼)|옮기|옮길|옮겨(?!\s*(?:둔|뒀|놓|놨))|이전(?!한|된|돼))'
    r'|(?:퇴직금|퇴직\s*급여)(?!\s*제도)\S*\s*(?:IRP|개인형|연금\s*저축|연금\s*계좌)\S*\s*(?:로|으로)\s*(?:받|수령|바로|직접)', re.I)
_PAYOUT_WORD_Q = re.compile(r'연금\s*(?:으로|을|를)?\s*(?:받|수령)|나눠\s*(?:받|수령)')
# 'IRP를 연금저축으로 옮길 수 있나요' — 계좌끼리 옮기기(이체)는 퇴직금을 받는 길이 아니다
_ACCT_MOVE_Q = re.compile(r'(?:IRP|개인형|연금\s*저축|연금\s*계좌)\S*?(?:를|을|에서)\s*(?:IRP|개인형|연금\s*저축|연금\s*계좌)', re.I)

PLAN_INTENTS = ('plan_db', 'plan_dc', 'plan_severance', 'plan_irp_corp', 'retire_route', 'old', 'isa_only')


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


def _accounts(p, q):
    """질문의 계좌·제도 노드. 글(q)이 있으면 글에서, 없으면 parse 값에서."""
    accounts = []
    if q:
        accounts = [nid for nid, rx in _ACCT_RX if re.search(rx, q, re.I)]
    else:
        d = p['isa_dest']                      # 질문에 계좌가 하나만 나오면 그 계좌(taxcalc.parse 규칙)
        if d:
            accounts.append('account.' + d)
        if p['isa']:
            accounts.append('plan.isa')
    if p['old'] and 'plan.old' not in accounts:
        accounts.append('plan.old')            # '1998년에 가입한 개인연금' — parse 가 (구)개인연금저축으로 읽었다
    if 'plan.old' in accounts and q and not re.search(r'(?<!개인)연금\s*저축\s*계좌', q):
        accounts = [a for a in accounts if a != 'account.savings']
    return accounts


def _scope(accounts):
    """질문이 어느 규칙 묶음에 드는가 — 'pension'(연금계좌 IRP·연금저축) 또는 제도 노드 id.
    (구)개인연금저축은 연금계좌 이름이 함께 나와도 그 상품 규칙으로 본다(중도인출 불가·이자소득세)."""
    if 'plan.old' in accounts:
        return 'plan.old'
    if any(a in accounts for a in PENSION_ACCTS):
        return 'pension'
    return next((s for s in PLAN_SCOPES if s in accounts), 'pension')


def _q_period(q):
    m = _PERIOD_Q.search(q or '')
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    return n if 0 < n < 100 else None


def _q_started(q):
    if _STARTED_NO_Q.search(q or ''):
        return False
    if _STARTED_YES_Q.search(q or ''):
        return True
    return None


def _q_isa_days(q):
    m = _ISA_DAYS_Q.search(q or '')
    return int(m.group(1)) * _DAY_UNIT[m.group(2)] if m else None


def _context(p, q):
    """parse 결과에서 경로를 정하는 값들 — 의도, 재원, 인출 유형, 규칙 사슬.
    검수(10-15): 계좌·제도 종류를 보지 않고 '해지·중도인출' 낱말만으로 의도를 정해, DB 를 개인 연금계좌처럼
    해지 세금(① 비과세·③ 16.5%)으로 계산하고 나이·재원을 되물었다. 이제 계좌 종류(scope)를 먼저 본다."""
    age = p['age']
    w = p['withdraw']
    pays = p['payout']
    qq = q or ''
    o = load()
    mentioned = set(p['fund_mentioned']) | set(p['fund'])
    if q and 'retire' in mentioned and 'retire' not in p['fund'] and not _RETIRE_WORD_Q.search(q):
        mentioned.discard('retire')            # '퇴직금제도 회사 다니는데' — 제도 이름이지 IRP 안의 퇴직금이 아니다
    accounts = _accounts(p, q)
    events = [nid for nid, rx in _EVENT_RX if q and re.search(rx, q)]
    if p['isa']:
        events.append('event.isa_maturity')
    scope = _scope(accounts)
    age_ok = _age_ok(age)
    period = _q_period(q)
    started = _q_started(q)
    isa_days = _q_isa_days(q) if p['isa'] else None
    min_years = o['attr_index']['attr.period'].get('min_years')
    wd_verb = bool(p['early'] or w is not None or _WD_VERB_Q.search(qq))
    unavoidable = p['unavoidable']
    wd_word = wd_verb or unavoidable
    # 영주권·장기체류·해외 이사 — 해외이주신고확인서가 있어야 부득이한 사유(doc20). 진짜 부득이 사건이 따로 없으면
    # 부득이 여부를 '모름'으로 두고 부득이 칸과 일반 인출 칸을 함께 보이며 신고확인서를 묻는다(taxcalc 가 부득이로 읽어도)
    emig_cond = bool(q and re.search(_EMIG_COND_RX, q) and not any(e in events for e in _UNAVOIDABLE_EVENTS)
                     and not any(e in events for e in _NOT_UNAVOIDABLE_EVENTS))
    if emig_cond and wd_verb:
        unavoidable = True
    premise = False                            # '주택 구입은 부득이한 사유라서 5.5%죠?' — 고객 전제가 틀림(doc20)
    if unavoidable and q and not any(e in events for e in _UNAVOIDABLE_EVENTS) \
            and any(e in events for e in _NOT_UNAVOIDABLE_EVENTS) and not re.search(r'영업\s*정지', q):
        unavoidable, premise = False, True
    pension_named = any(a in accounts for a in PENSION_ACCTS)
    retire_route = bool(q and re.search(r'퇴직', q) and not _RETIRE_HELD_Q.search(q) and not pays and not p['contrib']
                        and (_RETIRE_ROUTE_Q.search(q) or (_RETIRE_EVENT_Q.search(q) and not pension_named))
                        and (scope in ('plan.db', 'account.dc', 'plan.severance')
                             or ({'plan.db', 'account.dc', 'plan.severance'} & set(accounts))
                             or (not accounts and _RETIRE_WORD_Q.search(q))))
    # '연금저축에 퇴직금 넣을 수 있어요?' — 연금계좌 이름이 있어도 입금 길(doc51 표)을 묻는 말이다. 예전에는 '퇴직금' 언급만으로
    # 인출(withdraw) 갈래로 가 ② 연금수령·연금외수령 칸과 평가액·연차 되묻기를 냈다(9차 검수 910)
    if not retire_route and q and scope == 'pension' and _RETIRE_WORD_Q.search(q) and _RETIRE_DEPOSIT_Q.search(q) \
            and not _RETIRE_HELD_Q.search(q) and not wd_verb and not unavoidable and not pays and not p['contrib'] \
            and not _PAYOUT_WORD_Q.search(q) and not _ACCT_MOVE_Q.search(q) and not re.search(r'공제|환급|연말\s*정산', q):
        retire_route = True

    early_reason = None
    early = False
    intent = None
    if retire_route:
        intent = 'retire_route'
    elif scope == 'plan.old':
        intent = 'old'
    elif scope == 'plan.db':
        intent = 'plan_db' if (wd_word or unavoidable or premise) else ('payout' if pays else None)
    elif scope == 'account.dc':
        if wd_word or unavoidable or premise or (re.search(r'세금|과세|세율|운용\s*수익', qq) and not pays):
            intent = 'plan_dc'                  # DC 중도인출·운용수익 과세는 DC 표(doc55·doc42) — 연금계좌 표가 아니다
        elif pays:
            intent = 'payout'
        elif re.search(r'공제|납입|넣', qq):
            intent = 'deduction'                # DC 가입자부담금도 연금저축과 합산 900만 원(doc55)
    elif scope == 'plan.severance':
        intent = 'plan_severance' if wd_word or re.search(r'중간\s*정산', qq) else None
    elif scope == 'plan.irp_corp':
        intent = 'plan_irp_corp' if wd_word else None
    elif scope == 'plan.isa' and wd_word and not p['isa'] and not p['contrib']:
        intent = 'isa_only'                     # ISA 는 연금계좌가 아니다 — 55세 요건·①②③ 재원·16.5% 를 쓰지 않는다

    cancel = bool(p['early'] and not (q and _NOT_CANCEL_Q.search(q) and not _EARLY_AGE_Q.search(q)))
    if intent is None:
        # 연금계좌(IRP·연금저축) — 55세 전·개시 전·가입 5년 미만이면 연금외수령, 55세 이후 해지는 개시 여부·한도로 갈린다
        deduction_like = bool(p['contrib'] or p['income'] or p['isa'] or p['income_unknown_kind'])
        if q and _EARLY_AGE_Q.search(q):
            early_reason = 'hard'
        elif age_ok is False and not p['future'] and (w is not None or pays or (mentioned and not deduction_like)
                                                       or cancel):
            early_reason = 'age'
        elif started is False and (w is not None or wd_word):
            early_reason = 'prestart'
        elif period is not None and min_years and period < min_years and 'retire' not in mentioned \
                and (w is not None or wd_word):
            early_reason = 'period'
        elif cancel and age_ok is not True:
            early_reason = 'cancel'             # 나이를 모른다 — 55세 전이면 연금외수령(조건으로 밝힌다)
        elif premise:
            early_reason = 'premise'
        early = early_reason is not None
        if unavoidable and (w is not None or early or mentioned or pays or wd_verb):
            intent = 'unavoidable'
        elif early:
            intent = 'early'
        elif w is not None:
            intent = 'withdraw'
        elif pays:
            intent = 'payout'
        elif deduction_like:
            intent = 'deduction'
        elif mentioned:
            intent = 'withdraw'
        elif cancel or (q and _NOT_CANCEL_Q.search(q) and _WD_VERB_Q.search(q)):
            intent = 'withdraw'                 # 55세 이후 해지 · '해지 말고 일부만 인출' — 한도·개시 여부로 갈린다
        else:
            intent = 'general'
    if intent in PLAN_INTENTS:
        early = False

    # R-13(근퇴법 중도인출 사유)이 이 질문에 걸리는가 — 규칙 사슬·무주택 여부 가정·요건 판정 문장이 같은 판단을 쓴다.
    # IRP 일 수 있고(IRP 를 말했거나 계좌를 말하지 않음) 근퇴법 사유 사건이 있을 때. 부득이 갈래는 사건이 없어도 사유 목록을
    # 보이되, 사망·해외이주(·영주권 등)만 있으면 근퇴법 사유가 아니라 R-13 을 붙이지 않는다(doc20 '사망·해외이주는 중도인출 사유가 아니다')
    irp_possible = 'account.irp' in accounts or not pension_named
    r13_ev = any(e in events for e in _R13_EVENTS)
    emig_any = bool({'event.emigrate', 'event.death'} & set(events)) or emig_cond
    if intent == 'early':
        r13 = irp_possible and r13_ev
    elif intent == 'unavoidable':
        r13 = irp_possible and (r13_ev or not emig_any)
    else:
        r13 = False

    assumed = []
    if intent in PLAN_INTENTS:
        sources = []
    elif mentioned:
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
    if intent == 'payout' and scope in ('plan.db', 'account.dc'):
        assumed.append('퇴직급여를 퇴직 때 IRP 등 연금계좌로 옮겨 연금으로 받는다고 봄 — 연금계좌 세금 규칙(doc14·doc51)')

    if intent == 'withdraw':
        rules = ['R-04', 'R-05', 'R-06']
        if 'retire' in sources:
            rules += ['R-09', 'R-10']
        if 'credit' in sources:
            rules += ['R-07', 'R-08', 'R-10']
    elif intent == 'payout':
        # R-07·R-08 은 ③ 재원 규칙 — 퇴직금(②)만 받는 연금은 1,500만 원 판단에서 빠진다(doc21)
        rules = ['R-04'] + (['R-07', 'R-08'] if 'credit' in sources else []) + (['R-09'] if 'retire' in sources else [])
    elif intent == 'early':
        # 근퇴법 중도인출 사유(R-13)는 IRP(·DC) 규칙이다 — 연금저축만 말한 질문에는 붙이지 않는다(doc20 표 '사유무관 중도인출
        # 가능', 9차 검수 903). '세법상 부득이한 사유가 아님'(R-11)은 요건 판정 문장·근거로만 잇는다 — 규칙 사슬에 넣으면
        # R-11 조건(인출 사유)을 되묻게 되는데 사유(주택 구입 등)는 이미 질문에 있다
        rules = ['R-04', 'R-06', 'R-10', 'R-12'] + (['R-13'] if r13 else [])
    elif intent == 'unavoidable':
        rules = ['R-11', 'R-06', 'R-12'] + (['R-13'] if r13 else [])
    elif intent == 'deduction':
        rules = ['R-01', 'R-02'] + (['R-03'] if p['isa'] else [])
    elif intent in ('plan_db', 'plan_dc', 'plan_irp_corp'):
        rules = ['R-12'] + (['R-13'] if intent == 'plan_dc' else [])
    elif intent == 'old':
        rules = ['R-15', 'R-12']
    elif intent == 'retire_route':
        rules = ['R-09', 'R-10']
    else:
        rules = []
    rules = sorted(dict.fromkeys(rules))
    return {'p': p, 'q': q, 'age': age, 'an': _age_num(age), 'w': w, 'pays': pays, 'intent': intent, 'early': early,
            'early_reason': early_reason, 'wd_verb': wd_verb, 'cancel': cancel, 'scope': scope, 'started': started, 'period': period,
            'isa_days': isa_days, 'premise': premise, 'unavoidable_q': unavoidable, 'r13': r13, 'emig_cond': emig_cond,
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
    if aid == 'attr.period':
        n = ctx.get('period')
        if n is not None:
            return 'have', f'가입 {n}년'
        return 'assumed', a.get('assume', '')
    if aid == 'attr.started':
        s = ctx.get('started')
        if s is not None:
            return 'have', '연금 개시 신청 후' if s else '연금 개시 신청 전'
        if ctx.get('cancel'):
            return 'missing', ''                # 55세 이후 해지 — 개시 전이면 전액 연금외수령, 개시 후면 한도까지 연금소득세(doc1·doc5)
        return 'assumed', a.get('assume', '')
    if aid == 'attr.isa_days':
        d = ctx.get('isa_days')
        return ('have', f'만기 후 {d}일쯤') if d is not None else ('assumed', a.get('assume', ''))
    if aid in ('attr.has_retire', 'attr.care_cost', 'attr.retire_3y', 'attr.emigrate_proof'):
        return 'missing', ''                    # 한도(doc5)·3년(doc1)·퇴직금 여부·해외이주신고확인서(doc20) — 가정하지 않고 묻는다
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
            if aid == 'attr.started' and ctx['intent'] not in ('withdraw', 'payout'):
                continue                                   # 55세 전·개시 전·가입 5년 미만은 이미 연금외수령으로 정해졌다
            if aid == 'attr.care_cost' and not any(e in ctx['events'] for e in _LIMIT_EVENTS):
                continue                                   # 한도는 요양·재난피해에만(doc5)
            if aid == 'attr.reason' and ctx.get('emig_cond'):
                continue                                   # 사유(영주권·장기체류 등)는 질문에 있다 — 묻는 것은 신고확인서(doc20)
            if aid == 'attr.retire_3y' and not (('event.emigrate' in ctx['events'] or ctx.get('emig_cond'))
                                                and 'retire' in ctx['sources']):
                continue                                   # 해외이주 + 퇴직금일 때만(doc1·doc20)
            out.append((aid, rid))
    if ctx['intent'] == 'unavoidable' and ctx.get('emig_cond'):
        out.insert(0, ('attr.emigrate_proof', 'R-11'))     # 영주권·장기체류·해외 이사 — 신고확인서가 있어야 부득이(doc20)
    if ctx['intent'] in ('early', 'unavoidable') and not any(a == 'attr.fund_amount' for a, _ in out):
        out.append(('attr.fund_amount', 'R-06'))
    if ctx['intent'] in ('early', 'unavoidable') and not ctx['mentioned']:
        out.append(('attr.has_retire', 'R-06'))            # IRP·연금저축에는 퇴직금이 있을 수 있다(doc14·doc51) — 없다고 단정하지 않는다
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
            # 다른 납입을 모르면 전환액이 일반 한도까지 채운다고 본 값이다 — 가정을 줄에 밝힌다(9차 검수 909, doc4 산식)
            line = (f"ISA 전환액을 {'연금저축계좌' if d == 'savings' else 'IRP'}로 옮기면"
                    + ('' if p['contrib'] else '(올해 다른 납입이 없다고 볼 때)')
                    + f" 공제 대상 = 일반 한도분 {won(g)} + 추가분 {won(extra)}"
                    + (f' = {won(tot)}' if tot == g + extra else f' → 실제 납입액 상한 {won(tot)}'))
            row = {'dest': d, 'eligible': won(tot)}
            if rate:
                row['refund'] = won(tot * rate / 100)
                line += f' → 세액공제 {won(tot)} × {pct(rate)} = {row["refund"]}'
            if isa + s + i > tot:
                line += f' · 공제 못 받는 {won(isa + s + i - tot)}은 나중에 꺼낼 때 비과세(① 과세제외금액)'
            lines.append(line)
            by.append(row)
        if not p['contrib']:
            # 한도 금액은 RULEBOOK(R-01)이 원본 — 여기 숫자를 다시 적지 않는다
            lines.append('다른 납입으로 일반 공제 한도(R-01)를 이미 채웠다면 전환액 중 공제 대상은 추가분(R-03)만 — '
                         '전환액에서 세액공제대상금액을 뺀 나머지는 ① 비과세(doc4·doc23)')
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


_W_TAG = {'hard': ' (55세 전 인출·해지)', 'age': ' (55세 전 인출·해지)', 'prestart': ' (연금 개시 신청 전)',
          'cancel': ' (해지·중도인출 — 나이 확인 필요)'}
_R13_EVENTS = ('event.house', 'event.lease', 'event.care', 'event.rehab', 'event.disaster', 'event.loan')
_INTENT_NOTE = {
    'plan_db': 'DB는 중도인출·해지가 없는 제도라 연금계좌 세금 지도(①②③ 재원 × 인출 방식) 밖이에요',
    'plan_dc': 'DC 중도인출 세금은 연금계좌 세금 지도가 아니라 DC 표(퇴직금 퇴직소득세 / 가입자부담금 사유별)를 따라요',
    'plan_severance': '퇴직금제도 중간정산은 연금계좌 세금 지도 밖이에요',
    'plan_irp_corp': '기업형 IRP는 회사를 거쳐 신청 — 받은 문서에 세금 규칙이 따로 없어 세금 지도 칸을 표시하지 않아요',
    'retire_route': '퇴직급여를 받는 길(IRP 의무이전·일반계좌 등)이라 세금 지도 칸은 연금계좌(IRP·연금저축)에 넣은 뒤부터예요',
    'old': '(구)개인연금저축은 연금저축계좌와 다른 상품이라 세금 지도(연금계좌 표) 밖이에요',
    'isa_only': 'ISA는 연금계좌가 아니라 세금 지도 밖이에요',
}


def _irp_full(ctx):
    """IRP 에서 일부만 꺼낼 수 없는 경우인가 — 사유 없는 인출(doc14 FAQ '전체 해지 시에만 인출이 가능'), 사망·해외이주(doc20).
    55세가 넘어도 연금 개시 전·가입 5년 미만(IRP 연금수령요건 미충족, doc22)이면 사유 없이는 일부 인출이 아니라 전액 해지다
    (9차 검수 904 — 예전에는 55세 전('age')만 전액 해지로 봐 '60세 개시 전 1,000만 원 인출'을 일부 인출로 계산했다)."""
    acc, ev = ctx['accounts'], set(ctx['events'])
    if 'account.irp' not in acc or 'account.savings' in acc:
        return False
    if ctx['intent'] == 'early' and ctx['early_reason'] in ('hard', 'age', 'cancel', 'prestart', 'period'):
        return not (ev & set(_R13_EVENTS))
    if ctx['intent'] == 'unavoidable':
        return bool(ev & {'event.emigrate', 'event.death'} or ctx.get('emig_cond')) and not (ev & set(_R13_EVENTS))
    return False


def _irp_over(ctx):
    """전액 해지만 되는 IRP 인데 평가액이 인출 금액보다 크다 — 그 금액만 빼는 계산은 없다(해지하면 평가액 전액)."""
    p, w = ctx['p'], ctx['w']
    return bool(ctx.get('irp_full') and w is not None and p['balance'] is not None and w < p['balance'])


def _entities(ctx, p, o, f, B, plan=False):
    """1단계 개체 인식 → (글, 노드). plan=True(연금계좌가 아닌 제도 질문)면 퇴직금을 ② 이연퇴직소득으로 부르지 않는다 —
    회사 제도(DB·DC·퇴직금제도) 안의 퇴직급여는 연금계좌로 옮기기 전이라 이연퇴직소득이 아니다(doc39 '연금계좌로 이전한 퇴직금')."""
    won = taxcalc.won
    name = lambda s: o['index'][SRC_NODE[s]]['label']      # noqa: E731 — 07 의 재원 이름
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
    if plan:
        if 'retire' in ctx['mentioned']:
            ents.append('퇴직금 = 회사 퇴직급여(연금계좌로 옮기기 전 — ② 이연퇴직소득 아님)'
                        + (f" {won(p['fund']['retire'])}" if 'retire' in p['fund'] else ''))
            nodes1.append('event.retire')
    else:
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
    if ctx.get('period') is not None:
        ents.append(f"계좌.가입기간 = {ctx['period']}년")
    if ctx.get('started') is not None:
        ents.append('계좌.연금 개시 = ' + ('신청 후' if ctx['started'] else '신청 전'))
    if p['retire_eff'] is not None:
        ents.append(f"재원.퇴직소득세 실효세율 = {p['retire_eff']:g}%")
    if ctx['w'] is not None:
        reason = ctx.get('early_reason') if ctx['intent'] == 'early' else None
        tag = _W_TAG.get(reason or '', '')
        if reason == 'period':
            tag = f" (가입 {ctx['period']}년)"
        elif ctx['intent'] == 'withdraw' and ctx.get('cancel'):
            tag = ' (55세 이후 해지)'
        if _irp_over(ctx):
            tag += ' — IRP는 일부만 꺼낼 수 없어 해지하면 평가액 전액'
        ents.append(f"{'해지 금액(IRP 전액)' if ctx.get('irp_full') and not _irp_over(ctx) else '인출 금액'} = {won(ctx['w'])}" + tag)
    if ctx['pays']:
        ents.append(f'연금 수령액 = 연 {won(_payout_yearly(p))}')
    if p['contrib']:
        ents.append('납입 = ' + _attr_status('attr.contrib', ctx, f, B)[1])
    if p['isa']:
        ents.append(f"ISA 만기 전환액 = {won(p['isa'])}")
    if ctx['events']:
        ents.append('사건 = ' + ' · '.join(o['index'][e]['label'] for e in ctx['events']))
        nodes1 += ctx['events']
    if ctx.get('emig_cond'):
        # '사건 = 해외이주'로 단정하지 않는다 — 신고확인서가 있어야 해외이주(doc20)
        said = re.search(_EMIG_COND_RX, ctx['q'] or '')
        ents.append(f"해외 거주 = {said.group(0) if said else '영주권·장기체류'} (해외이주신고 여부 모름 — 그 자체로는 부득이한 사유 아님)")
    return ents, list(dict.fromkeys(nodes1))


def _r12_lines(ctx, o):
    """연금계좌 경로의 R-12 문장 — 질문의 계좌 종류에 맞는 것만(ontology.json R-12 by_plan, 원문 대조됨)."""
    by = o['rule_index']['R-12'].get('by_plan') or {}
    accs = [a for a in PENSION_ACCTS if a in ctx['accounts']] or list(PENSION_ACCTS)
    out = []
    for a in accs:
        if a == 'account.irp' and ctx.get('early_reason') in ('prestart', 'period'):
            # 55세가 넘었어도 개시 전·가입 5년 미만 — 'IRP는 55세 전에는…' 문장으로는 설명되지 않는다(9차 검수 904)
            out.append('R-12: ' + o['plan_rules']['pension']['irp_prestart'])
        elif a in by:
            out.append('R-12: ' + by[a]['text'])
    return out


def _evidence(rule_ids, extra_docs=()):
    """근거 연결 → (글들, 노드). 제도 규칙 원문(extra_docs)이 있으면 그것을 먼저 — 규칙의 대표 원문은 연금계좌 문서일 수 있다."""
    ev, nodes6 = [], []
    extra = list(dict.fromkeys(extra_docs))
    if extra:
        ev.append('제도 규칙 원문 → ' + ', '.join(extra))
        nodes6 += extra
    for rid in sorted(dict.fromkeys(rule_ids)):
        ds = rule_docs(rid)
        top = [d for d in ds if d['quoted']][:2] or ds[:1]
        ev.append(f"{rid} {taxcalc.RULE_NAME.get(rid, '')} → " + ', '.join(
            d['doc'] + (f" ({d['label']})" if d['label'] else '') for d in top))
        nodes6 += [rid] + [d['doc'] for d in top]
    return ev, list(dict.fromkeys(nodes6))


def _plan_path(ctx, p, o):
    """연금계좌(IRP·연금저축)가 아닌 제도 질문 — DB·DC·퇴직금제도·기업형IRP·(구)개인연금저축·ISA·퇴직급여 받는 길.
    재원(①②③)·연금외수령 칸·나이·인출 금액 되묻기를 만들지 않는다. 문장은 ontology.json plan_rules(원문 대조됨)."""
    pr = o['plan_rules']
    intent, q = ctx['intent'], ctx['q'] or ''
    ev = set(ctx['events'])
    ents, nodes1 = _entities(ctx, p, o, dict(p['fund']), p['balance'], plan=True)
    steps = [{'step': STEPS[0], 'detail': ' · '.join(ents) or '계산에 쓸 값이 질문에 없음', 'nodes': nodes1, 'intent': intent}]
    missing, assumed, req, nodes3, tax_lines, docs = [], [], [], [], [], []
    stage = None

    def ask(aid, why):
        a = o['attr_index'][aid]
        missing.append({'attr': aid, 'label': a['label'], 'ask': a.get('ask', a['label']), 'rule': why})

    def quotes_of(key):
        return [x['doc'] for x in pr[key].get('quotes', [])]

    if intent == 'plan_db':
        R = pr['plan.db']
        req += ['R-12: ' + R['lines'][0]] + R['lines'][1:]
        if ev & set(_R13_EVENTS + _UNAVOIDABLE_EVENTS):
            req.append(R['event_line'])
        nodes3 += ['R-12', 'plan.db', 'event.plan_change']
        tax_lines.append('해당 없음 — DB는 중도인출·해지가 없어 인출 세금(연금계좌 ①②③ 재원 표)을 쓰지 않음')
        docs += quotes_of('plan.db')
        stage = 'stage.early'
    elif intent == 'plan_dc':
        R = pr['account.dc']
        req += ['R-12: ' + R['lines'][0], 'R-13: ' + R['lines'][1]]
        reasons = [e for e in _R13_EVENTS if e in ev and e in R['tax']]
        if not reasons and 'event.loan' not in ev:
            if ctx['wd_verb'] or ctx['unavoidable_q']:
                req.append(R['no_reason_line'])
                ask('attr.reason', 'R-13 법정 중도인출 사유')
            tax_lines.append(R['tax_any'])
        else:
            tax_lines += [o['index'][e]['label'] + ' — ' + R['tax'][e] for e in reasons] or [R['tax_any']]
            if ev & {'event.house', 'event.lease'}:
                ask('attr.homeless', 'R-13 법정 중도인출 사유')
            ask('attr.dc_split', 'DC 중도인출 과세(doc55)')
        tax_lines.append(R['profit_line'])
        nodes3 += ['R-12', 'R-13', 'account.dc'] + [e for e in ctx['events']]
        docs += quotes_of('account.dc')
        stage = 'stage.early'
    elif intent == 'plan_severance':
        R = pr['plan.severance']
        req += R['lines'][:2]
        tax_lines.append(R['lines'][2])
        nodes3 += ['plan.severance', 'event.plan_change']
        docs += quotes_of('plan.severance')
        stage = 'stage.early'
    elif intent == 'plan_irp_corp':
        R = pr['plan.irp_corp']
        req += ['R-12: ' + R['lines'][0], R['lines'][1]]
        nodes3 += ['R-12', 'plan.irp_corp']
        tax_lines.append('해당 없음 — 받은 문서에 기업형 IRP 세금 규칙이 따로 없음')
        docs += quotes_of('plan.irp_corp')
        stage = 'stage.early'
    elif intent == 'old':
        R = pr['plan.old']
        nodes3 += ['R-15', 'plan.old']
        ded = bool(p['contrib'] or re.search(r'공제|납입|넣', q))
        pay = bool(ctx['pays'] or re.search(r'연금\s*(?:으로|을)?\s*(?:받|수령|개시)', q))
        wd = bool(ctx['cancel'] or ctx['unavoidable_q'] or re.search(r'해지|인출|빼|찾|꺼내|깨', q))
        if ded and not wd:
            req += ['R-15: ' + R['deduction'][0]]
            tax_lines.append('세액공제 계산(R-01·R-02)을 쓰지 않음 — (구)개인연금저축은 소득공제(doc1)')
            stage = 'stage.pay'
        elif pay and not wd:
            ok = _age_ok(ctx['age'])
            req += ['R-15: ' + R['payout'][0] + (f" — 만 55세 이상 {'✓' if ok else '✗'} ({_age_txt(ctx['age'])})"
                                                   if ok is not None else ''), R['payout'][1]]
            tax_lines.append('연금수령 요건을 채우면 비과세 — 연금저축계좌의 연금소득세(R-07)·1,500만 원(R-08) 규칙을 쓰지 않음')
            stage = 'stage.payout'
        elif re.search(r'옮|이전|이체', q) and not wd:
            req.append('R-15: ' + R['transfer'])
            stage = 'stage.transfer'
        else:
            req += ['R-12: ' + R['withdraw'][0]]
            special = re.search(r'사망|돌아가|해외\s*이주|이민|천재지변|퇴직|폐업|요양|입원', q)
            lines = R['withdraw'][1:]
            if special:
                lines = [lines[1], lines[0], lines[2]]       # 특별중도해지 사유가 있으면 비과세 줄을 먼저
            tax_lines += lines
            nodes3.append('R-12')
            stage = 'stage.early'
        docs += quotes_of('plan.old')
    elif intent == 'isa_only':
        R = pr['plan.isa']
        req.append(R['lines'][0])
        tax_lines += R['lines'][1:]
        nodes3 += ['plan.isa']
        docs += quotes_of('plan.isa')
    elif intent == 'retire_route':
        R = pr['retire_route']
        ok = _age_ok(ctx['age'])
        if 'account.savings' in ctx['accounts'] and 'plan.old' not in ctx['accounts']:
            # '연금저축에 퇴직금 넣을 수 있어요?' — 연금저축 → 퇴직금 엣지 규칙(doc51 표)을 먼저(9차 검수 910)
            req.append(R['savings'])
            nodes3.append('account.savings')
        if ok is False:
            req.append(R['under55'] + f" ({_age_txt(ctx['age'])})")
        elif ok is True:
            req.append(R['over55'] + f" ({_age_txt(ctx['age'])})")
        else:
            req += [R['under55'], R['over55']]
            ask('attr.age', 'IRP 의무이전 예외(55세 이상 퇴직)')
        req += R['lines'][:1] + R['lines'][2:]
        tax_lines += [R['lines'][1], R['tax']]
        nodes3 += ['event.retire', 'fund.retire', 'account.irp'] + [a for a in ctx['accounts'] if a.startswith('plan.') or a == 'account.dc']
        docs += quotes_of('retire_route')
        stage = 'stage.employer'

    if missing:
        det2 = '없음: ' + ' · '.join(m['label'] for m in missing) + ' → 되묻기 (' + \
            ' · '.join(dict.fromkeys(m['rule'] for m in missing)) + ')'
    else:
        det2 = '연금계좌 재원·나이·인출 금액은 이 제도 규칙에 필요 없음'
    steps.append({'step': STEPS[1], 'detail': det2, 'nodes': [m['attr'] for m in missing],
                  'missing': missing, 'assumed': assumed})
    steps.append({'step': STEPS[2], 'detail': ' / '.join(req) or '해당 없음', 'nodes': list(dict.fromkeys(nodes3))})
    steps.append({'step': STEPS[3], 'detail': '해당 없음 — 연금계좌 재원 인출 순서(R-06)는 이 제도에 쓰지 않음', 'nodes': []})
    steps.append({'step': STEPS[4], 'detail': ' / '.join(tax_lines) or '해당 없음', 'nodes': []})
    ev6, nodes6 = _evidence(ctx['rules'], docs)
    steps.append({'step': STEPS[5], 'detail': ' / '.join(ev6) or '해당 없음', 'nodes': nodes6})
    if missing:
        det7 = '되묻기: ' + ' · '.join(m['ask'] for m in missing) + ' — 또는 규칙을 먼저 설명'
    else:
        det7 = '규칙 설명(계산 없음) · 근거 문서'
    steps.append({'step': STEPS[6], 'detail': det7, 'nodes': []})
    steps[0]['scope'] = ctx['scope']
    if stage:
        steps[0]['stage'] = stage
    steps[0]['note'] = _INTENT_NOTE.get(intent, '')
    return steps


def path_for(parsed, q=None):
    """질문이 온톨로지를 지나는 단계 [{'step','detail','nodes',...}] (07 §7).
    parsed = taxcalc.parse(q) 결과(질문 문자열을 그대로 줘도 된다). q 를 같이 주면 계좌·사건 이름을 글에서 읽는다.
    parsed 가 None 이고 q 만 있으면 q 를 parse 한다. 모양이 이상한 값은 빠진 값으로 본다(예외를 내지 않는다).
    '빠진 속성 확인' 단계에는 'missing'(되묻기 목록)·'assumed'(대신 채운 값)가 붙는다.
    검수(10-15): 계좌·제도 종류를 먼저 본다 — DB·DC·퇴직금제도·기업형IRP·(구)개인연금저축·ISA 는 제도 규칙(_plan_path),
    연금계좌(IRP·연금저축)만 ①②③ 재원 × 인출 방식 표를 쓴다."""
    if isinstance(parsed, str):
        q, parsed = parsed, None
    q = q if isinstance(q, str) and q.strip() else None
    if parsed is None and q:
        parsed = taxcalc.parse(q)
    p = _clean(parsed)
    o = load()
    won = taxcalc.won
    ctx = _context(p, q)
    if ctx['intent'] in PLAN_INTENTS:
        return _plan_path(ctx, p, o)
    f, B, amount_notes = _amounts(ctx)
    intent, srcs = ctx['intent'], ctx['sources']
    ctx['irp_full'] = _irp_full(ctx)
    name = lambda s: o['index'][SRC_NODE[s]]['label']      # noqa: E731 — 07 의 재원 이름
    pens = o['plan_rules']['pension']
    ev = set(ctx['events'])

    # 1. 개체 인식
    ents, nodes1 = _entities(ctx, p, o, f, B)
    steps = [{'step': STEPS[0], 'detail': ' · '.join(ents) or '계산에 쓸 값이 질문에 없음',
              'nodes': nodes1, 'intent': intent, 'scope': ctx['scope']}]

    # 2. 빠진 속성 확인 — 규칙 사슬의 조건 속성 중 질문에 없는 것
    missing, assumed, have = [], list(ctx['assumed']) + amount_notes, []
    if ctx['irp_full'] and ctx['w'] is not None:
        if _irp_over(ctx):
            assumed.append(f"IRP는 사유 없이 일부만 꺼낼 수 없음 — 꺼내려면 평가액 {won(p['balance'])} 전액 해지(doc14)")
        else:
            assumed.append(f"IRP는 사유 없이 일부만 꺼낼 수 없어 {won(ctx['w'])}을 계좌 전액(해지)으로 봄")
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
            elif aid == 'attr.withdraw' and ctx['irp_full']:
                ask = '해지할 IRP 계좌 평가액(사유가 없으면 일부가 아닌 전액 해지)'
            missing.append({'attr': aid, 'label': lab, 'ask': ask, 'rule': rid})
        elif st == 'assumed':
            assumed.append(f"{a['label']}: {txt}" if txt else a['label'])
        elif st == 'derived':
            if txt:
                assumed.append(f"{a['label']}: {txt}")
            have.append(a['label'])
        else:
            have.append(f"{a['label']}({txt})" if txt else a['label'])
    if ctx['irp_full'] and ctx['w'] is not None and p['balance'] is None \
            and not any(m['attr'] == 'attr.balance' for m in missing):
        # 인출 금액을 계좌 전액으로 본 것은 가정일 뿐 — IRP 는 일부만 꺼낼 수 없으니 평가액(전액 해지)을 묻는다(9차 검수 904)
        a = o['attr_index']['attr.balance']
        missing.append({'attr': 'attr.balance', 'label': a['label'], 'rule': 'R-12',
                        'ask': f"해지할 IRP 계좌 평가액({won(ctx['w'])}이 계좌 전액이 아니면 — 일부만 꺼낼 수 없어 평가액 전액 해지)"})
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
    min_years = o['attr_index']['attr.period'].get('min_years')
    if intent in ('withdraw', 'payout'):
        ok = _age_ok(ctx['age'])
        r4 = f"R-04: 만 55세 이상 {'✓' if ok else '✗' if ok is False else '(나이 확인 필요)'}" \
            + (f" ({_age_txt(ctx['age'])})" if ctx['age'] is not None else '')
        if 'retire' in srcs:
            r4 += ' · 이연퇴직소득이 있어 가입 5년 요건 면제 ✓'
        elif ctx['period'] is not None:
            r4 += f" · 가입 {ctx['period']}년 {'✓' if not min_years or ctx['period'] >= min_years else '✗'}"
        else:
            r4 += ' · 가입 5년 이상(질문에 없음 — 충족으로 봄)'
        if ctx['started'] is True:
            r4 += ' · 연금 개시 신청 후 ✓'
        elif ctx['cancel']:
            r4 += (' · 연금 개시 신청 여부 확인 필요 — 개시 신청 전 해지는 전액 연금외수령, '
                   '연금 지급 중 해지는 연금수령한도까지 연금소득세·넘는 금액만 연금외수령(doc1·doc5)')
        else:
            r4 += ' · 연금 개시 신청 후(질문에 없음 — 개시로 봄, 개시 전 인출은 연금외수령)'
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
        reason = ctx['early_reason']
        age_t = f" ({_age_txt(ctx['age'])})" if ctx['age'] is not None else ''
        if reason == 'prestart':
            req.append('R-04: 연금 개시 신청 전 인출 → 55세가 넘어도 연금외수령(doc5·doc21 표)' + age_t)
        elif reason == 'period':
            req.append(f"R-04: 가입 {ctx['period']}년 ✗ (5년 미만, 퇴직금 없음) → 연금수령 요건을 못 채워 연금외수령" + age_t)
        elif reason in ('cancel', 'premise'):
            req.append('R-04: 나이 확인 필요 — 55세 전 해지·중도인출은 연금외수령, 55세 이후 연금 지급 중 해지는 '
                       '연금수령한도까지 연금소득세·넘는 금액만 연금외수령(doc1)')
        else:
            req.append('R-04: 55세 전 인출·해지 → 연금수령 요건을 못 채워 연금외수령' + age_t)
        req += _r12_lines(ctx, o)
        nodes3 += ['R-04', 'R-12', 'wd.other']
        nu = [e for e in ctx['events'] if e in _NOT_UNAVOIDABLE_EVENTS]
        if nu and not ctx['r13']:
            # 연금저축만 — 근퇴법 사유(R-13)·무주택 여부는 쓰지 않고, 세법상 부득이한 사유가 아니라는 점(R-11)만 짚는다(9차 검수 903)
            req.append('R-11: ' + pens['savings_not_unavoidable'])
            nodes3 += ['R-11'] + nu
        elif ctx['premise']:
            req.append('R-13: ' + pens['house_premise'])
            nodes3 += ['R-13'] + nu
        elif nu:
            req.append('R-13: IRP 중도인출 사유(' + ' · '.join(o['index'][e]['label'] for e in nu)
                       + ')에 해당 — 세법상 부득이한 사유는 아니라 연금외수령(doc20)')
            nodes3 += ['R-13'] + nu
        elif ctx['irp_full'] and ctx['early_reason'] in ('hard', 'age', 'cancel'):
            # IRP 는 55세 전 법정 사유가 있어야 일부 인출(doc14·doc20) — 사유 목록을 함께 보인다
            req.append('R-13: IRP 55세 전 법정 중도인출 사유 — ' + o['rule_index']['R-13']['condition'])
            nodes3.append('R-13')
    elif intent == 'unavoidable':
        r11 = o['rule_index']['R-11']
        req.append('R-11: ' + r11.get('path_text', r11['condition']))
        nodes3 += ['R-11', 'wd.unavoidable'] + ctx['events']
        emig = 'event.emigrate' in ev or ctx['emig_cond']
        if emig:
            # 해외이주는 신고확인서가 있어야 인정 — 영주권·장기체류만으로는 아님(doc20, 9차 검수 908)
            req.append('R-11: ' + pens['emigrate_proof'])
            nodes3.append('event.emigrate')
        if ev & set(_LIMIT_EVENTS):
            req.append('R-11: ' + r11['limit_text'])
        if 'event.disaster' in ev:
            req.append('R-11: ' + pens['disaster_cond'])
        accs = ctx['accounts']
        if 'account.irp' in accs and (emig or 'event.death' in ev) and not ev & set(_R13_EVENTS):
            req.append('R-12: ' + pens['irp_death_emigrate'])
            nodes3.append('R-12')
        elif 'account.savings' in accs and 'account.irp' not in accs:
            req.append('R-12: ' + pens['savings_any'])
            nodes3.append('R-12')
        elif ctx['r13']:
            req.append('R-13: ' + o['rule_index']['R-13']['condition'])
            nodes3.append('R-13')
        else:
            # 계좌를 말하지 않은 사망·해외이주 — 근퇴법 사유(R-13)가 아니다. IRP 는 전액 해지만, 연금저축은 일부도 된다(doc20)
            req += ['R-12: ' + pens['irp_death_emigrate'], 'R-12: ' + pens['savings_any']]
            nodes3.append('R-12')
        if emig and 'retire' in srcs:
            req.append('R-11: ' + pens['emigrate_retire'])
    elif intent == 'deduction':
        # 기준 금액(5,500만·4,500만 …)은 RULEBOOK 요약에서 가져온다 — 판정은 taxcalc.credit_rate 가 한다
        req.append('R-01: ' + _RULE_SUMMARY['R-01'] + (' (DC 가입자부담금도 합산, doc55)' if 'account.dc' in ctx['accounts'] else ''))
        if p['income']:
            kind, v = p['income']
            req.append(f"R-02: {kind} {won(v)} → 공제율 {taxcalc.pct(taxcalc.credit_rate(v, kind))} "
                       f"(기준: {_RULE_SUMMARY['R-02']})")
        else:
            req.append(f"R-02: 공제율은 소득으로 정해짐 — 소득이 질문에 없음 (기준: {_RULE_SUMMARY['R-02']})")
        nodes3 += ['R-01', 'R-02', 'account.savings', 'account.irp']
        if p['isa']:
            max_days = o['attr_index']['attr.isa_days'].get('max_days')
            if ctx['isa_days'] is not None and max_days and ctx['isa_days'] > max_days:
                req.append(f"R-03: ISA 만기일부터 약 {ctx['isa_days']}일 — 60일이 지나 연금계좌 전환 불가(doc4) → 추가 공제 대상 아님")
            else:
                req.append(f"R-03: ISA 만기 전환 {won(p['isa'])} → 추가 공제 대상 {won(taxcalc.isa_extra(p['isa']))}"
                           f" ({_RULE_SUMMARY['R-03']} — 만기일부터 60일 안 전환, doc4)")
            nodes3 += ['R-03', 'plan.isa']
    # 상품 운용 — DC·IRP(퇴직연금)에 펀드·ETF 를 담는 질문이면 위험자산 한도(R-14)를 요건으로 짚는다
    if q and re.search(r'펀드|ETF|TDF|위험\s*자산|주식\s*형|주식\s*혼합|투자\s*신탁', q) and \
            ({'account.irp', 'account.dc', 'plan.db'} & set(ctx['accounts']) or re.search(r'퇴직\s*연금', q)):
        req.append('R-14: ' + _RULE_SUMMARY['R-14'] + ' (적격 TDF·채권형·MMF 는 DC·IRP 100% — DB 는 적격 TDF 도 70%까지, doc58)')
        nodes3 += ['R-14', 'product.performance', 'product.risk']
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
        if intent in ('early', 'unavoidable') and not ctx['mentioned']:
            det += ' — ② 퇴직금은 없다고 봄(질문에 없음, 있으면 ②가 ③보다 먼저 빠지고 퇴직소득세)'
        steps.append({'step': STEPS[3], 'detail': det, 'nodes': ['R-06'] + [SRC_NODE[s] for s in srcs]})
    else:
        steps.append({'step': STEPS[3], 'detail': '해당 없음' + (' — 납입·공제 질문' if intent == 'deduction' else ''),
                      'nodes': []})

    # 5. 세금 계산 — 재원 × 인출 유형 표(07 §6)로 세목을 정하고, 값이 다 있으면 taxcalc 로 금액까지
    tax_lines, nodes5, calc, cells5 = [], [], None, []
    if intent in ('withdraw', 'payout', 'early', 'unavoidable'):
        limited = intent == 'unavoidable' and bool(ev & set(_LIMIT_EVENTS))
        # 영주권·장기체류·해외 이사 — 해외이주신고확인서가 있는지 모르므로 부득이 칸과 일반 인출 칸을 함께(doc20)
        cond = intent == 'unavoidable' and ctx['emig_cond']
        if intent == 'early':
            modes = {s: ['연금외수령'] for s in srcs}
        elif intent == 'unavoidable':
            # 요양·재난피해는 한도까지만 연금소득세 — 넘는 금액은 일반 인출(doc5). ① 은 어느 쪽이든 비과세라 한 칸
            modes = {s: ['부득이', '연금외수령'] if (limited or cond) and s != 'free' else ['부득이'] for s in srcs}
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
                tag = None
                if len(modes[s]) > 1 and cond and not limited:
                    tag = {'부득이': '해외이주신고확인서 있으면', '연금외수령': '없으면(55세 전·개시 전 해지 등)'}.get(m)
                elif len(modes[s]) > 1 and (intent == 'withdraw' or limited):
                    tag = {'연금수령': '한도 안', '부득이': '한도 안', '연금외수령': '한도 밖'}.get(m)
                parts.append((f'{tag}: ' if tag else f'{m}: ') + tr['text'])
                # ② 퇴직금 칸은 규칙(R-09 감면·R-10·R-11)도 — 연금수령 세목이 연금소득세가 돼도 감면 규칙이 경로에 보이게
                nodes5 += tr['nodes'] + (tr['rule_ids'] if s == 'retire' else [])
                cells5.append([s, m])
            if parts:
                tax_lines.append(f'{name(s)} — ' + ' / '.join(parts))
        if intent == 'unavoidable' and ('event.emigrate' in ev or ctx['emig_cond']) and 'retire' in srcs:
            tax_lines.append('② 해외이주: 퇴직금 입금 후 3년이 안 됐으면 퇴직금은 퇴직소득세(doc1·doc20)')
        if res and res['total'] is not None:
            calc = {'total': won(res['total']), 'limit': won(res['limit']) if res['limit'] is not None else None,
                    'rows': [{'source': k, 'take': won(t), 'inside': won(i), 'over': won(ov)} for k, t, i, ov in res['rows']],
                    'lines': res['lines']}
            tax_lines.append(f'올해 세금 합계 = {calc["total"]} (taxcalc.withdraw_tax)')
        elif intent == 'early' and ctx['w'] is not None and 'free' not in p['fund'] and not f.get('retire') \
                and not f.get('credit') and 'retire' not in ctx['mentioned']:
            # ① 금액을 모르는 연금외수령 — 퇴직금이 없다고 볼 때 전부 ③ 으로 본 값이 최대 세금(taxcalc.by_source 와 같은 조건).
            # 퇴직금이 섞였으면 퇴직소득세가 사람마다 달라 최대도 말할 수 없다 — 그래서 '퇴직금 없음'을 가정으로 밝힌다
            mx = taxcalc.withdraw_tax(ctx['w'], credit=ctx['w'], early=True)
            calc = {'total': won(mx['total']), 'limit': None, 'upper_bound': True, 'rows': [], 'lines': mx['lines']}
            tax_lines.append(f'공제 안 받은 돈을 모르면 — 퇴직금이 없고 전부 {taxcalc.FUND_NAME["credit"]}이라 볼 때(최대) '
                             f'{won(ctx["w"])} × 16.5% = {calc["total"]} (taxcalc.withdraw_tax)')
        elif missing:
            tax_lines.append('금액은 빠진 속성을 받은 뒤 계산')
        elif intent == 'payout':
            tax_lines.append('연 수령액의 세금은 답변의 계산 블록(taxcalc)이 낸다 — 여기서는 규칙 경로만')
        if _irp_over(ctx):
            # 금액은 답변 계산 블록(taxcalc)과 맞추려 질문한 금액으로 두되, IRP 는 그 금액만 뺄 수 없다는 점을 밝힌다(doc14)
            tax_lines.append(f"IRP는 일부만 꺼낼 수 없음 — 위 금액은 질문한 {won(ctx['w'])} 기준이고, 해지하면 평가액 "
                             f"{won(p['balance'])} 전액이 위 칸대로 과세(doc14)")
    elif intent == 'deduction':
        pd = p
        max_days = o['attr_index']['attr.isa_days'].get('max_days')
        if p['isa'] and ctx['isa_days'] is not None and max_days and ctx['isa_days'] > max_days:
            pd = dict(p, isa=None)              # 기한이 지나 전환 불가 — ISA 추가 공제·전환 한도를 계산하지 않는다(doc4)
        tax_lines, calc, nodes5 = _deduction_lines(pd)
    step5 = {'step': STEPS[4], 'detail': ' / '.join(tax_lines) or '해당 없음', 'nodes': list(dict.fromkeys(nodes5)),
             'cells': cells5}         # 세금 지도에서 지나는 칸 — 노드 목록은 중복을 지워 칸을 되짚을 수 없다
    if calc:
        step5['calc'] = calc
    steps.append(step5)

    # 6. 근거 연결 — 규칙 → 원문 대조된 문서(꼬리표가 있으면 독자·기준일)
    used = ctx['rules'] + [r for st in steps for r in st['nodes'] if re.match(r'^R-\d\d$', r)]
    ev6, nodes6 = _evidence(used)
    steps.append({'step': STEPS[5], 'detail': ' / '.join(ev6) or '해당 없음', 'nodes': nodes6})

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
# 열 머리 힌트 — 연금수령 요건은 55세 이후 · 가입 5년 · 개시 신청 후 한도 안(doc1·doc39), 개시 신청 전 인출은 연금외수령(doc5·doc21)
MODE_INFO = {'연금수령': ('연금수령', '55세 이후 · 가입 5년 · 개시 신청 후 한도 안'),
             '부득이': ('부득이한 사유', '요양·파산·사망·해외이주 등 · 6개월 안 서류'),
             '연금외수령': ('연금외수령', '55세 전 해지 · 개시 전 인출 · 한도 초과')}
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
               ('credit', '부득이'): ('5.5%', '저율', '한도', '6개월'), ('credit', '연금외수령'): ('16.5%',)}
# 칸 머리 한 줄 — 각 칸의 plain(07 §6) 을 줄인 것. 새 세율을 만들지 않는다
_CELL_SHORT = {('retire', '연금수령'): '퇴직소득세 30~50% 감면', ('retire', '연금외수령'): '퇴직소득세 100%',
               ('retire', '부득이'): '이연퇴직소득세 × 70%',
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
        # 칸에 붙은 문서 원문(evidence) — 세목을 문서로 고친 칸(② × 연금수령·부득이)은 그 원문을 먼저 보인다.
        # 세목이 '문서 확인 필요'인 칸만 '후보'라고 밝힌다
        cand = '확인 필요' in str(c['tax'])
        evq = [{'file': e['file'], 'text': e['text'], 'title': _brief(o, e['file'])['title'], 'candidate': cand}
               for e in c.get('evidence', [])]
        quotes = (evq + quotes) if c.get('resolved') or c.get('md07_tax') else (quotes or evq)
        files = list(dict.fromkeys([q['file'] for q in quotes] + list(c['docs'])))
        short = '비과세' if c['tax'] == '비과세' else _CELL_SHORT.get((c['source'], c['mode']), c['tax'])
        cells.append({'source': c['source'], 'mode': c['mode'], 'tax': c['tax'], 'short': short, 'plain': c['plain'],
                      'rule_ids': list(c['rule_ids']), 'quotes': quotes[:3], 'docs': [_brief(o, f) for f in files][:4]})
    scope = tm.get('scope', '')
    stages = [{'id': s['id'], 'label': s['label'], 'group': s.get('group', ''),
               'rule_ids': STAGE_RULES.get(s['id'], []), 'modes': STAGE_MODES.get(s['id'], []),
               'docs': [_brief(o, f) for f in s['docs']]} for s in o['stages']]
    return {'sources': [{'key': k, 'label': taxcalc.FUND_NAME[k]} for k in tm['sources']],
            'modes': [{'key': m['key'], 'label': MODE_INFO[m['key']][0], 'hint': MODE_INFO[m['key']][1]}
                      for m in tm['modes']],
            'cells': cells, 'stages': stages, 'scope': scope,
            'docs': {f: _brief(o, f) for f in sorted(o['docs'], key=lambda x: int(re.sub(r'\D', '', x) or 0))}}


_NODE_SRC = {v: k for k, v in SRC_NODE.items()}
_NODE_MODE = {v: k for k, v in MODE_NODE.items()}
_INTENT_STAGE = {'early': 'stage.early', 'withdraw': 'stage.payout', 'payout': 'stage.payout',
                 'deduction': 'stage.pay', 'isa': 'stage.pay', 'unavoidable': 'stage.early', 'invest': 'stage.invest',
                 'plan_db': 'stage.early', 'plan_dc': 'stage.early', 'plan_severance': 'stage.early',
                 'plan_irp_corp': 'stage.early', 'retire_route': 'stage.employer', 'old': 'stage.early'}


def question_cells(q):
    """질문이 세금 지도에서 지나는 칸 → {'cells': [[재원, 인출 방식], ...], 'stage', 'rule_ids', 'intent', 'steps'}.
    답변의 '온톨로지 경로'와 같은 path_for 의 단계 노드에서 읽는다. 모델을 부르지 않는다."""
    steps = path_for(None, q or '')
    nodes = list(dict.fromkeys(n for s in steps for n in s.get('nodes', [])))
    # '세금 계산' 단계의 노드는 [재원, (인출 유형), 세목, …, 재원, 세목 …] 순서다 — 재원마다 뒤따르는 세목과
    # 단계 안의 인출 유형으로 칸을 고른다(세목이 맞는 칸만: 07 §6 표의 tax_node)
    tax_step = next((s for s in steps if s['step'] == STEPS[4]), {'nodes': []})
    if 'cells' in tax_step:
        intent = next((s.get('intent') for s in steps if s.get('intent')), '') or ''
        return {'cells': [list(c) for c in tax_step['cells']], 'stage': steps[0].get('stage') or _INTENT_STAGE.get(intent, ''),
                'rule_ids': [n for n in nodes if re.match(r'^R-\d\d$', str(n))], 'intent': intent,
                'note': steps[0].get('note', ''),
                'steps': [{'step': s['step'], 'detail': s['detail']} for s in steps]}
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
    # 연금계좌가 아닌 제도(DB·DC·(구)개인연금저축·ISA …) 질문은 세금 지도 칸이 없다 — 왜 없는지 한 줄(note)
    return {'cells': cells, 'stage': steps[0].get('stage') or _INTENT_STAGE.get(intent, ''),
            'rule_ids': [n for n in nodes if re.match(r'^R-\d\d$', str(n))], 'intent': intent,
            'note': steps[0].get('note', ''),
            'steps': [{'step': s['step'], 'detail': s['detail']} for s in steps]}
