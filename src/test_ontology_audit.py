# -*- coding: utf-8 -*-
"""
온톨로지 검수(10-15) 회귀 시험 — 반박 검증까지 통과한 오류마다 재현 입력을 넣어 다시 틀리지 않는지 본다.
API 없이 돈다.  PYTHONIOENCODING=utf-8 python src/test_ontology_audit.py

사용자가 찾은 예: 'DB형인데 해지할 수 있어요?' 가 DB 를 개인 연금계좌처럼 다뤄 '55세 전 인출·해지 → 연금외수령',
'① 세액공제 안 받은 돈 × 연금외수령 비과세' 칸, 나이·재원·인출 금액 되묻기를 냈다. 문서(doc49)는 DB 중도인출을
허용하지 않는다. 원인은 질문 경로가 제도·계좌 종류를 보지 않은 것 — 이제 계좌 종류(scope)를 먼저 보고, 연금계좌
(IRP·연금저축)가 아닌 제도는 ontology.json plan_rules(문서 원문과 함께)로 답한다.

묶음
  D  data/ontology.json 값(규칙 조건·엣지 메모·세금 칸·노드)
  P  질문이 지나는 길(path_for·question_cells)
  V  시연 화면(그래프·세금 지도·열 머리)
  G  온톨로지 길잡이(data/onto_guide.json·onto_guide.select)
  T  조각 순위·펀드 분류(onto_link·data/ontology_funds.json)
  Q  모든 원문 인용이 문서에 글자 그대로 있는가(줄바꿈이 끊긴 PDF 표는 match 'ws' — 띄어쓰기·줄바꿈만 무시)
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import onto_guide  # noqa: E402
import onto_link  # noqa: E402
import ontology  # noqa: E402
import taxcalc  # noqa: E402

ROOT = ontology.ROOT
DOCS = os.path.join(ROOT, 'extracted', 'docs')
PROS = os.path.join(ROOT, 'extracted', 'prospectus')
results = []


def check(name, ok, why=''):
    results.append((name, bool(ok), why))


O = ontology.load()
R = O['rule_index']
NODE = O['index']
EDGES = O['edges']
CELLS = {(c['source'], c['mode']): c for c in O['tax_matrix']['cells']}


def path(q):
    s = ontology.path_for(q)
    return s, {x['step']: x for x in s}


def text(steps):
    return json.dumps(steps, ensure_ascii=False)


def cells(q):
    return ontology.question_cells(q)['cells']


def missing(d):
    return [m['attr'] for m in d['빠진 속성 확인'].get('missing', [])]


def edge(a, b):
    return [e for e in EDGES if e['from'] == a and e['to'] == b]


def concept(cid):
    return next(c for c in onto_guide.load()['concepts'] if c['id'] == cid)


def picked(q):
    return [c['id'] for c, _, _ in onto_guide.select(q)]


# 연금계좌 규칙을 DB·DC·(구)개인연금저축에 붙였을 때 나오던 말
PENSION_LEAK = re.compile(r'55세 전 인출·해지 → 연금수령 요건을 못 채워|전부 ③|× 16\.5%|연금외수령: 비과세')


# ---------------------------------------------------------------------------
# D — ontology.json 값
# ---------------------------------------------------------------------------
def test_data():
    c12 = R['R-12']['condition']
    check('[D1] R-12 조건: DB 불가 + DC 전환 후에만', 'DB → 불가' in c12 and 'DC 로 전환' in c12, c12)
    check('[D1] R-12 → DB 는 이름 붙은 관계 "중도인출 불가"(doc49 원문)',
          edge('R-12', 'plan.db') and edge('R-12', 'plan.db')[0]['label'] == '중도인출 불가'
          and 'DB(확정급여형)제도의 경우는 중도인출이 허용되지 않습니다' in edge('R-12', 'plan.db')[0]['quote'])
    check('[D2] R-12 조건: DC 재직 중 해지 없음 · IRP 사유 없으면 전액 해지만',
          'DC → 재직 중 해지 없음' in c12 and 'IRP → 55세 전에는 법정 사유' in c12 and '전액 해지만' in c12, c12)
    for ev in ('event.house', 'event.lease'):
        e = edge(ev, 'wd.other')[0]
        check(f'[D3] {ev} 메모: DC 는 퇴직금 퇴직소득세 · ② 재원은 퇴직소득세(16.5% 는 ③ 만)',
              'DC 는 퇴직금 퇴직소득세' in e['note'] and '② 퇴직금 퇴직소득세' in e['note']
              and any(q['doc'] == 'doc55' for q in e.get('quotes', [])), e['note'])
    for ev in ('event.care', 'event.rehab', 'event.disaster'):
        e = edge(ev, 'wd.unavoidable')[0]
        check(f'[D4] {ev} 메모: IRP·연금저축과 DC 를 나눔(DC 퇴직금 퇴직소득세)', 'DC: 퇴직금은 퇴직소득세' in e['note'], e['note'])
    check('[D4] 요양 메모: DC 는 의료비 > 연간임금총액 12.5%', '12.5%' in edge('event.care', 'wd.unavoidable')[0]['note'])
    check('[D4] 재난 메모: DC 사회재난은 기타소득세', '사회재난이면 기타소득세' in edge('event.disaster', 'wd.unavoidable')[0]['note'])
    dev = O['meta']['deviations'][4]
    check('[D4] deviations: 부득이 = 연금소득세는 연금계좌 얘기, DC 는 doc55 표', 'DC 중도인출은' in dev and 'doc55' in dev, dev)
    check('[D5] 재난 노드: 특별재난지역 15일 이상 입원 · 한도', '15일 이상 입원' in NODE['event.disaster'].get('note', '')
          and '한도' in NODE['event.disaster']['note'])
    check('[D5] 재난 엣지: 재난피해 한도(doc21) 원문', any(q['doc'] == 'doc21' for q in edge('event.disaster', 'wd.unavoidable')[0]['quotes']))
    c11 = R['R-11']['condition']
    check('[D5] R-11 조건: 요양·재난피해 한도', '200만 원 + 의료비 + 간병비 + 휴직월수 × 150만 원' in c11, c11)
    check('[D5] ③ × 부득이 칸: 요양·재난피해 한도', '한도' in CELLS[('credit', '부득이')]['plain'])
    check('[D6] R-11 조건: 6개월 안 서류 · 놓치면 기타소득세', '6개월' in c11 and '기타소득세' in c11)
    check('[D6] ③ × 부득이 칸: 6개월 안 서류', '6개월' in CELLS[('credit', '부득이')]['plain'])
    c15 = R['R-15']['condition']
    check('[D7] R-12 이 (구)개인연금저축에 적용 · 중도인출 불가', 'plan.old' in R['R-12']['applies_to']
          and '(구)개인연금저축 → 중도인출 불가' in c12 and 'plan.old' in R['R-12']['by_plan'])
    check('[D7] R-15: 중도해지 이자소득세 15.4% · 특별중도해지 비과세', '이자소득세 15.4%' in c15 and '특별중도해지' in c15 and '비과세' in c15)
    check('[D7] R-15 이 연금저축계좌에 적용되지 않음', R['R-15']['applies_to'] == ['plan.old'])
    check('[D8] R-15: 만 55세 이후 개시 · 5년 이상 수령 · 5년 경과 후 해지 비과세',
          '만 55세 이후 개시' in c15 and '5년 이상' in c15 and '5년 경과 후 해지도 비과세' in c15)
    c3 = R['R-03']['condition']
    check('[D9] R-03: 만기 후 60일 안 · 넘으면 전환 불가', '60일' in c3 and '전환 불가' in c3 and 'fund.credit' in R['R-03']['applies_to'])
    check('[D9] R-03 조건 속성에 만기 후 경과일', any(x['attr'] == 'attr.isa_days' for x in R['R-03']['conditions']))
    c14 = R['R-14']['condition']
    check('[D10] R-14: 위험자산 70% · 채권형/MMF/적격 TDF 100% · DB TDF 70%',
          '위험자산' in c14 and 'MMF' in c14 and '적격 TDF 는 DB 에서 70%' in c14, c14)
    check('[D10] 실적배당 노드에서 디폴트옵션 상품을 뺌', '디폴트옵션 상품' not in NODE['product.performance']['kinds'])
    check('[D10] DC·IRP —허용한다(70%)→ 위험자산 / (100%)→ 투자위험을 낮춘 실적배당',
          all(edge(a, 'product.risk') and edge(a, 'product.risk')[0]['label'] == '허용한다(70%)' and edge(a, 'product.lowrisk')
              for a in ('account.dc', 'account.irp')) and not edge('account.dc', 'product.performance'))
    # 원리금보장 노드만 연결 없이 떠 있었다(10-18) — doc56 DC/IRP 투자한도 표·doc58 특례 ① 원문으로 100%
    check('[D10] DC·IRP —허용한다(100%)→ 원리금보장 (doc58 특례 ①·doc56 표)',
          all(edge(a, 'product.guaranteed') and edge(a, 'product.guaranteed')[0]['label'] == '허용한다(100%)'
              and edge(a, 'product.guaranteed')[0]['doc'] == 'doc58'
              and any(q['doc'] == 'doc56' for q in edge(a, 'product.guaranteed')[0].get('quotes', []))
              for a in ('account.dc', 'account.irp')))
    g = ontology.graph()
    deg = {n['id']: 0 for n in g['nodes']}
    for e in g['edges']:
        deg[e['from']] = deg.get(e['from'], 0) + 1
        deg[e['to']] = deg.get(e['to'], 0) + 1
    check('[D10] 화면 개념 지도에 연결 없는 노드가 없음', not [k for k, v in deg.items() if v == 0],
          str([k for k, v in deg.items() if v == 0]))
    # 노드마다 출처(문서 원문) — 아래 문서 줄에 그려지지 않은 문서도 노드를 누르면 보인다(10-18)
    nos = [n['label'] for n in g['nodes'] if n['type'] != 'doc' and not n.get('sources')]
    check('[D10] 화면 개념 지도의 개념·규칙 노드마다 출처 원문이 있음', not nos, str(nos))
    off = [(n['label'], s['file'], s['text'][:30]) for n in g['nodes'] for s in n.get('sources', [])
           if _ws(s['text']) not in _ws(open(os.path.join(DOCS, s['file']), encoding='utf-8').read())]
    check('[D10] 노드 출처 원문이 문서에 글자 그대로 있음', not off, str(off[:3]))
    check('[D10] 노드 출처 수 표시: 보이는 것 ≤ 전체, 문서 노드에는 출처 칸 없음',
          all(len(n['sources']) <= n['n_sources'] <= 999 for n in g['nodes'] if n['type'] != 'doc')
          and not any('sources' in n for n in g['nodes'] if n['type'] == 'doc'))
    e = edge('account.savings', 'fund.retire')[0]
    check('[D11] 연금저축 ← 퇴직금 엣지: 55세 이상 조건을 떼지 않음', e['quote'].startswith('만 55세 이상이 되면')
          and '55세 미만 법정퇴직금과 DC 퇴직금은 IRP' in e['note'])
    c8 = R['R-08']['condition']
    check('[D12] R-08: 전 금융기관 합산 · 부득이한 사유 인출 제외', '전 금융기관 합산' in c8 and '부득이한 사유 인출' in c8)
    check('[D12] excluded_rules: "문서 근거 없음"을 고침', O['excluded_rules'][0]['why'].startswith('문서 근거 있음'))
    check('[D12] ③ × 연금수령 칸: 전 금융기관 합산·부득이 제외', '전 금융기관 합산' in CELLS[('credit', '연금수령')]['plain']
          and '부득이한 사유 인출 제외' in CELLS[('credit', '연금수령')]['plain'])
    c = CELLS[('retire', '부득이')]
    check('[D13] ② × 부득이 = 연금소득세(이연퇴직소득세 × 70%) — 문서로 풀었음', c['tax'] == '연금소득세' and '70%' in c['text']
          and c['tax_node'] == 'tax.pension' and c['rule_ids'] == ['R-11'] and c.get('md07') == '(문서 확인 필요)')
    check('[D13] ② × 부득이: "문서마다 조건이 달라" 문구 없음 · 해외이주 3년 예외', '문서마다' not in c['plain'] and '3년' in c['plain'])
    check('[D13] open_questions 에서 풀린 질문을 지움', not any('이연퇴직소득의 과세 방식' in x for x in O['open_questions']))
    check('[D13] 부득이 노드: ② 재원은 이연퇴직소득세 × 70%', '이연퇴직소득세 × 70%' in NODE['wd.unavoidable']['note'])
    c13 = R['R-13']['condition']
    check('[D14] R-13: 담보대출 상환 · 최근 5년 이내 파산·회생 · DC 12.5%', '담보대출' in c13 and '최근 5년 이내' in c13 and '12.5%' in c13)
    check('[D14] R-13 은 연금외수령에 적용되지 않음(사건에만)', 'wd.other' not in R['R-13']['applies_to'] and 'event.loan' in R['R-13']['applies_to'])
    check('[D14] 담보대출 상환 → 연금외수령(doc20 표 O | X | 16.5%)', edge('event.loan', 'wd.other'))
    check('[D14] deviations: 연금외수령으로 잇는 것에 담보대출', '담보대출 상환' in O['meta']['deviations'][4])
    c5 = R['R-05']['condition']
    check('[D15] R-05: DB·DC 는 퇴직금 전액을 신규 연금계좌로 옮길 때만 6년차', 'DB·DC 가입자는 퇴직금 전액을 신규 연금계좌로' in c5)
    check('[D16] R-01: DC 가입자부담금 합산 · DC 계좌에 적용', 'DC 가입자부담금' in R['R-01']['condition'] and 'account.dc' in R['R-01']['applies_to'])
    inc = ' '.join(NODE['fund.retire']['includes'])
    check('[D17] ② 재원: 임원 퇴직금은 한도 이내분 · 퇴직수당 2002.1.1 이후분', '한도 이내분' in inc and '2002.1.1' in inc)
    check('[D18] R-07a: "문서 간 상충" 아님(doc38 단일 문서)', '상충은 아니다' in R['R-07a']['note'])
    check('[D18] 종신 4.4%(일반 지식) 흔적 없음', not any('4.4%' in str(x) for x in O['excluded_rules'] + O['open_questions']))
    check('[D19] 제도 변경 ≠ 세법 개정 — R-08 은 세법 개정에서', not edge('event.plan_change', 'R-08') and edge('event.tax_reform', 'R-08')
          and edge('event.plan_change', 'plan.dc'))
    check('[D20] 개인형IRP 는 퇴직연금제도(근퇴법 제2조)', NODE['plan.irp'].get('parent') == 'plan.pension'
          and '개인형IRP' in NODE['plan.pension']['kinds'])
    check('[D21] 독자 필터 문구 = 실제 정책(통째로 빼는 문서 없음)', '17건' not in O['documents']['policy'][0]['rule']
          and '섹션 단위' in O['documents']['policy'][0]['rule'])


# ---------------------------------------------------------------------------
# P — 질문이 지나는 길
# ---------------------------------------------------------------------------
DB_QS = ['DB형인데 해지할 수 있어요?', 'DB형인데 중도인출 돼요?', 'DB 가입자인데 55세 전에 해지하면 세금은?',
         'DB형 퇴직연금 주택 구입으로 중도인출 가능한가요?', 'DB형 퇴직연금 3000만 원 해지하면 세금 얼마예요?',
         'DB형 퇴직연금 중도인출 되나요?', 'DB형 퇴직연금인데 요양 때문에 중도인출 되나요?']
DC_QS = ['DC 해지할 수 있어요?', 'DC형 전세보증금으로 2000만 원 중도인출하면 세금 얼마?', 'DC형 주택 구입 때문에 중도인출하면 세금은?',
         'DC형 요양 때문에 1000만 원 중도인출하면 세금은?', 'DC 요양 6개월 이상인데 중도인출 되나요?', 'DC형인데 재직 중에 해지할 수 있어요?',
         'DC 가입자인데 55세 전에 해지하면?', 'DC형 퇴직연금 중도인출하면 세금은?', '58세 DC형 가입자인데 중도인출하면 세금은?',
         'DC형인데 퇴직금 5000만원 중도인출하면 세금은?', 'DC 주택구입 중도인출하면 세금 얼마예요?', 'DC 요양 중도인출 세금']
OLD_QS = ['(구)개인연금저축 중도인출 돼요?', '(구)개인연금저축 해지하면 세금은?', '(구)개인연금저축 1000만 원 해지하면 세금 얼마?',
          '(구)개인연금저축 65세인데 해지하면 세금은?', '(구)개인연금저축 요양 때문에 해지하면 세금은?',
          '(구)개인연금저축 퇴직해서 해지하면 세금은?', '1998년에 가입한 개인연금 해지하면 세금은?']


def test_path():
    for q in DB_QS:
        s, d = path(q)
        t = text(s)
        check(f'[P1·V1] {q}: DB 규칙(중도인출 불가) · 세금 칸 없음 · 연금계좌 규칙 안 붙음',
              s[0]['intent'] == 'plan_db' and cells(q) == [] and 'DB는 중도인출이 허용되지 않음' in d['요건 판정']['detail']
              and not PENSION_LEAK.search(t) and 'R-04' not in t and not missing(d), d['요건 판정']['detail'][:160])
    for q in ('DB형 가입자인데 요양 때문에 중도인출하면 세금은?', 'DB형인데 파산하면 중도인출 되나요?'):
        s, d = path(q)
        check(f'[P2] {q}: 사유가 있어도 DB 는 불가 — 부득이 연금소득세 칸 없음',
              s[0]['intent'] == 'plan_db' and cells(q) == [] and 'R-11' not in d['요건 판정']['nodes']
              and '사유(요양·주택 구입 등)가 있어도 DB에서는 중도인출이 안 됨' in d['요건 판정']['detail'])
    for q, want in [('재직 중 DB 퇴직금 미리 받을 수 있어요?', 'plan_db'), ('퇴직금제도인데 중간정산 받을 수 있어요?', 'plan_severance'),
                    ('퇴직금제도 회사인데 퇴직금 미리 받으면 세금은?', 'plan_severance'),
                    ('DB형에서 퇴직하면 퇴직금이 어디로 가요?', 'retire_route'),
                    ('DB형 60세인데 퇴직금 일시금으로 받으면 세금은?', 'retire_route'),
                    ('DB 가입자 50세 퇴직하면 퇴직금 IRP로 꼭 옮겨야 하나요?', 'retire_route'),
                    ('DC 퇴직금 IRP로 이전 안 하고 그냥 받을 수 있어요?', 'retire_route')]:
        s, d = path(q)
        t = text(s)
        check(f'[P3] {q}: {want} — 연금계좌 한도·감면 칸·평가액 되묻기 없음',
              s[0]['intent'] == want and cells(q) == [] and 'attr.balance' not in missing(d) and 'attr.year_n' not in missing(d)
              and '이연퇴직소득.금액' not in t, f"{s[0]['intent']} / {missing(d)}")
    s, d = path('DB 가입자 50세 퇴직하면 퇴직금 IRP로 꼭 옮겨야 하나요?')
    check('[P3] 50세 퇴직: IRP 의무이전(doc51 표)', '55세 전 퇴직: 법정퇴직금(DB·DC)은 IRP로 의무이전' in d['요건 판정']['detail'])
    s, d = path('DB형 60세인데 퇴직금 일시금으로 받으면 세금은?')
    check('[P3] 60세 퇴직: 일반계좌 선택 가능 · 일시금은 퇴직소득세(doc45)', '55세 이상 퇴직' in d['요건 판정']['detail']
          and '일시금은 퇴직소득세' in d['세금 계산']['detail'])
    for q in DC_QS:
        s, d = path(q)
        t = text(s)
        check(f'[P4·V3] {q}: DC 규칙 — ①·③ 연금계좌 칸·나이 되묻기·"(없으면 해지)" 없음',
              s[0]['intent'] == 'plan_dc' and cells(q) == [] and not PENSION_LEAK.search(t) and '없으면 해지' not in t
              and 'attr.age' not in missing(d) and '55세 전' not in d['개체 인식']['detail'], f"{s[0]['intent']} {missing(d)}")
    s, d = path('DC형 전세보증금으로 2000만 원 중도인출하면 세금 얼마?')
    check('[P4] DC 전세: 퇴직금 퇴직소득세 / 가입자부담금 기타소득세(doc55) · 퇴직금·부담금 구성을 물음',
          '퇴직금(회사 부담금): 퇴직소득세 / 가입자부담금: 기타소득세' in d['세금 계산']['detail'] and 'attr.dc_split' in missing(d))
    s, d = path('DC형 요양 때문에 1000만 원 중도인출하면 세금은?')
    check('[P4] DC 요양: 가입자부담금만 연금소득세 · 의료비 12.5% 요건', '가입자부담금: 연금소득세 3~5%' in d['세금 계산']['detail']
          and '12.5%' in d['요건 판정']['detail'])
    s, d = path('DC 해지할 수 있어요?')
    check('[P4] DC 해지: 사유가 없으면 퇴직 때 퇴직급여 — 사유를 물음', '사유가 없으면 DC 적립금은 퇴직할 때' in d['요건 판정']['detail']
          and 'attr.reason' in missing(d))
    for q in OLD_QS:
        s, d = path(q)
        t = text(s)
        check(f'[P5·V9] {q}: (구)개인연금저축 규칙 — 중도인출 불가 · 이자소득세 15.4% · 16.5% 안 씀',
              s[0]['intent'] == 'old' and cells(q) == [] and '15.4%' in d['세금 계산']['detail'] and '기타소득세 16.5% (R-10)' not in t
              and 'account.savings' not in d['개체 인식']['nodes'] and 'R-15' in d['근거 연결']['nodes'], d['세금 계산']['detail'][:120])
    s, d = path('(구)개인연금저축 요양 때문에 해지하면 세금은?')
    check('[P5] (구) 요양: 특별중도해지 비과세가 먼저', d['세금 계산']['detail'].startswith('특별중도해지'))
    s, d = path('1999년 가입 개인연금저축 연금으로 월 30만 원 받으면 세금?')
    check('[P5] (구) 연금 수령: 비과세 · 연금저축계좌 연금소득세 안 씀', s[0]['intent'] == 'old' and '비과세' in d['세금 계산']['detail']
          and '3.3~5.5% (R-07)' not in text(s))
    s, d = path('(구)개인연금저축 300만 원 납입하면 세액공제 얼마?')
    check('[P6] (구) 납입: 소득공제 40% · 72만 원 — 세액공제(R-01·R-02) 계산 없음', '소득공제' in d['요건 판정']['detail']
          and '72만' in d['요건 판정']['detail'] and 'calc' not in d['세금 계산'] and '공제 대상 300만 원' not in text(s))
    for q in ('연금저축 60세인데 해지하면 세금은?', '60세인데 연금저축 2000만 원 해지하면 세금 얼마예요?'):
        s, d = path(q)
        check(f'[P7] {q}: 55세 이후 해지 — "55세 전" 단정 없음 · 개시 여부를 물음',
              s[0]['intent'] == 'withdraw' and '55세 전' not in d['개체 인식']['detail'] and '55세 전 인출·해지 →' not in d['요건 판정']['detail']
              and 'attr.started' in missing(d) and '330만' not in text(s), d['요건 판정']['detail'][:120])
    for q in ('IRP 해지하면 세금은?', '연금저축 해지하면 세금은?'):
        s, d = path(q)
        check(f'[P7] {q}: 나이를 모르면 R-04 를 조건으로(55세 전이면 / 55세 이후 개시 후면)',
              '나이 확인 필요 — 55세 전 해지' in d['요건 판정']['detail'] and 'attr.age' in missing(d))
    s, d = path('연금저축 해지 말고 일부만 인출하면 세금은?')
    check('[P7] "해지 말고"를 해지로 읽지 않음', s[0]['intent'] == 'withdraw')
    for q in ('52세 연금저축에 세액공제 받은 돈 1000만 원 있는데 세금은?', '50세인데 IRP에 퇴직금 5000만 원 있어요. 세금은?'):
        s, d = path(q)
        check(f'[P8] {q}: R-04 ✗ 면 연금외수령만 — 한도 안 연금소득세·감면 칸 없음',
              s[0]['intent'] == 'early' and all(m == '연금외수령' for _, m in cells(q))
              and 'attr.balance' not in missing(d) and 'attr.year_n' not in missing(d), str(cells(q)))
    for q in ('IRP 가입 3년인데 60세에 1000만 원 인출하면 세금은?', '연금저축 가입한 지 3년인데 60세에 1000만 원 인출하면 세금?'):
        s, d = path(q)
        check(f'[P9] {q}: 가입 3년 ✗ → 연금외수령', '가입 3년 ✗' in d['요건 판정']['detail']
              and cells(q) == [['free', '연금외수령'], ['credit', '연금외수령']])
    s, d = path('60세 IRP 연금 개시 안 하고 1000만 원 빼면 세금은?')
    check('[P10] 개시 안 하고 → 연금외수령(doc5 표)', '연금 개시 신청 전 인출' in d['요건 판정']['detail']
          and all(m == '연금외수령' for _, m in cells('60세 IRP 연금 개시 안 하고 1000만 원 빼면 세금은?')))
    s, d = path('57세 IRP 평가액 1억 연금수령 2년차 2000만 원 인출하면 세금은?')
    check('[P10·V5] 개시 여부를 모르면 가정으로 밝힘', '연금 개시 신청 후(질문에 없음' in d['요건 판정']['detail'])
    s, d = path('연금저축 요양 때문에 중도인출하면 세금은?')
    check('[P11] 연금저축 부득이: 근퇴법 사유(R-13)·무주택 여부를 붙이지 않음', 'R-13' not in d['요건 판정']['nodes']
          and '연금저축은 사유와 상관없이' in d['요건 판정']['detail'] and 'attr.homeless' not in missing(d))
    r11 = R['R-11']['path_text']
    check('[P11] 부득이 경로 R-11: 세법상 부득이 사유 목록(사망·해외이주·3개월 요양)', all(w in r11 for w in ('사망', '해외이주', '3개월 이상 요양')))
    s, d = path('IRP 해외이주로 해지하면 세금은?')
    check('[P11·V7] IRP 해외이주: 전액 해지만(doc20)', '부분 인출이 아닌 전액 해지만' in d['요건 판정']['detail'])
    s, d = path('IRP 요양 1000만 원 중도인출하면 세금 얼마?')
    check('[P12] 요양: 한도 안 연금소득세 · 한도 밖 연금외수령 · 한도 증빙을 물음', ['credit', '연금외수령'] in cells('IRP 요양 1000만 원 중도인출하면 세금 얼마?')
          and '한도 밖: 기타소득세 16.5%' in d['세금 계산']['detail'] and 'attr.care_cost' in missing(d))
    s, d = path('IRP 재난으로 중도인출 세금')
    check('[P12·D5] 재난: 15일 이상 입원 조건 · 한도', '15일 이상 입원' in d['요건 판정']['detail'] and '한도' in d['요건 판정']['detail'])
    for q in ('ISA 중도인출 돼요?', 'ISA 중도해지하면 세금은?'):
        s, d = path(q)
        check(f'[P13] {q}: ISA 는 연금계좌가 아님 — 55세·16.5% 단정 없음', s[0]['intent'] == 'isa_only' and cells(q) == []
              and 'R-04' not in text(s) and '받은 문서에서 확인되지 않음' in d['세금 계산']['detail'])
    s, d = path('퇴직금제도 회사 다니는데 IRP 중도인출 돼요?')
    check('[P14] "퇴직금제도"는 제도 이름 — IRP 안의 퇴직금(②)으로 보지 않음', 'fund.retire' not in d['개체 인식']['nodes']
          and 'plan.severance' in d['개체 인식']['nodes'] and ['retire', '연금외수령'] not in cells('퇴직금제도 회사 다니는데 IRP 중도인출 돼요?'))
    s, d = path('50세인데 IRP에서 1000만 원 인출하면 세금은?')
    check('[P15] IRP 55세 전 사유 없음: 일부 인출 불가 — 전액 해지로 봄', '해지 금액(IRP 전액)' in d['개체 인식']['detail']
          and any('일부만 꺼낼 수 없어' in a for a in d['빠진 속성 확인']['assumed']))
    s, d = path('IRP 가입자 사망해서 해지하면 세금은?')
    check('[P15] IRP 사망: "올해 빼려는 금액" 대신 계좌 평가액(전액 해지)', any('전액 해지' in m['ask'] for m in d['빠진 속성 확인']['missing'])
          and not any(m['ask'] == '올해 빼려는 금액' for m in d['빠진 속성 확인']['missing']))
    for q in ('IRP에 퇴직금 들어 있는데 연 2000만 원 연금으로 받으면 세금은?', 'DB형에서 연금으로 월 100만 원 받으면 세금은?'):
        s, d = path(q)
        check(f'[P16] {q}: ② 재원만이면 R-07·R-08(③ 규칙)을 걸지 않음', 'R-07' not in d['근거 연결']['nodes']
              and 'R-08' not in d['근거 연결']['nodes'] and 'R-09' in d['근거 연결']['nodes'])
    s, d = path('IRP 파산으로 중도인출하면 세금은?')
    check('[P17·V14] R-13 문장: 담보대출 상환 · 최근 5년 이내', '담보대출' in d['요건 판정']['detail'] and '최근 5년 이내' in d['요건 판정']['detail'])
    s, d = path('IRP에 퇴직금 5000만 원 있는데 해외이주하면 세금은?')
    check('[P18] ② × 해외이주: 이연퇴직소득세 × 70% · 입금 후 3년을 물음', '이연퇴직소득세 × 70%' in d['세금 계산']['detail']
          and 'attr.retire_3y' in missing(d) and ['retire', '부득이'] in cells('IRP에 퇴직금 5000만 원 있는데 해외이주하면 세금은?'))
    for q in ('기업형 IRP 중도인출 돼요?', '기업형IRP 해지하면 세금은?', '기업형 IRP 55세 전에 해지하면?'):
        s, d = path(q)
        check(f'[P19] {q}: 기업형 IRP(회사 경유) — 개인형 IRP 경로 아님', 'plan.irp_corp' in d['개체 인식']['nodes']
              and 'account.irp' not in d['개체 인식']['nodes'] and '회사를 거쳐' in d['요건 판정']['detail'] and cells(q) == [])


# ---------------------------------------------------------------------------
# V — 시연 화면(그래프·세금 지도·열 머리)
# ---------------------------------------------------------------------------
def test_views():
    g = ontology.graph()
    ids = [n['id'] for n in g['nodes']]
    check('[V] 그래프 노드 60개 이하', len(ids) <= ontology.GRAPH_MAX, str(len(ids)))
    es = {(e['from'], e['to']): e['label'] for e in g['edges']}
    check('[V2] 그래프 R-12 → DB = "중도인출 불가"(적용된다 아님)', es.get(('R-12', 'plan.db')) == '중도인출 불가')
    r12 = next(n for n in g['nodes'] if n['id'] == 'R-12')
    check('[V2] R-12 요약에 DB·DC 가 빠지지 않음', 'DB' in r12['summary'] and 'DC' in r12['summary'], r12['summary'])
    s, d = path('62세인데 연금저축 해지하면 세금 얼마예요?')
    check('[V4] 62세 해지: "55세 전" 단정 없음', '55세 전 인출·해지 →' not in d['요건 판정']['detail'] and s[0]['intent'] == 'withdraw')
    s, d = path('60세인데 IRP 3000만원 해지하면 세금은?')
    check('[V4] 60세 IRP 3,000만 원 해지: 495만 원(전부 16.5%) 단정 없음', '495만' not in text(s) and '(55세 전' not in d['개체 인식']['detail'])
    hint = {m['key']: m['hint'] for m in ontology.overview()['modes']}
    check('[V5] 세금 지도 열 머리: 개시 신청·가입 5년 · 개시 전 인출', '개시 신청' in hint['연금수령'] and '가입 5년' in hint['연금수령']
          and '개시 전' in hint['연금외수령'], str(hint))
    q = '무주택자 주택 구입은 부득이한 사유라서 IRP 중도인출하면 5.5%죠?'
    s, d = path(q)
    check('[V6] 고객 전제(주택 구입 = 부득이)를 바로잡음 — 연금외수령', s[0]['intent'] == 'early'
          and all(m == '연금외수령' for _, m in cells(q)) and '세법상 부득이한 사유가 아님' in d['요건 판정']['detail'])
    s, d = path('해외이주하는데 IRP 일부만 중도인출할 수 있나요?')
    check('[V7] 해외이주 IRP 일부 인출 질문: 전액 해지만', '전액 해지만' in d['요건 판정']['detail'] and 'R-13: 무주택자' not in d['요건 판정']['detail'])
    ov = {(c['source'], c['mode']): c for c in ontology.overview()['cells']}
    check('[V8] ③ × 부득이 칸 원문에 요양 한도(doc20 200만원 …)', any('200만원' in qq['text'] for qq in ov[('credit', '부득이')]['quotes']))
    check('[V9] 그래프: R-15 → 연금저축계좌 "적용된다" 없음', ('R-15', 'account.savings') not in es)
    check('[V10] ② × 연금수령 세목 = 연금소득세(doc1·doc21 표), 07 글자는 md07_tax', CELLS[('retire', '연금수령')]['tax'] == '연금소득세'
          and CELLS[('retire', '연금수령')].get('md07_tax') == '퇴직소득세')
    check('[V10] 그래프 ② → 연금소득세(연금수령) · ② → 퇴직소득세(연금외수령)', '연금수령' in es.get(('fund.retire', 'tax.pension'), '')
          and es.get(('fund.retire', 'tax.retire')) == '과세된다(연금외수령)')
    check('[V11] 그래프 DC·IRP → 위험자산(70%)', es.get(('account.dc', 'product.risk')) == '허용한다(70%)')
    s, d = path('DB형 퇴직연금에 TDF 100% 담을 수 있나요?')
    check('[V11] DB TDF 질문: DB 는 TDF 도 70%까지(doc58)', 'DB 는 적격 TDF 도 70%까지' in d['요건 판정']['detail'])
    check('[V12] ③ × 연금수령 칸·R-07 요약: 종신연금 3.3%(doc38)', '종신연금' in CELLS[('credit', '연금수령')]['plain']
          and '종신' in R['R-07']['summary'])
    check('[V13] 그래프: 제도 변경 → R-08 없음, 세법 개정 → R-08', ('event.plan_change', 'R-08') not in es and ('event.tax_reform', 'R-08') in es)
    for q in ('IRP 주택 구입으로 중도인출하면 세금은?', '60세인데 IRP 3000만원 해지하면 세금은?'):
        s, d = path(q)
        ok = ('퇴직금은 없다고 봄' in d['인출 순서']['detail'] and 'attr.has_retire' in missing(d)) if s[0]['intent'] == 'early' else \
            '전부 ③' in ' '.join(d['빠진 속성 확인']['assumed'])
        check(f'[V15] {q}: ② 퇴직금 없음을 가정으로 밝힘(되묻기)', ok, d['인출 순서']['detail'])
    s, d = path('50세인데 연금저축 2,000만 원 해지하면 세금은?')
    check('[V15] "최대"는 퇴직금이 없다고 볼 때만', '퇴직금이 없고 전부 ③' in d['세금 계산']['detail'])
    qc = ontology.question_cells('DB형인데 해지할 수 있어요?')
    check('[V1] 세금 지도: DB 질문은 칸 없이 이유 한 줄(note)', qc['cells'] == [] and 'DB' in qc['note'])
    html = open(os.path.join(ROOT, 'src', 'static', 'demo.html'), encoding='utf-8').read()
    check('[V3] 시연 화면 세금 지도 머리말: 연금계좌 기준 — DC·DB 다름', '연금계좌(연금저축·IRP) 기준' in html and 'hit.note' in html)


# ---------------------------------------------------------------------------
# G — 온톨로지 길잡이
# ---------------------------------------------------------------------------
def test_guide():
    for q in ('DB형인데 해지할 수 있어요?', 'DB 중도인출 되나요?', 'DB에서 DC로 바꾸면 중도인출 되나요?'):
        got = picked(q)
        check(f'[G-a] {q}: DB 개념(G21) · 연금계좌 재원·요건·감면 개념 없음', 'G21' in got and not {'G01', 'G02', 'G03', 'G05', 'G10'} & set(got), str(got))
    for q in ('DC형 운용수익 세금은?', 'DC 중도인출하면 세금은?', 'DC에서 펀드 팔고 다른 펀드로 바꾸면 세금 내요?'):
        got = picked(q)
        check(f'[G-b] {q}: DC 세금 개념(G22) · G01·G03·G05·G10 없음', 'G22' in got and not {'G01', 'G03', 'G05', 'G10'} & set(got), str(got))
    t = concept('G09')['text']
    check('[G-c] G09: "어느 계좌든 16.5%" 없음 · 재원별(퇴직금은 퇴직소득세) · DC', '어느 계좌든' not in t and '퇴직금은 퇴직소득세' in t and 'DC 중도인출은' in t)
    t = concept('G08')['text']
    check('[G-d·G-r] G08: DC 는 나이와 상관없이 사유 · DB→DC 전환 · (구) 불가', 'IRP·DC는 만 55세 전' not in t and 'DC는 나이와 상관없이' in t
          and 'DC로 제도를 바꾼 뒤' in t and '(구)개인연금저축은 중도인출이 안 되고' in t)
    for q in ('개인연금저축 중도인출 돼요?', '개인연금저축 해지하면 세금은?', '2000년에 가입한 개인연금저축 비과세인가요?'):
        got = picked(q)
        nodes, _ = onto_guide.question_nodes(q)
        check(f'[G-e] {q}: (구)개인연금저축 개념(G23) · 연금저축계좌로 읽지 않음', 'G23' in got and 'G01' not in got and 'G17' not in got
              and 'account.savings' not in nodes, f'{got} {sorted(nodes)}')
    check('[G-f] G02: IRP 근퇴법 연금 지급기간 5년', 'IRP는 근퇴법상 연금 지급기간이 5년 이상' in concept('G02')['text'])
    t = concept('G14')['text']
    check('[G-f] G14: "IRP에 쓰지 마라" 없음 · IRP 정기연금 최소 5년', '연금저축계좌·IRP에 쓰지 마라' not in t and '최소 5년' in t)
    check('[G-g] G12: 55세 이상이면 DC 퇴직금도 일반계좌', 'DC 퇴직금도 일반계좌로' in concept('G12')['text'])
    check('[G-h] G20: 퇴직금이 있으면 5년이 안 돼도 이체', '5년이 안 돼도 55세 이상이면' in concept('G20')['text']
          and '전액 이체일 때만 된다' not in concept('G20')['text'])
    check('[G-i] G19: IRP 는 55세 전 법정 사유가 있어야 인출', 'IRP는 55세 전에는' in concept('G19')['text'])
    t = concept('G16')['text']
    check('[G-j] G16: 천재지변(물적 피해)과 재난피해를 나눔 · 재난피해도 한도', '천재지변(물적·인적 피해' in t and '요양(3개월 이상)·재난피해는 한도' in t)
    check('[G-k] G10: 11년차·부득이 예외', '11년차부터는 한도가 없어' in concept('G10')['text'] and '부득이한 사유로 꺼내면' in concept('G10')['text'])
    check('[G-l] G18: 퇴직연금도 레버리지·인버스 불가', '퇴직연금(DC·IRP)도 레버리지·인버스' in concept('G18')['text'])
    check('[G-m] G01·G04: 종신연금 3.3%', '종신연금' in concept('G01')['text'] and '종신연금' in concept('G04')['text'])
    check('[G-n] always: 연금계좌 ↔ 회사 퇴직연금(DC·DB) 짝', '회사 퇴직연금(DC·DB)' in onto_guide.load()['always'])
    rx = onto_guide.load()['rule_lexicon']['R-09']
    check('[G-o] R-09 표현이 "퇴직금은 연금저축으로"에 걸리지 않음', not re.search(rx, 'DC 퇴직금은 연금저축으로 받을 수 있나요?'))
    check('[G-o] "DC 퇴직금은 연금저축으로 받을 수 있나요?" → G12', 'G12' in picked('DC 퇴직금은 연금저축으로 받을 수 있나요?'))
    check('[G-o] "퇴직금 받은 IRP를 연금저축으로 옮길 수 있나요? 56세" → G13', 'G13' in picked('퇴직금 받은 IRP를 연금저축으로 옮길 수 있나요? 56세입니다'))
    lx = onto_guide.load()['lexicon']
    check('[G-p] plan.old 표현이 "연금저축계좌(구)"에 걸리지 않음', not re.search(lx['plan.old'], '연금저축계좌(구)는 연금 받는 방법이 어떻게 돼요?')
          and not re.search(lx['plan.old'], '2010년에 가입한 연금저축(구) 계좌 중도인출 돼요?'))
    check('[G-q] event.retire 표현이 "퇴직연금"에 걸리지 않음', not re.search(lx['event.retire'], 'DB형 퇴직연금에서 ETF 살 수 있나요?')
          and 'G12' not in picked('DB형 퇴직연금에서 ETF 살 수 있나요?'))
    check('[G-s] G09: 합산 기준은 없음 + 요양·재난피해 한도', '합산 기준은 없다(단, 요양·재난피해는 200만 원' in concept('G09')['text'])
    check('[G-t] G13: 종신연금 계좌 이체 불가 · 개시 계좌 · 2013.3.1 · 개인연금저축끼리', all(w in concept('G13')['text'] for w in
          ('종신연금을 받는 계좌는 이체할 수 없', '잔고 없는 신규계좌', '2013년 3월 1일 이후', '개인연금저축끼리')))
    check('[G-u] G04: 2013.3.1 전 가입한 DC·DB', '2013년 3월 1일 전에 가입한 DC·DB' in concept('G04')['text'])
    srcs = concept('G14')['sources']
    check('[G-v] G14: 연금저축 개시 후 변경은 doc2 원문, doc21 은 IRP 문장 근거', any(s['doc'] == 'doc2.txt' and '임의식으로 개시 신청한 이후' in s['quote'] for s in srcs)
          and 'IRP는 정기연금' in concept('G14')['text'])
    check('[G-w] G17: 2025년 해외 배당 세후', '2025년부터 해외 배당소득' in concept('G17')['text'])
    check('[G-x] G12: 60일 — 일반 퇴직급여는 IRP, 공무원 퇴직수당은 연금저축·IRP', '수령일부터 60일 안에 IRP' in concept('G12')['text']
          and '공무원 퇴직수당' in concept('G12')['text'])
    # 개념 문장의 숫자는 원문에 있어야 한다(test_rules 와 같은 규칙) — 새 개념 포함
    num = re.compile(r'\d[\d,]*(?:\.\d+)?')
    for c in onto_guide.load()['concepts']:
        have = {x.replace(',', '') for _, q in onto_guide.quotes(c) for x in num.findall(q)}
        miss = [x for x in num.findall(c['text']) if x.replace(',', '') not in have]
        check(f"[G] {c['id']} 숫자가 원문에 있음", not miss, str(miss))


# ---------------------------------------------------------------------------
# T — 조각 순위·펀드 분류
# ---------------------------------------------------------------------------
PENSION_ONLY_FUNDS = ['KR5153450112', 'KR510902777M', 'KR510902773M', 'KR5153450431', 'KR5153420022', 'KR5113420012',
                      'KR5113450111', 'KR5174420011', 'KR5114450222', 'KR5114420022', 'KR5123365001']
FLOOR_FUNDS = ['KR5123420015', 'KR5131420025', 'KR5110501016', 'KR5123420039', 'KR5118420036', 'KR5153420079',
               'KR5116501001', 'KR5114420046', 'KR5119520012']


def _ws(t):
    return re.sub(r'\s+', '', t)


def test_tags():
    ft = onto_link.fund_table()
    for fid in PENSION_ONLY_FUNDS:
        f = ft.get(fid + '.txt') or {}
        line = onto_link.fund_line({'file': fid + '.txt'})
        body = open(os.path.join(PROS, fid + '.txt'), encoding='utf-8').read()
        ev = f.get('accounts_evidence') or {}
        check(f'[T1] {fid}: 연금저축 전용 — DC·IRP 위험자산 70% 줄 없음(투자설명서 가입자격 원문)',
              f.get('accounts') == ['연금저축'] and '연금저축계좌 전용' in line and '70%' not in line
              and 'DC·IRP) 위험자산 —' not in line and '비위험자산' not in line and '위험 자산 여부' not in line
              and ev.get('quote') and _ws(ev['quote']) in _ws(body), line)
    for fid in FLOOR_FUNDS:
        f = ft.get(fid + '.txt') or {}
        ev = f.get('floor_evidence') or {}
        body = open(os.path.join(PROS, fid + '.txt'), encoding='utf-8').read()
        check(f'[T4] {fid}: 근거에 투자설명서 사채 신용등급 하한(“하한 없음” 단정 고침)', '이상(doc58 투자적격' in f.get('risk_asset_basis', '')
              and ev.get('quote') and _ws(ev['quote']) in _ws(body))
    w, _, _ = onto_link.question_profile('DB형인데 해지할 수 있어요?')
    check('[T2] DB 해지 질문 순위 무게: 연금계좌 인출 노드(wd.other·R-04·R-06·R-10) 없음',
          not {'wd.other', 'R-04', 'R-06', 'R-10'} & set(w) and 'plan.db' in w and 'R-12' in w, str(w))
    w, _, _ = onto_link.question_profile('IRP 중도인출 할 수 있어요?')
    check('[T3] IRP 질문: account.irp 를 plan.irp 로도 펼침', w.get('plan.irp') and w.get('account.irp'), str(w))
    tab = onto_link.chunk_table()
    if tab.get('doc14.txt#5') and tab.get('doc39.txt#0'):
        check('[T3] IRP 중도인출 사유 조각(doc14#5) 겹침 ≥ 연금수령한도 조각(doc39#0)',
              onto_link.overlap(tab['doc14.txt#5'], w, set()) > onto_link.overlap(tab['doc39.txt#0'], w, set()))
    w, _, _ = onto_link.question_profile('DC형 퇴직연금 주택구입으로 중도인출하면 세금은?')
    if tab.get('doc41.txt#1') and tab.get('doc49.txt#0'):
        check('[T2·T3] DC 주택구입: DC 중도인출 조각(doc49#0) > 연금저축·IRP 글(doc41#1)',
              onto_link.overlap(tab['doc49.txt#0'], w, set()) > onto_link.overlap(tab['doc41.txt#1'], w, set()), str(w))


# ---------------------------------------------------------------------------
# Q — 원문 인용이 글자 그대로인가
# ---------------------------------------------------------------------------
def _walk(x):
    if isinstance(x, dict):
        f = x.get('doc') or x.get('file')
        qt = x.get('quote') or (x.get('text') if 'file' in x else None)
        if isinstance(f, str) and isinstance(qt, str) and re.match(r'^doc\d+(?:\.txt)?$', f):
            yield f if f.endswith('.txt') else f + '.txt', qt, x.get('match')
        for v in x.values():
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)


def test_quotes():
    for name in ('ontology.json', 'onto_guide.json'):
        raw = json.load(open(os.path.join(ROOT, 'data', name), encoding='utf-8'))
        n = bad = 0
        for f, qt, m in _walk(raw):
            body = open(os.path.join(DOCS, f), encoding='utf-8').read()
            ok = (_ws(qt) in _ws(body)) if m == 'ws' else (qt in body)
            n += 1
            if not ok:
                bad += 1
                check(f'[Q] {name} {f}: {qt[:30]}… 원문 그대로', False)
        check(f'[Q] {name} 원문 인용 {n}개 모두 글자 그대로', n >= 50 and not bad, f'{bad}개 불일치')
    pr = json.load(open(os.path.join(ROOT, 'data', 'ontology.json'), encoding='utf-8'))['plan_rules']
    for key in ('plan.db', 'account.dc', 'plan.old', 'retire_route', 'plan.isa', 'plan.severance', 'plan.irp_corp', 'pension'):
        check(f'[Q] plan_rules {key}: 원문 인용이 붙어 있음', len(pr[key].get('quotes', [])) >= 1)


# ---------------------------------------------------------------------------
# 9 — 9차 재검수(903~910)에서 남은 것. 자세한 재현은 src/test_fix1014_onto9.py
# ---------------------------------------------------------------------------
def test_round9():
    for q in ('50세 연금저축 주택 구입으로 1000만 원 중도인출하면 세금은?', '연금저축 담보대출 갚으려고 중도인출하면 세금은?'):
        s, d = path(q)
        check(f'[9-903] {q}: 연금저축에 근퇴법 사유(R-13)·무주택 여부 없음(doc20 사유무관)',
              'R-13' not in ontology.question_cells(q)['rule_ids'] and '무주택' not in ' '.join(d['빠진 속성 확인']['assumed'])
              and '세법상 부득이한 사유가 아님' in d['요건 판정']['detail'])
    s, d = path('IRP 가입 3년인데 60세에 1000만 원 인출하면 세금은?')
    check('[9-904] IRP 60세 가입 3년: 전액 해지 · 평가액을 물음', '해지 금액(IRP 전액)' in d['개체 인식']['detail']
          and 'attr.balance' in missing(d) and 'IRP는 55세 전에는' not in d['요건 판정']['detail'])
    check('[9-905] R-13 조건: DC 전월세는 해당 사업장 재직 중 1회만(doc55)', '해당 사업장 재직 중 1회만' in R['R-13']['condition'])
    es = {(e['from'], e['to']): e['label'] for e in ontology.graph()['edges']}
    check('[9-906] 그래프 ② → 연금소득세: 이연퇴직소득세 × 70~50%', '이연퇴직소득세 × 70~50%' in es.get(('fund.retire', 'tax.pension'), ''))
    s, d = path('미국 영주권 취득했는데 연금저축 해지하면 세금 낮아지나요?')
    check('[9-908] 영주권: 해외이주로 단정하지 않음 · doc20 조건 · 신고확인서를 물음', '사건 = 해외이주' not in d['개체 인식']['detail']
          and '영주권 취득만으로는 인정되지 않음' in d['요건 판정']['detail'] and 'attr.emigrate_proof' in missing(d))
    check('[9-909] R-03 조건: 세액공제대상금액만 ③, 나머지 ①(doc4) — "(최대 300만 원)은 ③" 단정 없음',
          '세액공제대상금액' in R['R-03']['condition'] and '공제받은 금액(최대 300만 원)은 ③' not in R['R-03']['condition'])
    s, d = path('연금저축에 퇴직금 넣을 수 있어요?')
    check('[9-910] 연금저축에 퇴직금 넣을 수 있어요? → 받는 길(doc51 표) · 세금 칸 없음', s[0]['intent'] == 'retire_route'
          and cells('연금저축에 퇴직금 넣을 수 있어요?') == [] and 'account.savings' in d['요건 판정']['nodes'])


def main():
    for t in (test_data, test_path, test_views, test_guide, test_tags, test_quotes, test_round9):
        try:
            t()
        except Exception as e:                      # noqa: BLE001 — 한 묶음이 죽어도 나머지는 돈다
            import traceback
            check(f'{t.__name__} 예외', False, traceback.format_exc()[-600:])
    fail = [(n, w) for n, ok, w in results if not ok]
    print(f'{len(results) - len(fail)}/{len(results)} 통과')
    for n, w in fail:
        print(f'  [실패] {n} — {w}')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
