# -*- coding: utf-8 -*-
"""
10-15 9차 검토 — parser9 묶음(taxcalc) 회귀 시험. API 호출 없음.

    PYTHONIOENCODING=utf-8 python src/test_fix1014_parser9.py

800  8차 _dest_parts 의 '닫힌 계좌'가 넣겠다는 마디('IRP에 넣으려고 하는데 연금저축도 있어서 800만 원')의 계좌까지 넣는 곳에서 빼
     연금저축 99만 원을 냈다(8차 전 사본은 IRP 132만 원). 닫힘은 이미 넣은·남이 넣는 마디(지난·진행 동사, 그 마디의 금액, 회사·가족
     주어)일 때만, 넣겠다는 마디면 그 계좌, 둘 다 아니면 되묻기.
900  DB·기업형 IRP(와 DC·종류 모를 퇴직연금) 해지·중도인출을 연금계좌 '55세 전·해지 인출 세금'으로 495만·165만·330만 원 계산했다 —
     DB 는 중도인출 불가(doc49), 회사가 허용하면 DC 전환 뒤 DC 중도인출(doc13), 55세 전 퇴직이면 IRP 의무이전(doc51) 규칙만.
901  55세 이후 해지('60세인데 연금저축 2000만 원 해지')를 '55세 전·해지' 블록으로 330만 원 — 개시 여부·연금수령한도 규칙과 되묻기
     (개시 신청 전이면 연금외수령 doc5, 연금 지급 중 해지는 한도까지 연금소득세 doc1).
902  규칙표 요약(R-11~R-14)이 ontology.json condition 과 같은 말 — R-12 DB 불가·DC 법정 사유·IRP 사유 없으면 전액 해지, R-11 6개월
     서류·요양·재난 한도, R-13 DC 전월세 재직 중 1회, R-14 적격 TDF.
907  (구)개인연금저축 납입을 연금저축계좌 납입(contrib savings)으로 읽었다 — 따로 담는다(old_contrib).
908  영주권·장기체류·해외 이사를 해외이주(부득이)로 단정 — 해외이주신고확인서부터 묻고(doc20) 블록 첫 줄에 단서를 싣는다.
재현 입력마다 기대 결과를, 지금 맞게 도는 이웃 질문은 그대로인지 함께 본다.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import taxcalc  # noqa: E402
import orchestrator  # noqa: E402  (되묻기 판단 확인용 — 모델 호출 없음)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
passed, total = 0, 0


def ok(cond, msg):
    global passed, total
    total += 1
    if cond:
        passed += 1
    else:
        print('실패:', msg)


def calc(q):
    return taxcalc.compute(q)


def blocks(q):
    c = calc(q)
    return c['blocks'] if c else []


def topics(q):
    return [b['topic'] for b in blocks(q)]


def key(q):
    for b in blocks(q):
        return b.get('key_figure')
    return None


def figs(q):
    return [f for b in blocks(q) for f in b.get('figures') or []]


def body(q):
    return '\n'.join(b['text'].split('\n[원문]')[0] for b in blocks(q))


def needs(q):
    return [n for b in blocks(q) for n in b.get('needs') or []]


def decide(q):
    return orchestrator.decide(q, calc=calc(q))[0]


def parse(q):
    return taxcalc.parse(q)


def accts(q):
    return [(c['acct'], c['v']) for c in parse(q)['contrib']]


T = ' 세액공제? 총급여 5천'
EARLY_T = '55세 전·해지 인출 세금'


def fix(s):
    return s.replace('연금저축는', '연금저축은').replace('연금저축가', '연금저축이').replace('연금저축로', '연금저축으로')


# ── 결함 800: 넣겠다는 마디의 계좌는 넣는 곳 — 의도 동사 × 있다 꼴 × 방향(8차 전 사본과 같은 값)
INTENT = ['넣으려고 하는데', '넣으려는데', '넣고 싶은데', '납입하려고 하는데', '넣을까 하는데', '몰아서 넣으려는데', '넣을 건데',
          '넣을 생각인데', '넣으려 하는데', '넣어야 하는데', '넣기로 했는데', '넣고자 하는데', '넣을 예정인데']
HAVE = ['{B}도 있어서', '{B}도 있고', '{B}도 있는데', '{B}도 가입돼 있어서', '{B} 계좌도 있는데', '{B}는 있는데', '{B}도 갖고 있는데']
WANT = {'IRP': ('irp', '132만 원'), '연금저축': ('savings', '99만 원')}
n800 = 0
for A, B in (('IRP', '연금저축'), ('연금저축', 'IRP')):
    acct, want = WANT[A]
    for v in INTENT:
        for h in HAVE:
            q = fix(f'{A}에 {v} {h.format(B=B)} 800만 원 넣으면') + T
            n800 += 1
            ok(accts(q) == [(acct, 800)] and key(q) == want and not parse(q)['acct_unsure'],
               f'800 넣겠다는 계좌: {q} → {accts(q)} {key(q)}')
        # 다른 계좌가 없다고만 하면 넣겠다는 계좌 그대로(8차 전과 같음)
        q = fix(f'{A}에 {v} {B}는 없는데 800만 원 넣으면') + T
        ok(accts(q) == [(acct, 800)] and key(q) == want, f'800 넣겠다는 계좌 + 없는 계좌: {q} → {accts(q)} {key(q)}')
# 재현 입력 그대로
q = 'IRP에 넣으려고 하는데 연금저축도 있어서 800만 원 넣으면 세액공제? 총급여 5천'
ok(key(q) == '132만 원' and '납입: IRP 800만 원' in body(q), f'800 재현 1: {body(q)}')
q = '연금저축에 넣고 싶은데 IRP도 있고 800만 원 넣으면' + T
ok(key(q) == '99만 원' and '납입: 연금저축 800만 원' in body(q), f'800 재현 2: {body(q)}')
# 넣겠다는 계좌와 새로 만드는 계좌가 갈리면 모름 — 숫자 없이 되묻기
for q in ('IRP에 넣으려고 하는데 연금저축이 없어서 새로 만들어 800만 원 넣으면' + T,
          '연금저축에 넣고 싶은데 IRP가 없어서 새로 만들어 800만 원 넣으면' + T):
    ok(not accts(q) and parse(q)['acct_unsure'] == [800] and not figs(q) and decide(q) == '되묻기',
       f'800 넣겠다는 계좌 vs 새 계좌: {q} → {accts(q)} {figs(q)}')
# 하려다 만 말('넣으려고 했는데·넣고 싶었는데')은 바꾼 말일 수 있다 — 있다 꼴이면 되묻기, 새로 만든다면 그 계좌
for q in ('IRP에 넣으려고 했는데 연금저축도 있어서 800만 원 넣으면' + T, '연금저축에 넣고 싶었는데 IRP도 있어서 800만 원 넣으면' + T):
    ok(not accts(q) and parse(q)['acct_unsure'] == [800] and not figs(q), f'800 하려다 만 말: {q} → {accts(q)} {figs(q)}')
q = 'IRP에 넣으려고 했는데 연금저축이 없어서 새로 만들어 800만 원 넣으면' + T
ok(accts(q) == [('savings', 800)] and key(q) == '99만 원', f'800 하려다 만 말 + 새 계좌: {accts(q)}')
# 넣겠다는 말도 이미 한 일도 아닌 마디('IRP에 넣고 연금저축도 있는데')는 판마다 갈린 꼴이라 되묻기
for q in ('IRP에 넣고 연금저축도 있는데 800만 원 넣으면' + T, '연금저축에 넣는데 IRP도 있어서 800만 원 넣으면' + T):
    ok(not accts(q) and parse(q)['acct_unsure'] == [800] and not figs(q) and decide(q) == '되묻기',
       f'800 모르는 마디: {q} → {accts(q)} {figs(q)}')
# 이웃: 이미 넣은·진행 중·남이 넣는 마디는 8차대로 닫힘(뒤 계좌)
for q, want in (('연금저축에 600만 원 채웠고 IRP는 있는데 300만 원 더 넣으면' + T, [('savings', 600), ('irp', 300)]),
                ('연금저축에 넣었는데 IRP도 있어서 300만 원 더 넣으면' + T, [('irp', 300)]),
                ('연금저축에 매달 50만 원 넣고 있고 IRP도 있는데 300만 원 넣으면' + T, [('savings', 600), ('irp', 300)]),
                ('IRP에 넣고 있는데 연금저축도 있어서 300만 원 더 넣으면' + T, [('savings', 300)]),
                ('DC에 회사가 넣어주고 IRP는 있는데 300만 원 넣으면' + T, [('irp', 300)]),
                ('회사가 DC에 넣어주려는데 IRP도 있어서 300만 원 넣으면' + T, [('irp', 300)]),
                ('IRP에 800만 원 넣으려는데 연금저축도 있어서 300만 원 넣으면' + T, [('irp', 800), ('savings', 300)])):
    ok(accts(q) == want and not parse(q)['acct_unsure'], f'800 이웃 닫힌 마디: {q} → {accts(q)} {parse(q)["acct_unsure"]}')
# 판정 단위
ok(taxcalc._clause_kind('', '에 넣으려고 하는데 ') == 'intent' and taxcalc._clause_kind('', '에 넣을 건데 ') == 'intent'
   and taxcalc._clause_kind('', '에 600만 원 채웠고 ') == 'done' and taxcalc._clause_kind('회사가 ', '에 넣어주고 ') == 'done'
   and taxcalc._clause_kind('', '에 넣고 있고 ') == 'done' and taxcalc._clause_kind('', '에 넣었는데 ') == 'done'
   and taxcalc._clause_kind('', '에 넣으려고 했는데 ') is None and taxcalc._clause_kind('', '에 넣고 ') is None
   and taxcalc._clause_kind('', '에 2030 TDF로 넣으려는데 ') == 'intent', '_clause_kind 판정')
ok(taxcalc._dest_parts('IRP에 넣으려고 하는데 연금저축도 있어서 ') == (['IRP'], False, [])
   and taxcalc._dest_parts('연금저축에 600만 원 채웠고 IRP는 있는데 ') == (['IRP'], False, ['연금저축'])
   and taxcalc._dest_parts('IRP에 넣고 연금저축도 있는데 ') == ([], True, [])
   and taxcalc._dest_parts('IRP에 넣으려는데 연금저축이 없어서 새로 만들어 ') == ([], True, []), '_dest_parts 판정')

# ── 결함 900: DB·기업형 IRP 해지·중도인출 — 연금계좌 해지 세금으로 계산하지 않는다(숫자 없음, 제도 규칙)
DB_LINE = 'DB(확정급여형)는 중도인출이 허용되지 않는다'
for q in ('DB형 퇴직연금 3000만 원 해지하면 세금 얼마예요?', 'DB형 퇴직연금 주택 구입으로 1000만 원 중도인출하면 세금은?',
          '확정급여형 퇴직연금 2000만 원 해지하면 세금은?', '45세인데 DB형 퇴직연금 2000만 원 해지하면 세금은?',
          '60세인데 DB형 퇴직연금 2000만 원 해지하면 세금은?', '퇴직연금 DB 깨면 세금 얼마나 떼요?',
          'DB형 퇴직연금 요양 때문에 중도인출하면 세금은?', 'DB형 퇴직연금 2000만 원 55세 전에 빼면 세금은?',
          'DB형인데 중도인출 돼요?', 'DB에서 DC로 바꾸면 중도인출 되나요?', 'DB 3000만 원 해지하면 세금 얼마예요?'):
    t = body(q)
    ok(topics(q) == ['퇴직연금 중도인출·해지(규칙)'] and not figs(q) and DB_LINE in t and 'DB에서 DC로 퇴직급여제도를 바꿀 수 있다' in t
       and 'IRP로 옮겨야 하고(의무 이전)' in t and '16.5% 대상' not in t and EARLY_T not in t
       and blocks(q)[0]['rids'] == ['R-12'], f'900 DB: {q} → {topics(q)} {figs(q)}\n{t}')
q = 'DB형 퇴직연금 요양 때문에 중도인출하면 세금은?'
ok('요양·파산 같은 사유가 있어도 DB에서는 중도인출이 허용되지 않는다' in body(q), f'900 DB 요양: {body(q)}')
CORP_LINE = '기업형 IRP를 해지·인출할 때의 세금 규칙은 받은 문서에 따로 없어 금액을 계산하지 않음'
for q in ('기업형 IRP 2000만 원 해지하면 세금은?', '기업형IRP 3000만 원 해지하면 세금 얼마예요?',
          '기업형 IRP 주택 구입으로 1000만 원 중도인출하면 세금은?', '60세인데 기업형 IRP 2000만 원 해지하면 세금은?',
          '기업형 IRP 요양 때문에 중도인출하면 세금은?', '기업형 IRP 중도인출 돼요?'):
    t = body(q)
    ok(topics(q) == ['퇴직연금 중도인출·해지(규칙)'] and not figs(q) and CORP_LINE in t and '회사 명판·등록인감 날인' in t
       and EARLY_T not in t, f'900 기업형 IRP: {q} → {topics(q)} {figs(q)}\n{t}')
# DC·종류 모를 퇴직연금 — 연금계좌 해지 블록 대신 퇴직연금 중도인출 규칙(숫자 없음)
for q, dc in (('DC형 퇴직연금 3000만 원 해지하면 세금 얼마예요?', True), ('45세인데 DC형 퇴직연금 2000만 원 해지하면 세금은?', True),
              ('DC형 퇴직연금 주택 구입으로 1000만 원 중도인출하면 세금은?', True), ('DC형 퇴직연금 요양 때문에 중도인출하면 세금은?', True),
              ('퇴직연금 3000만 원 해지하면 세금 얼마예요?', False), ('퇴직연금 주택 구입으로 1000만 원 중도인출하면 세금은?', False),
              ('60세인데 퇴직연금 2000만 원 해지하면 세금은?', False), ('퇴직연금 2000만 원 55세 전에 빼면 세금은?', False)):
    t = body(q)
    ok(all(x.startswith('퇴직연금 중도인출') for x in topics(q)) and topics(q) and not figs(q) and EARLY_T not in ' '.join(topics(q))
       and ('세금(DC): 퇴직금 몫은 퇴직소득세' in t if dc else '퇴직연금 종류(DB·DC·IRP 중 어느 것인지)' in needs(q)),
       f'900 DC·퇴직연금: {q} → {topics(q)} {figs(q)} {needs(q)}')
q = 'DC형 퇴직연금 3000만 원 해지하면 세금 얼마예요?'
ok('재직 중 DC를 해지해 찾는 방법은 받은 문서에 없다' in body(q) and '중도인출하려는 금액' not in needs(q), f'900 DC 해지: {body(q)}')
# 이웃 그대로 — 연금계좌 이름이 있으면 그 계좌 규칙, 담보 질문, 금액 없는 DC 질문, 세액공제
for q, t_want, k_want in (('DB형인데 IRP 2000만 원 해지하면 세금은?', EARLY_T, '330만 원'),
                          ('IRP 3000만 원 해지하면 세금 얼마예요?', EARLY_T, '495만 원'),
                          ('연금저축 2000만 원 해지하면 세금은?', EARLY_T, '330만 원'),
                          ('DB에서 퇴직금 받아 IRP에 넣었는데 IRP 2000만 원 해지하면 세금은?', '55세 전·해지 인출 세금(규칙)', None),
                          ('DB형 퇴직연금 담보대출 되나요?', '퇴직연금 중도인출·담보(규칙)', None),
                          ('기업형 IRP에 300만 원 넣으면' + T, '세액공제', '49만 5천 원')):
    ok(topics(q)[:1] == [t_want] and key(q) == k_want, f'900 이웃: {q} → {topics(q)} {key(q)}')
q = 'DC형 전세보증금으로 2000만 원 중도인출하면 세금 얼마?'
ok(topics(q) == ['퇴직연금 중도인출·담보(규칙)'] and '중도인출하려는 금액' not in needs(q) and '세금(DC)' in body(q),
   f'900 이웃 DC 전세(금액을 말했으면 다시 묻지 않음): {needs(q)}')
ok(calc('DC와 DB, 퇴직금이 정해지는 방식이랑 운용 주체가 어떻게 다른가요?') is None
   and not parse('DC와 DB, 퇴직금이 정해지는 방식이랑 운용 주체가 어떻게 다른가요?')['early'],
   "900 이웃 '정해지는'은 해지가 아님")
for q in ('DC 깨서 300만 원 넣으면' + T, 'DC 해지해서 300만 원 넣으면' + T):
    ok(not figs(q) and not any(x.startswith('퇴직연금 중도인출·해지') for x in topics(q)), f'900 이웃 DC 깨서 넣기: {q} → {topics(q)}')
ok(taxcalc._plan_kind('DB형 퇴직연금 해지') == 'db' and taxcalc._plan_kind('기업형 IRP 해지') == 'irp_corp'
   and taxcalc._plan_kind('DC형 퇴직연금') == 'dc' and taxcalc._plan_kind('퇴직연금 해지') == 'retire'
   and taxcalc._plan_kind('DB형인데 IRP 해지') is None and taxcalc._plan_kind('기업형 IRP랑 개인형 IRP') is None
   and taxcalc._plan_kind('DB인데 연금저축 해지') is None, '_plan_kind 판정')

# ── 결함 901: 55세 이후 해지 — '55세 전·해지' 블록이 아니라 개시 여부·한도 규칙과 되묻기(숫자 없음)
LATE_T = '55세 이후 해지·인출 세금(규칙)'
for q in ('60세인데 연금저축 2000만 원 해지하면 세금 얼마예요?', '62세인데 연금저축 해지하면 세금은?', '60세인데 IRP 3000만원 해지하면 세금은?',
          '55세인데 연금저축 2000만 원 해지하면 세금 얼마예요?', '60대인데 IRP 깨면 세금 얼마나 떼요?', '연금저축 60세인데 해지하면 세금은?',
          '67세인데 연금저축 해지하면 1,500만 원 넘어도 분리과세예요?', '70세에 IRP 중도인출 1,500만 원 넘게 하면 종합과세돼요?',
          '만 61세인데 연금저축 2000만 원 중도인출하면 세금은?', '65세에 연금저축 해지하면 16.5% 다 떼이나요?'):
    t = body(q)
    ok(topics(q) == [LATE_T] and not figs(q) and any('연금 개시 여부' in n for n in needs(q))
       and '개시 신청 전이면 55세가 넘어도 연금외수령' in t and '연금수령한도까지는 연금소득세' in t
       and '55세 전 인출·해지는 연금수령이 아니라' not in t and decide(q) == '되묻기', f'901 55세 이후 해지: {q} → {topics(q)} {figs(q)}\n{t}')
q = '60세인데 아직 연금 개시 안 한 연금저축 2000만 원 해지하면 세금은?'
t = body(q)
ok(topics(q) == [LATE_T] and '개시 신청 전이라 55세가 넘어도 연금외수령' in t and '연금수령한도까지는' not in t
   and not any('연금 개시 여부' in n for n in needs(q)), f'901 개시 전: {t}')
q = '60세인데 연금 받고 있는 연금저축 해지하면 세금은?'
t = body(q)
ok(topics(q) == [LATE_T] and '연금수령한도까지는 연금소득세' in t and '개시 신청 전이면' not in t
   and any('평가액' in n for n in needs(q)), f'901 받는 중(평가액 모름): {t}')
q = '60세인데 연금 받고 있는 연금저축 평가액 1억, 수령 3년차인데 해지하면 세금은?'
ok(topics(q) == ['연금수령한도'] and key(q) == '1,485만 원', f'901 받는 중(한도 계산 doc1 해지 규칙): {topics(q)} {key(q)}')
q = '60세인데 연금저축 해지하지 말고 연 1,200만 원씩 받으면 세금은?'
ok(topics(q) == ['연금 수령 세금'] and key(q) == '66만 원', f"901 '해지하지 말고' 수령: {topics(q)} {key(q)}")
q = '60세인데 요양 때문에 연금저축 2000만 원 해지하면 세금은?'
t = body(q)
ok(topics(q) == ['부득이한 사유 인출(규칙)'] and '55세 이후 해지는 연금 개시 여부로 갈린다' in t
   and '55세 전 인출·해지는 연금외수령' not in t, f'901 55세 이후 부득이: {t}')
# 이웃 그대로 — 55세 전·나이 모름·'55세 안 넘었는데'
for q, k_want in (('55세 안 넘었는데 연금저축 2000만원 해지할라고요 전부 16.5% 떼서 330만원 맞나요?', '330만 원'),
                  ('50세인데 연금저축 2000만 원 해지하면 세금은?', '330만 원'), ('연금저축 2000만 원 해지하면 세금은?', '330만 원'),
                  ('아직 55살이 안 됐는데 IRP 2000만 원 해지하면 세금은?', '330만 원'),
                  ('60세인데 55세 전에 연금저축 2000만 원 해지했으면 세금은?', '330만 원')):
    ok(topics(q) == [EARLY_T] and key(q) == k_want, f'901 이웃 55세 전: {q} → {topics(q)} {key(q)}')
for q in ('54세인데 IRP 깨면 세금 얼마나 떼요?', '연금저축 해지하면 세금은?'):
    ok(topics(q) == ['55세 전·해지 인출 세금(규칙)'], f'901 이웃 규칙: {q} → {topics(q)}')

# ── 결함 902: 규칙표 요약(R-11~R-14)이 ontology.json condition 과 같은 말, 근거 원문은 문서에 그대로
BOOK = {rid: (keys, summary) for rid, _, keys, summary, _ in taxcalc.RULEBOOK}
s11, s12, s13, s14 = (BOOK[r][1] for r in ('R-11', 'R-12', 'R-13', 'R-14'))
ok('6개월' in s11 and '200만 원 + 의료비 + 간병비' in s11 and '기타소득세' in s11 and '해외이주신고확인서' in s11, f'902 R-11: {s11}')
ok('DB는 중도인출 불가' in s12 and 'DC는 법정 사유가 있을 때만' in s12 and '전액 해지만' in s12 and 'DC로 바꾼 뒤' in s12
   and '연금저축은 사유 없이' in s12, f'902 R-12: {s12}')
ok('1회' in s13 and '6개월 이상 요양' in s13 and '최근 5년 이내 파산' in s13 and '담보대출 상환' in s13, f'902 R-13: {s13}')
ok('70% 이내' in s14 and 'TDF' in s14 and 'DB는 70%까지' in s14, f'902 R-14: {s14}')


def _nows(s):
    return re.sub(r'\s+', '', s)


for rid in ('R-04', 'R-10', 'R-11', 'R-12', 'R-13', 'R-14'):
    for k in BOOK[rid][0]:
        f, text = taxcalc.RULES[k]
        with open(os.path.join(ROOT, 'extracted', 'docs', f), encoding='utf-8') as fh:
            ok(_nows(text) in _nows(fh.read()), f'902 원문 {rid} {k} — {f}')
onto = {r['id']: r for r in json.load(open(os.path.join(ROOT, 'data', 'ontology.json'), encoding='utf-8'))['rules']}
c12, c11 = onto['R-12']['condition'], onto['R-11']['condition']
ok('DB → 불가' in c12 and 'DC 로 전환' in c12 and '전액 해지만' in c12, f'902 ontology R-12 condition 이 요약과 같은 말: {c12}')
ok('6개월' in c11 and '간병비' in c11, f'902 ontology R-11 condition 이 요약과 같은 말: {c11}')

# ── 결함 907: (구)개인연금저축 납입은 연금저축계좌 납입이 아니다
for q, v in (('(구)개인연금저축 300만 원 납입하면 세액공제 얼마?', 300), ('구 개인연금저축 300만 원 넣으면 소득공제 얼마예요?', 300),
             ('1998년에 가입한 개인연금 300만 원 납입하면 공제?', 300),
             ('1999년에 가입한 개인연금저축계좌에 올해 600만원 넣으면 세액공제 얼마예요? 연봉은 5천만원이에요', 600)):
    p = parse(q)
    ok(p['old'] and p['contrib'] == [] and p['old_contrib'] == [v] and calc(q) is None, f'907 (구)개인연금: {q} → {accts(q)} {p["old_contrib"]}')
q = '(구)개인연금저축에 300만 원 넣고 연금저축에 300만 원 넣으면' + T
ok(accts(q) == [('savings', 300)] and parse(q)['old_contrib'] == [300], f'907 (구) + 연금저축: {accts(q)}')
# 이웃 그대로 — (구)·옛 해 없이 '개인연금저축'만은 지금처럼 연금저축, 연금저축 납입
for q, want in (('개인연금저축 300만 원 납입하면 세액공제 얼마?', [('savings', 300)]), ('연금저축 300만 원 넣으면' + T, [('savings', 300)])):
    ok(accts(q) == want and not parse(q)['old_contrib'], f'907 이웃: {q} → {accts(q)}')

# ── 결함 908: 영주권·장기체류·해외 이사 — 해외이주로 단정하지 않고 해외이주신고확인서부터 묻는다
PROOF = '단순 장기체류나 영주권 취득만으로는 세법상 부득이한 사유(해외이주)로 인정되지 않는다'
for q in ('영주권 받아서 IRP 해지하면 세금은?', '미국 영주권 취득했는데 연금저축 해지하면 세금 낮아지나요?',
          '해외로 이사 가는데 연금저축 해지하면 세금은?', '캐나다 영주권 나왔는데 50세에 IRP 2000만 원 해지하면 세금은?',
          '외국으로 이사 가요. IRP 해지하면 세금은?', '60세인데 영주권 받아서 연금저축 해지하면 세금은?'):
    p = parse(q)
    b = blocks(q)
    t = body(q)
    ok(p['emigrate_proof'] and b and b[0]['topic'] == '부득이한 사유 인출(규칙)' and t.split('\n')[1].startswith('- ' + PROOF)
       and any('해외이주신고확인서' in n for n in needs(q)) and not figs(q) and decide(q) == '되묻기'
       and '해외이주신고확인서로 해외이주가 인정되면' in t, f'908 영주권·이사: {q} → {needs(q)}\n{t}')
for q in ('해외 장기체류 중인데 IRP 해지하면 세금은?', '해외 장기 체류 예정인데 연금저축 2000만 원 해지하면 세금은?'):
    ok(parse(q)['emigrate_proof'] and PROOF in body(q) and not parse(q)['unavoidable'], f'908 장기체류: {q}\n{body(q)}')
# 이웃 그대로 — 이민·해외이주라는 말이 있으면 해외이주 규칙(확인서는 필요 조건으로만), test_rules 의 인식 표
for q in ('이민 가는데 연금저축 해지하면 세금은?', '영주권 따고 이민 가는데 연금저축 깨면 세금은?', '해외이주 신고했어요. 연금저축 해지하면 세금은?',
          '외국으로 이주하게 돼서 연금저축을 정리하려고 해요. 해지하면 세금 많이 떼나요?', '50세인데 해외이주 때문에 IRP 2,000만원 해지하면 세금은?'):
    p = parse(q)
    ok(p['emigrate'] and not p['emigrate_proof'] and not needs(q) and topics(q) == ['부득이한 사유 인출(규칙)']
       and '해외이주(이민): 근퇴법상' in body(q) and PROOF not in body(q), f'908 이웃 해외이주: {q} → {needs(q)}')
for q, want in (('캐나다로 이민 갑니다', True), ('해외로 이주할 예정이에요', True), ('외국으로 이사 가요', True),
                ('해외이주 신고했어요', True), ('지방으로 이사 가요', False), ('해외 주식형 펀드', False)):
    p = parse(q)
    ok(p['emigrate'] == want and (not want or p['unavoidable']), f'908 이웃 인식(test_rules EMIGRATE): {q}')

print(f'{passed}/{total} 통과 (800 의도 꼴 {n800}개 포함)')
sys.exit(0 if passed == total else 1)
