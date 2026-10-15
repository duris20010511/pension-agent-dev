# -*- coding: utf-8 -*-
"""
10-14 6차 결함 검토 — guard6 묶음(orchestrator._YEAR_CTX · _BARE_AFTER_ACCT · unread_amounts) 회귀 시험. API 호출 없음.

    python src/test_fix1014_guard6.py

5차 수정 뒤 회귀(506~508): 4자리 연도(19xx·20xx, 1900·2000만 뺌) 뒤에 기준·원천(징수)·연말정산이 오면 연도로 보아, 실제 만 원
금액('IRP 1980 원천징수되면', '연금저축 2050 기준으로')이 그물을 빠져 생성으로 갔다. 어림수만 빼는 기준도 일관되지 않았다.
고친 뒤: 숫자 뒤 말이 연도에만 붙는 말(원천징수영수증·지급명세서·납입증명서 같은 서류, 귀속, 년도·연도)일 때만 연도로 본다.
4자리는 서류에 찍힐 수 있는 해(1990년~내년)만, 서류 앞 두 자리('IRP 24 원천징수영수증')도 연도다(509).
answer.py 가 /chat 에서 부르는 조건과 같게 taxcalc.compute 결과를 calc 로 넘긴다.
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import orchestrator
import taxcalc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
passed, total = 0, 0
ASK = '단위(만 원인지) 확인 필요'
NEXT_YEAR = datetime.date.today().year + 1


def ok(cond, msg):
    global passed, total
    total += 1
    if cond:
        passed += 1
    else:
        print('실패:', msg)


def unread(q):
    return orchestrator.unread_amounts(q, taxcalc.compute(q))


def ask(q):
    u = unread(q)
    return u, (orchestrator.unread_amount_answer(u, q) if u else '')


def bare(q):
    return orchestrator._BARE_AFTER_ACCT.findall(taxcalc.normalize(q))


def no_year(q):
    """연도만 지운 같은 질문 — 연도는 금액이 아니므로 그물 결과가 같아야 한다."""
    return re.sub(r'(IRP|저축)\s*(?:(?:19|20)\d\d|\d\d)\s*', r'\1 ', q)


def retire_branch(a):
    return '연차' in a and '30%' in a and '총급여' not in a and '무엇을 계산할지' not in a


def asked(q, n):
    """숫자 n 을 단위 확인으로 되묻고, 계산값(원 금액)을 지어내지 않는다."""
    u, a = ask(q)
    return (n, ASK) in u and '읽지 못했어요' in a and '숫자를 짐작해서 답하지 않으려고' in a


# ── 결함 506 — 'IRP 1980 원천징수되면'의 1980은 금액일 수 있다(고치기 전 [] → 생성으로 감)
q = 'IRP 1980 원천징수되면 세금 얼마예요?'
u, a = ask(q)
ok(u == [('1980', ASK)], f'506 읽은 숫자: {u}')
ok(asked(q, '1980'), f'506 되묻기: {a}')

# ── 결함 507 — 'IRP 1950 기준으로 연금 받으면'(고치기 전 []) — 수령 갈래로 되묻는다
q = 'IRP 1950 기준으로 연금 받으면 세금 얼마예요?'
u, a = ask(q)
ok(u == [('1950', ASK)], f'507 읽은 숫자: {u}')
ok(asked(q, '1950') and '만 나이' in a and '1년에 받을 금액' in a, f'507 수령 갈래 되묻기: {a}')

# ── 결함 508 — 기준·귀속(아직 없는 해) 꼴(고치기 전 [])
for q, n in (('연금저축 2050 기준으로 세액공제 얼마예요?', '2050'), ('연금저축 2020 기준 세액공제 얼마예요?', '2020'),
             ('IRP 2030 귀속 퇴직소득세 얼마예요?', '2030')):
    ok(asked(q, n), f'508 되묻기: {q} → {unread(q)}')
u, a = ask('연금저축 2050 기준으로 세액공제 얼마예요?')
ok('총급여' in a and '납입액' in a, f'508 세액공제 갈래 되묻기: {a}')
u, a = ask('IRP 2030 귀속 퇴직소득세 얼마예요?')
ok(retire_branch(a), f'508 퇴직 갈래 되묻기: {a}')

# 같은 원인 — 어림수인지와 상관없이 숫자 꼴이 같으면 결과가 같다(1900·1950·1980·2000·2020·2025·2050)
for n in ('1900', '1950', '1980', '2000', '2020', '2025', '2050'):
    for tpl in ('IRP {} 원천징수되면 세금 얼마예요?', '연금저축 {} 기준으로 세액공제 얼마예요?',
                '연금저축 {}기준 세액공제 얼마예요?', 'IRP {} 연말정산하면 얼마 돌려받아요?',
                'IRP {}원천징수되면 세금 얼마예요?', 'IRP {} 원천징수 세금 얼마예요?',
                '연금저축 {} 연말 정산 세액공제 얼마예요?', '연금저축 {} 소득 기준 세액공제 얼마예요?'):
        q = tpl.format(n)
        ok(asked(q, n), f'금액일 수 있는 숫자를 놓침: {q} → {unread(q)}')
        ok(bare(q) == [n], f'단위 없는 숫자로 읽지 않음: {q} → {bare(q)}')

# 서류 이름 앞이라도 동사가 끼면('넣은 영수증') 금액일 수 있다 — 연도로 보지 않는다
q = '연금저축 2000 넣은 영수증 보면 세액공제 얼마예요?'
ok(('2000', ASK) in unread(q), f'동사가 낀 서류 앞 숫자: {unread(q)}')
# 서류에 찍힐 수 없는 해(1990년 전·내년 뒤)는 귀속이 붙어도 되묻는다
for q, n in (('연금저축 2050 귀속 세액공제 얼마예요?', '2050'), ('연금저축 1989 귀속 세액공제 얼마예요?', '1989'),
             (f'연금저축 {NEXT_YEAR + 1} 귀속 세액공제 얼마예요?', str(NEXT_YEAR + 1))):
    ok(asked(q, n), f'있을 수 없는 해: {q} → {unread(q)}')

# ── 결함 509 — 연도에만 붙는 말(서류·귀속·년도)이면 연도다. 같은 원인의 다른 표현 모두
q = 'IRP 2025 원천징수영수증에 퇴직소득세 300만원인데 연금으로 받으면 세금 얼마예요?'
u, a = ask(q)
ok(u == [('300만 원', '퇴직소득세')], f'509 읽은 숫자: {u}')
ok('조건이 더 필요해요' in a and '읽지 못했어요' not in a and '2025' not in a, f'509 연도를 못 읽은 금액으로 보임:\n{a}')
ok(retire_branch(a), f'509 퇴직 갈래: {a}')
YEAR_QS = ('IRP 2025 원천징수영수증에 퇴직소득세 300만원인데 연금으로 받으면 세금 얼마예요?',
           '연금저축 2023 원천징수영수증 보니까 세액공제 얼마 받아요?',
           'IRP 2024 원천 징수 영수증에 퇴직소득세 500만원인데 연금 수령하면 세금 얼마예요?',
           'IRP 2022원천징수영수증 퇴직소득세 400만원이면 얼마나 감면돼요?',
           '연금저축 2025 원천징수 내역 보면 환급 얼마예요?',
           'IRP 1998 원천징수영수증 퇴직소득세 얼마예요?',
           'IRP 2010 원천징수영수증 퇴직소득세 200만원이면 얼마나 감면돼요?',
           # 509 에서 남았던 꼴 — 소득 종류가 끼거나 다른 서류 이름, 두 자리 연도
           'IRP 2025 퇴직소득 원천징수영수증 보면 퇴직소득세 얼마예요?',
           'IRP 2025 퇴직 소득 원천 징수 영수증 보면 퇴직소득세 얼마예요?',
           'IRP 2025 연금소득 원천징수영수증 보면 세금 얼마예요?',
           'IRP 2025 지급명세서 보면 세금 얼마예요?', '연금저축 2024 납입증명서 보면 세액공제 얼마예요?',
           'IRP 2025 납입확인서 보면 세액공제 얼마예요?', 'IRP 2025 원천징수 명세 보면 세금 얼마예요?',
           'IRP 24 원천징수영수증 보면 퇴직소득세 얼마예요?',
           'IRP 24 귀속 퇴직소득세 300만원이면 연금으로 받을 때 세금 얼마예요?',
           'IRP 2025 귀속 퇴직소득세 300만원이면 연금으로 받을 때 세금 얼마예요?',
           '연금저축 2000 귀속 세액공제 얼마예요?', f'연금저축 {NEXT_YEAR} 귀속 세액공제 얼마예요?',
           '연금저축 1990 원천징수영수증 보면 세액공제 얼마예요?',
           '연금저축 2025년도 세액공제 얼마예요?', '연금저축 2025 연도 세액공제 얼마예요?')
for q in YEAR_QS:
    u = unread(q)
    ok(not any(r == ASK for _, r in u), f'연도를 단위 없는 금액으로 되물음: {q} → {u}')
    ok(u == unread(no_year(q)), f'연도 하나로 그물 결과가 달라짐: {q} → {u} / {no_year(q)} → {unread(no_year(q))}')
    ok(bare(q) == [], f'연도를 단위 없는 숫자로 읽음: {q} → {bare(q)}')
# 509 퇴직 갈래 — 퇴직소득세를 읽은 질문은 연도와 상관없이 퇴직 갈래로 되묻고, 답에 연도가 없다
for q, y in (('IRP 2024 원천 징수 영수증에 퇴직소득세 500만원인데 연금 수령하면 세금 얼마예요?', '2024'),
             ('IRP 2022원천징수영수증 퇴직소득세 400만원이면 얼마나 감면돼요?', '2022'),
             ('IRP 24 귀속 퇴직소득세 300만원이면 연금으로 받을 때 세금 얼마예요?', '24')):
    u, a = ask(q)
    ok(retire_branch(a) and '조건이 더 필요해요' in a and not re.search(r'(?<!\d)' + y + r'(?!\d)', a), f'509 퇴직 갈래: {q}\n{a}')

# 연도 뒤에 금액이 따로 있으면 그 금액은 그대로 계산한다
for q in ('연금저축 2024 원천징수영수증 기준으로 600 넣으면 세액공제 얼마예요?',
          'IRP 2025 지급명세서 보고 연금저축 600 넣으면 세액공제 얼마예요?'):
    c = taxcalc.compute(q) or {}
    ok(any(b.get('numbers') for b in c.get('blocks', [])) and unread(q) == [], f'연도 뒤 금액 계산: {q} → {unread(q)}')

# 정규식 단위 — 연도 말 앞의 숫자만 뺀다
for t, b in (('IRP 2025 원천징수영수증', []), ('IRP 2025 원천징수', ['2025']), ('IRP 2025 기준', ['2025']),
             ('IRP 2025 연말정산', ['2025']), ('IRP 2025 귀속', []), ('IRP 2050 귀속', ['2050']),
             ('IRP 24 원천징수영수증', []), ('IRP 300 영수증', ['300']), ('IRP 1500 기준', ['1500']),
             ('IRP 2025년', []), ('IRP 2025 넣으면', ['2025'])):
    ok(bare(t) == b, f'단위 없는 숫자: {t} → {bare(t)}')

# ── 지금 맞게 도는 질문은 그대로 — 평가셋·같은 뜻 다른 표현·대화 시험 질문에서 연도를 금액으로 되묻지 않고, 평가셋은 그물에 걸리지 않는다
ev = json.load(open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
qs = [it['question'] for it in ev]
for g in json.load(open(os.path.join(ROOT, 'eval', 'paraphrase_set.json'), encoding='utf-8')):
    qs += [g['canonical']] + [it['q'] for it in g['items']]
for d in json.load(open(os.path.join(ROOT, 'eval', 'dialogs.json'), encoding='utf-8'))['dialogues']:
    qs += [t['user'] for t in d['turns']] + [t['expected_standalone'] for t in d['turns'] if t.get('expected_standalone')]
year_ask = [q for q in qs if any(r == ASK and re.fullmatch(r'(?:19|20)\d\d', t) for t, r in unread(q))]
ok(not year_ask, f'평가 질문에서 연도를 되물음: {year_ask}')
ok(not [it['id'] for it in ev if unread(it['question'])], '평가셋 100문항은 그물에 걸리지 않는다')

print(f'{passed}/{total} 통과')
sys.exit(0 if passed == total else 1)
