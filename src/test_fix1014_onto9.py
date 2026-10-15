# -*- coding: utf-8 -*-
"""
9차 검수(온톨로지 재검수) 결함 재현 시험 — 묶음 onto9. API 없이 돈다.
    PYTHONIOENCODING=utf-8 python src/test_fix1014_onto9.py

  903  연금저축 + 주택·전세·담보대출에 근퇴법 사유 꼬리표(R-13·무주택 여부)를 붙이지 않는다(doc20 표 '사유무관 중도인출 가능')
  904  IRP 55세 이상이라도 개시 전·가입 5년 미만 인출은 일부 인출이 아니라 전액 해지 — 평가액을 묻는다(doc14·doc22)
  905  R-13 DC 조건에 '전∙월세 사유로 중도인출은 해당 사업장 재직 중 1회만 가능'(doc55)
  906  그래프 ② → 연금소득세 엣지에 '이연퇴직소득세 × 70~50%'(③ 5.5~3.3% 와 구분)
  908  영주권·장기체류·해외 이사는 그 자체로 해외이주(부득이)가 아니다 — doc20 원문으로 조건을 밝히고 신고확인서를 묻는다
  909  R-03 조건: 전환액 중 세액공제대상금액(다른 납입이 없으면 일반 한도분 + 추가분)만 ③, 나머지 ①(doc4) — 경로에 가정을 밝힌다
  910  '연금저축에 퇴직금 넣을 수 있어요?'는 입금 길 질문 — 인출 갈래가 아니라 퇴직급여 받는 길(doc51 표)

같은 원인의 다른 표현과, 고치며 깨지면 안 되는 이웃 질문(IRP 주택 구입은 R-13 유지 등)도 함께 본다.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ontology  # noqa: E402

ROOT = ontology.ROOT
DOCS = os.path.join(ROOT, 'extracted', 'docs')
O = ontology.load()
R = O['rule_index']
results = []


def check(name, ok, why=''):
    results.append((name, bool(ok), why))


def path(q):
    s = ontology.path_for(q)
    return s, {x['step']: x for x in s}


def missing(d):
    return [m['attr'] for m in d['빠진 속성 확인'].get('missing', [])]


def asks(d):
    return [m['ask'] for m in d['빠진 속성 확인'].get('missing', [])]


def assumed(d):
    return ' | '.join(d['빠진 속성 확인'].get('assumed', []))


def doc(f):
    return open(os.path.join(DOCS, f), encoding='utf-8').read()


# ---------------------------------------------------------------------------
# 903 — 연금저축에는 근퇴법 사유(R-13)·무주택 여부를 붙이지 않는다
# ---------------------------------------------------------------------------
def test_903():
    for q in ('50세 연금저축 주택 구입으로 1000만 원 중도인출하면 세금은?', '연금저축 담보대출 갚으려고 중도인출하면 세금은?',
              '52세 연금저축 전세자금으로 500만 원 빼면 세금은?', '연금저축 집 사려고 해지하면 세금은?',
              '연금저축 전세 보증금 때문에 해지하면 세금은?', '주택 구입은 부득이한 사유라서 연금저축 인출하면 5.5%죠?'):
        s, d = path(q)
        qc = ontology.question_cells(q)
        check(f'[903] {q}: R-13·무주택 여부 없음 · 세법상 부득이한 사유 아님(R-11, doc20)',
              'R-13' not in qc['rule_ids'] and 'R-13' not in d['근거 연결']['nodes'] and 'R-13' not in d['요건 판정']['nodes']
              and 'attr.homeless' not in missing(d) and '무주택' not in assumed(d)
              and '세법상 부득이한 사유가 아님' in d['요건 판정']['detail'] and 'R-11' in d['근거 연결']['nodes']
              and all(m == '연금외수령' for _, m in qc['cells']), f"{qc['rule_ids']} / {assumed(d)}")
        check(f'[903] {q}: 인출 사유를 다시 묻지 않음(사유는 질문에 있음)', 'attr.reason' not in missing(d), str(missing(d)))
    # 이웃 — IRP 는 근퇴법 사유가 맞다(R-13·무주택 여부 유지)
    for q in ('IRP 주택 구입으로 1000만 원 중도인출하면 세금은?', '무주택자 주택 구입은 부득이한 사유라서 IRP 중도인출하면 5.5%죠?',
              '연금저축이랑 IRP 주택 구입으로 중도인출하면 세금은?'):
        s, d = path(q)
        qc = ontology.question_cells(q)
        check(f'[903 이웃] {q}: IRP 는 R-13 유지', 'R-13' in qc['rule_ids'] and 'R-13' in d['요건 판정']['nodes'], str(qc['rule_ids']))
    s, d = path('IRP 주택 구입으로 1000만 원 중도인출하면 세금은?')
    check('[903 이웃] IRP 주택 구입: 무주택 여부 가정 유지', '무주택' in assumed(d), assumed(d))
    # 같은 원인 — 사망·해외이주는 근퇴법 사유가 아니다(doc20) — 부득이 갈래에서도 R-13 을 붙이지 않는다
    for q in ('해외이주하면 IRP 해지 세금은?', '해외이주하면 연금 해지 세금은?',
              '가족이랑 캐나다로 이민 갑니다. 출국 전에 연금저축이랑 IRP를 정리해야 하는데요, 아직 55세도 안 됐고 '
              '이민은 중도인출 사유도 아니라던데 그냥 깨면 16.5% 다 떼이는 거죠?'):
        qc = ontology.question_cells(q)
        check(f'[903 같은 원인] {q[:30]}…: 해외이주에 R-13 없음', 'R-13' not in qc['rule_ids'], str(qc['rule_ids']))
    s, d = path('해외이주하면 연금 해지 세금은?')
    check('[903 같은 원인] 계좌를 모르는 해외이주: IRP 전액 해지 · 연금저축 일부 가능 둘 다',
          '전액 해지만' in d['요건 판정']['detail'] and '연금저축은 사유와 상관없이' in d['요건 판정']['detail'])
    for q in ('IRP 요양 1000만 원 중도인출하면 세금 얼마?', '요양 때문에 중도인출하면 세금은?'):
        qc = ontology.question_cells(q)
        check(f'[903 이웃] {q}: 요양(근퇴법 사유)은 R-13 유지', 'R-13' in qc['rule_ids'], str(qc['rule_ids']))


# ---------------------------------------------------------------------------
# 904 — IRP 개시 전·가입 5년 미만은 전액 해지, 평가액을 묻는다
# ---------------------------------------------------------------------------
def test_904():
    for q, tag in [('60세 IRP 연금 개시 안 하고 1000만 원 빼면 세금은?', '연금 개시 신청 전'),
                   ('IRP 가입 3년인데 60세에 1000만 원 인출하면 세금은?', '가입 3년'),
                   ('58세인데 IRP 개시 전에 500만 원 인출하면 세금은?', '연금 개시 신청 전'),
                   ('IRP 가입 2년 됐는데 62세에 2000만 원 빼면 세금은?', '가입 2년'),
                   ('62세인데 IRP 연금 개시 신청 전에 2000만 원 꺼내면 세금은?', '연금 개시 신청 전'),
                   ('IRP 가입한 지 2년 됐고 57세인데 500만 원 찾으면 세금?', '가입 2년')]:
        s, d = path(q)
        qc = ontology.question_cells(q)
        ent, req = d['개체 인식']['detail'], d['요건 판정']['detail']
        check(f'[904] {q}: 일부 인출이 아니라 해지 금액(IRP 전액) · 평가액(전액 해지)을 물음',
              '해지 금액(IRP 전액)' in ent and tag in ent and '일부만 꺼낼 수 없어' in assumed(d)
              and 'attr.balance' in missing(d) and any('전액 해지' in a and '평가액' in a for a in asks(d)), f'{ent} / {asks(d)}')
        check(f'[904] {q}: R-12 문장이 개시 전·5년 요건으로 설명(55세 전 문장 아님, doc14·doc22)',
              '연금 개시 전 인출은 전액 해지' in req and '55세 이상 & 가입기간 5년 이상' in req and 'IRP는 55세 전에는' not in req, req)
        check(f'[904] {q}: 세금 칸은 연금외수령만', qc['cells'] and all(m == '연금외수령' for _, m in qc['cells']), str(qc['cells']))
    s, d = path('60세 IRP 연금 개시 안 하고 해지하면 세금은?')
    check('[904] 금액 없는 개시 전 해지: "올해 빼려는 금액" 대신 IRP 평가액(전액 해지)',
          any('해지할 IRP 계좌 평가액' in a for a in asks(d)) and '올해 빼려는 금액' not in asks(d), str(asks(d)))
    # 55세 전('age')도 같은 처리 — 금액이 있으면 평가액을 묻는다
    s, d = path('50세인데 IRP에서 1000만 원 인출하면 세금은?')
    check('[904] 55세 전 IRP 인출: 해지 금액(IRP 전액) · 평가액을 물음', '해지 금액(IRP 전액)' in d['개체 인식']['detail']
          and 'attr.balance' in missing(d))
    # 평가액을 말했으면 '그 금액 = 계좌 전액'이라 하지 않는다
    s, d = path('50세 IRP 평가액 3000만 원인데 1000만 원 빼면 세금은?')
    check('[904] 평가액이 더 크면: 해지하면 평가액 전액이라고 밝힘(1,000만 원을 계좌 전액으로 보지 않음)',
          '해지 금액(IRP 전액) = 1,000만' not in d['개체 인식']['detail'] and '평가액 3,000만 원 전액 해지' in assumed(d)
          and '해지하면 평가액 3,000만 원 전액' in d['세금 계산']['detail'], d['세금 계산']['detail'][-120:])
    # 이웃 — 연금저축은 일부 인출이 된다 / IRP 도 법정 사유가 있으면 일부 인출
    s, d = path('연금저축 가입 3년인데 60세에 1000만 원 인출하면 세금은?')
    check('[904 이웃] 연금저축 가입 3년: 일부 인출 그대로(전액 해지로 보지 않음)', '인출 금액 = 1,000만 원' in d['개체 인식']['detail']
          and 'attr.balance' not in missing(d) and '연금저축은 사유와 상관없이' in d['요건 판정']['detail'])
    s, d = path('58세인데 IRP 개시 전에 주택 구입으로 500만 원 인출하면 세금은?')
    check('[904 이웃] IRP 개시 전 + 주택 구입(법정 사유): 일부 인출로 봄', '인출 금액 = 500만 원' in d['개체 인식']['detail']
          and '일부만 꺼낼 수 없어' not in assumed(d) and 'R-13' in d['요건 판정']['nodes'])


# ---------------------------------------------------------------------------
# 905 — R-13 DC 전월세 1회
# ---------------------------------------------------------------------------
def test_905():
    once = '- 전∙월세 사유로 중도인출은 해당 사업장 재직 중 1회만 가능'
    check('[905] doc55 원문 글자 그대로', once in doc('doc55.txt'))
    r13 = R['R-13']
    check('[905] R-13 조건: DC 전월세는 해당 사업장 재직 중 1회만 · IRP 는 횟수 제한 없음',
          'DC 는 해당 사업장 재직 중 1회만' in r13['condition'] and 'IRP 는 횟수 제한 없음' in r13['condition'], r13['condition'])
    check('[905] R-13 원문에 doc55 1회 · doc20 횟수 제한 없음', any(x['quote'] == once for x in r13['quotes'])
          and any('횟수 제한을 두지 않음' in x['quote'] for x in r13['quotes']))
    check('[905] doc20 원문 글자 그대로', '무주택자인 가입자의 주거 목적 전세보증금 부담 (횟수 제한을 두지 않음)' in doc('doc20.txt'))
    for q in ('DC 전세 보증금으로 중도인출하면 세금은?', 'DC 전세자금 중도인출 두 번 할 수 있어요?', 'DC형 중도인출 사유가 뭐예요?'):
        s, d = path(q)
        check(f'[905] {q}: DC 경로 R-13 문장에 재직 중 1회', s[0]['intent'] == 'plan_dc'
              and '전세금/월세보증금(해당 사업장 재직 중 1회만)' in d['요건 판정']['detail'], d['요건 판정']['detail'][:200])


# ---------------------------------------------------------------------------
# 906 — 그래프 ② → 연금소득세 라벨
# ---------------------------------------------------------------------------
def test_906():
    es = {(e['from'], e['to']): e['label'] for e in ontology.graph()['edges']}
    r, c = es.get(('fund.retire', 'tax.pension'), ''), es.get(('fund.credit', 'tax.pension'), '')
    check('[906] ② → 연금소득세: 이연퇴직소득세 × 70~50%', '이연퇴직소득세 × 70~50%' in r and '연금수령' in r, r)
    check('[906] ③ → 연금소득세: 5.5~3.3% — ② 와 라벨이 다름', '5.5~3.3%' in c and '이연퇴직' not in c and r != c, c)
    check('[906] 연금외수령 엣지 라벨은 그대로', es.get(('fund.retire', 'tax.retire')) == '과세된다(연금외수령)')
    for er in O['tax_matrix']['edge_rates']:
        for x in er['quotes']:
            check(f"[906] edge_rates 원문 {x['doc']} 글자 그대로", x['quote'] in doc(x['doc'] + '.txt'), x['quote'][:40])


# ---------------------------------------------------------------------------
# 908 — 영주권·장기체류·해외 이사
# ---------------------------------------------------------------------------
def test_908():
    src = '단순 장기체류나 영주권 취득만으로는 부득이한 사유로 인정되지 않는다.'
    check('[908] doc20 원문 글자 그대로', src in doc('doc20.txt'))
    check('[908] 해외이주 사건 표현이 영주권·해외로 이사에 걸리지 않음',
          not re.search(ontology._EMIGRATE_RX, '미국 영주권 취득했는데') and not re.search(ontology._EMIGRATE_RX, '해외로 이사 가는데')
          and re.search(ontology._EMIGRATE_RX, '해외이주하면') and re.search(ontology._EMIGRATE_RX, '이민 갑니다'))
    for q, acct in [('영주권 받아서 IRP 해지하면 세금은?', 'irp'), ('미국 영주권 취득했는데 연금저축 해지하면 세금 낮아지나요?', 'savings'),
                    ('해외로 이사 가는데 연금저축 해지하면 세금은?', 'savings'), ('외국에서 장기체류하게 돼서 연금저축 해지하면 세금은?', 'savings'),
                    ('영주권 나왔는데 IRP 깨면 세금 어떻게 돼요?', 'irp'), ('해외 장기 체류 예정인데 IRP 해지하면?', 'irp')]:
        s, d = path(q)
        qc = ontology.question_cells(q)
        ent = d['개체 인식']['detail']
        check(f'[908] {q}: "사건 = 해외이주"로 단정하지 않음 · 신고확인서를 물음(사유는 다시 묻지 않음)',
              '사건 = 해외이주' not in ent and 'event.emigrate' not in d['개체 인식']['nodes']
              and 'attr.emigrate_proof' in missing(d) and 'attr.reason' not in missing(d), f'{ent} / {missing(d)}')
        check(f'[908] {q}: 요건 판정에 doc20 원문 조건(영주권 취득만으로는 인정 안 됨 · 해외이주신고확인서)',
              '영주권 취득만으로는 인정되지 않음' in d['요건 판정']['detail'] and '해외이주신고확인서' in d['요건 판정']['detail'])
        check(f'[908] {q}: 세금 칸 — 부득이(신고확인서 있으면)와 연금외수령(없으면) 둘 다',
              ['credit', '부득이'] in qc['cells'] and ['credit', '연금외수령'] in qc['cells']
              and '해외이주신고확인서 있으면' in d['세금 계산']['detail'], str(qc['cells']))
        if acct == 'irp':
            check(f'[908] {q}: IRP 는 어느 쪽이든 전액 해지만', '전액 해지만' in d['요건 판정']['detail']
                  and any('해지할 IRP 계좌 평가액' in a for a in asks(d)))
    # 이웃 — 해외이주·이민은 사건으로 읽는다(신고확인서 조건만 덧붙임)
    for q in ('해외이주하면 IRP 해지 세금은?', '영주권 따서 이민 가는데 IRP 해지하면 세금은?', '이민 가는데 연금저축 해지하면 세금은?'):
        s, d = path(q)
        qc = ontology.question_cells(q)
        check(f'[908 이웃] {q}: 해외이주 사건 · 부득이 칸만 · 신고확인서 조건 문장', '사건 = 해외이주' in d['개체 인식']['detail']
              and 'attr.emigrate_proof' not in missing(d) and all(m == '부득이' for _, m in qc['cells'])
              and '해외이주신고확인서' in d['요건 판정']['detail'], str(qc['cells']))
    pq = O['plan_rules']['pension']['quotes']
    check('[908] pension 원문에 doc20 영주권 문장', any(x['quote'] == src for x in pq))


# ---------------------------------------------------------------------------
# 909 — R-03 조건과 경로 계산
# ---------------------------------------------------------------------------
def test_909():
    cond = R['R-03']['condition']
    check('[909] R-03 조건: "(최대 300만 원)은 ③" 단정 없음 · 세액공제대상금액(doc4)',
          '공제받은 금액(최대 300만 원)은 ③' not in cond and '세액공제대상금액' in cond and '900만 원을 다 채웠다면' in cond
          and '나머지는 ① 비과세 재원(doc4)' in cond, cond)
    want = ['만기ISA전환 납입액에서 세액공제대상금액을 뺀 나머지 금액은 비과세재원에 포함되어 비과세 인출 및',
            '연금저축펀드 600만원, IRP에 300만원을 납입해서 900만원을 다 채웠다면 ISA 만기금액은 연금저축']
    qs = [x['quote'] for x in R['R-03']['quotes']]
    for w, f in zip(want, ('doc4.txt', 'doc23.txt')):
        check(f'[909] R-03 원문 {f} 글자 그대로', w in qs and w in doc(f), w[:30])
    for q in ('ISA 만기 3000만원 연금저축으로 옮기면 추가공제 받나요?', 'ISA 만기 자금 3,000만 원을 IRP로 옮기면 추가로 세액공제를 받을 수 있나요?'):
        s, d = path(q)
        t = d['세금 계산']['detail']
        check(f'[909] {q}: 경로에 "다른 납입이 없다고 볼 때" 가정 · 한도를 채웠으면 추가분만',
              '올해 다른 납입이 없다고 볼 때' in t and '추가분(R-03)만' in t and '올해 다른 납입은 없다고 봄' in assumed(d), t[:200])
    s, d = path('ISA 만기 3000만원 연금저축으로 옮기면 추가공제 받나요?')
    check('[909] 연금저축 전환 계산은 그대로(600 + 300 = 900, 나머지 2,100 ①)', '= 900만 원' in d['세금 계산']['detail']
          and '2,100만 원' in d['세금 계산']['detail'])
    s, d = path('연금저축 600만 원 넣었고 ISA 만기 3000만원 IRP로 옮기면 공제는? 총급여 5000만 원')
    check('[909 이웃] 납입액을 말했으면 "다른 납입 없음" 가정을 붙이지 않음', '다른 납입이 없다고' not in d['세금 계산']['detail']
          and (d['세금 계산'].get('calc') or {}).get('total') == '198만 원', d['세금 계산']['detail'][:160])


# ---------------------------------------------------------------------------
# 910 — 퇴직금 입금 길
# ---------------------------------------------------------------------------
def test_910():
    for q in ('연금저축에 퇴직금 넣을 수 있어요?', '퇴직금을 연금저축으로 받을 수 있나요?', '연금저축으로 퇴직금 입금 가능해요?',
              '연금저축에 퇴직금 넣어도 돼요?', '연금저축계좌에 퇴직금 이체 가능한가요?', '퇴직금 연금저축으로 받으면 세금은?',
              '퇴직금 받은 거 연금저축에 넣으면 세금 줄어요?', 'IRP 말고 연금저축에 퇴직금 넣어도 되나요?', '연금저축에 퇴직금도 입금되나요?'):
        s, d = path(q)
        qc = ontology.question_cells(q)
        check(f'[910] {q}: 퇴직급여 받는 길 — 연금저축 직접 수령 조건(doc51) · 인출 칸·평가액·연차 되묻기 없음',
              s[0]['intent'] == 'retire_route' and qc['cells'] == [] and '연금저축계좌로 퇴직금을 바로 받을 수 있는 것은' in d['요건 판정']['detail']
              and 'account.savings' in d['요건 판정']['nodes'] and 'fund.retire' in d['요건 판정']['nodes']
              and not {'attr.balance', 'attr.year_n', 'attr.withdraw', 'attr.actual_n'} & set(missing(d)),
              f"{s[0]['intent']} {missing(d)}")
    s, d = path('58세 퇴직하는데 퇴직금 연금저축에 넣어도 되나요?')
    check('[910] 58세: 55세 이상 퇴직 — DB·퇴직금제도 퇴직금은 연금저축도 선택(doc51 표)', s[0]['intent'] == 'retire_route'
          and '55세 이상 퇴직' in d['요건 판정']['detail'] and 'attr.age' not in missing(d))
    s, d = path('IRP에 퇴직금 넣을 수 있어요?')
    check('[910 같은 원인] IRP에 퇴직금 넣을 수 있어요? — 받는 길(IRP 의무이전·60일)', s[0]['intent'] == 'retire_route'
          and '60일 안에 IRP' in d['요건 판정']['detail'] and ontology.question_cells('IRP에 퇴직금 넣을 수 있어요?')['cells'] == [])
    for q in ('DC 퇴직금을 연금저축으로 받을 수 있어요?', 'DB형에서 퇴직하면 퇴직금이 어디로 가요?'):
        s, d = path(q)
        check(f'[910 이웃] {q}: 받는 길 그대로', s[0]['intent'] == 'retire_route')
    # 같은 원인 — 제도 이름과 함께 와도 연금계좌 이름이 있으면 예전에는 인출 갈래였다
    for q in ('DC 퇴직금 연금저축에 넣을 수 있나요?', 'DC 퇴직금 IRP에 넣어야 하나요?', 'DB형 퇴직금 연금저축에 넣을 수 있나요? 57세예요'):
        s, d = path(q)
        check(f'[910 같은 원인] {q}: 받는 길 · 세금 칸 없음', s[0]['intent'] == 'retire_route' and ontology.question_cells(q)['cells'] == [],
              s[0]['intent'])
    s, d = path('DB형 퇴직금 연금저축에 넣을 수 있나요? 57세예요')
    check('[910] DB 57세: 55세 이상 퇴직 — DB 퇴직금은 연금저축 직접 수령 가능 · 나이를 다시 묻지 않음',
          '55세 이상 퇴직' in d['요건 판정']['detail'] and 'DB·퇴직금제도 퇴직금' in d['요건 판정']['detail'] and not missing(d))
    # 이웃 — 이미 넣은 퇴직금을 빼거나 연금으로 받는 질문은 인출·수령 갈래
    for q in ('연금저축에 들어 있는 퇴직금 빼면 세금은?', '연금저축에 퇴직금 1억 넣어 뒀는데 2000만 원 빼면 세금은?',
              '연금저축에 퇴직금 넣고 나중에 빼면 세금은?', '연금저축에 퇴직금 넣어서 연금으로 받으면 세금은?',
              'IRP에 퇴직금 들어 있는데 연 2000만 원 연금으로 받으면 세금은?',
              '퇴직금 받은 IRP를 연금저축으로 옮길 수 있나요? 56세입니다'):        # 계좌끼리 옮기기(이체)는 받는 길이 아니다
        s, d = path(q)
        check(f'[910 이웃] {q}: 받는 길로 보내지 않음', s[0]['intent'] != 'retire_route', s[0]['intent'])
    eq = next(e for e in O['edges'] if e['from'] == 'account.savings' and e['to'] == 'fund.retire')
    check('[910] account.savings → fund.retire 엣지 원문(doc51) 그대로', eq['quote'] in doc('doc51.txt'))
    rq = O['plan_rules']['retire_route']['quotes']
    for x in rq:
        check(f"[910] retire_route 원문 {x['doc']} 글자 그대로", x['quote'] in doc(x['doc'] + '.txt'), x['quote'][:40])


def main():
    for t in (test_903, test_904, test_905, test_906, test_908, test_909, test_910):
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
