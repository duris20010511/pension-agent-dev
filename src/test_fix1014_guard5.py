# -*- coding: utf-8 -*-
"""
10-14 5차 결함 검토 — guard5 묶음(orchestrator._YEAR_CTX · _BARE_AFTER_ACCT · unread_amounts) 회귀 시험. API 호출 없음.

    python src/test_fix1014_guard5.py

4차 수정 뒤 회귀(410~413): _BARE_AFTER_ACCT 가 _WON_UNIT 을 함께 쓰게 되면서 '원천'의 '원'을 단위로 보지 않자, 계좌 이름 바로 뒤
연도('IRP 2025 원천징수영수증')를 단위 없는 금액으로 되물었다(3차 결함 209 꼴). 4자리 연도(19xx·20xx) 뒤에 원천징수·영수증·년(도)·
연도·기준·귀속·연말정산이 오면 금액이 아니다. 다만 어림수(1900·2000)는 금액으로 흔히 쓰는 꼴이라 그대로 되묻는다.
6차 검토(506~508)에서 바뀜: 기준·연말정산·'원천징수'만은 금액에도 붙어 연도 말에서 뺐고, 어림수 대신 뒤 말로 가른다
(자세한 시험은 test_fix1014_guard6.py).
answer.py 가 /chat 에서 부르는 조건과 같게 taxcalc.compute 결과를 calc 로 넘긴다.
"""
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


def no_year(q):
    """연도만 지운 같은 질문 — 연도는 금액이 아니므로 그물 결과가 같아야 한다."""
    return re.sub(r'(IRP|저축)\s*(?:19|20)\d\d\s*', r'\1 ', q)


def retire_branch(a):
    return '연차' in a and '30%' in a and '총급여' not in a and '무엇을 계산할지' not in a


# ── 결함 410 — 계좌 이름 뒤 연도를 금액 후보로 보여 주지 않는다(고치기 전: [('300만 원','퇴직소득세')]에 '조건이 더 필요해요')
q = 'IRP 2025 원천징수영수증에 퇴직소득세 300만원인데 연금으로 받으면 세금 얼마예요?'
u, a = ask(q)
ok(u == [('300만 원', '퇴직소득세')], f'410 읽은 숫자: {u}')
ok('조건이 더 필요해요' in a and '읽지 못했어요' not in a and '2025' not in a, f'410 연도를 못 읽은 금액으로 보임:\n{a}')
ok(retire_branch(a), f'410 퇴직 갈래: {a}')

# ── 결함 411 — '연금저축 2024 원천징수영수증 기준으로 세액공제 얼마예요?'(고치기 전 [])
q = '연금저축 2024 원천징수영수증 기준으로 세액공제 얼마예요?'
ok(unread(q) == [], f'411 연도를 되물음: {unread(q)}')

# ── 결함 412 — 'IRP 2025 원천징수 세금 얼마예요?'(고치기 전 []). 6차(검토 506)에서 바뀜: '원천징수'만으로는 연도로 보지 않는다
# ('IRP 1980 원천징수되면'은 금액이다) — 숫자 없이 단위를 되묻는다
q = 'IRP 2025 원천징수 세금 얼마예요?'
ok(unread(q) == [('2025', ASK)], f'412 단위 되묻기: {unread(q)}')

# ── 결함 413 — '개인형IRP 2024 원천징수영수증'(고치기 전 []), 퇴직금 금액이 함께 있으면 퇴직금만 읽은 숫자
q = '개인형IRP 2024 원천징수영수증 보면 퇴직소득세 얼마예요?'
ok(unread(q) == [], f'413 연도를 되물음: {unread(q)}')
q = 'IRP 2024 원천징수영수증 기준 퇴직금 1억이면 연금으로 받을 때 세금 얼마예요?'
u, a = ask(q)
ok(u == [('1억 원', '퇴직금')], f'413 읽은 숫자: {u}')
ok('조건이 더 필요해요' in a and '읽지 못했어요' not in a and '2024' not in a, f'413 연도를 못 읽은 금액으로 보임:\n{a}')
ok(retire_branch(a), f'413 퇴직 갈래: {a}')

# ── 같은 꼴의 다른 표현 — 붙여 쓰기·영수증·귀속·년도·연도·19xx. 연도를 지운 질문과 결과가 같다
# (6차 검토 506~508에서 바뀜: 기준·연말정산·'원천징수'만은 금액에도 붙어 아래 되묻기 쪽으로 옮겼다)
for q in ('IRP 2025 원천징수영수증에 퇴직소득세 300만원인데 연금으로 받으면 세금 얼마예요?',
          'IRP 2025원천징수영수증에 퇴직소득세 300만원인데 연금으로 받으면 세금 얼마예요?',
          '연금저축 2024 원천징수영수증 기준으로 세액공제 얼마예요?',
          '개인형IRP 2024 원천징수영수증 보면 퇴직소득세 얼마예요?',
          'IRP 2024 원천징수영수증 기준 퇴직금 1억이면 연금으로 받을 때 세금 얼마예요?',
          'IRP 2025 영수증 보면 퇴직소득세 얼마예요?', 'IRP 2025 귀속 퇴직소득세 300만원이면 연금으로 받을 때 세금 얼마예요?',
          '연금저축 2025년도 세액공제 얼마예요?', '연금저축 2000 귀속 세액공제 얼마예요?',
          '연금저축 2025 년도 세액공제 얼마예요?', '연금저축 2025 연도 세액공제 얼마예요?',
          'IRP 1999 원천징수영수증 퇴직소득세 얼마예요?'):
    u = unread(q)
    ok(not any(r == ASK for _, r in u), f'연도를 단위 없는 금액으로 되물음: {q} → {u}')
    ok(u == unread(no_year(q)), f'연도 하나로 그물 결과가 달라짐: {q} → {u} / {no_year(q)} → {unread(no_year(q))}')
    ok(orchestrator._BARE_AFTER_ACCT.findall(taxcalc.normalize(q)) == [], f'연도를 단위 없는 숫자로 읽음: {q}')

# 연도 뒤에 금액이 따로 있으면 그 금액은 그대로 계산한다
for q in ('연금저축 2024 원천징수영수증 기준으로 600 넣으면 세액공제 얼마예요?',
          'IRP 2025 원천징수영수증 보고 연금저축 600 넣으면 세액공제 얼마예요?'):
    c = taxcalc.compute(q) or {}
    ok(any(b.get('numbers') for b in c.get('blocks', [])) and unread(q) == [], f'연도 뒤 금액 계산: {q} → {unread(q)}')

# ── 금액일 수 있는 숫자는 그대로 되묻는다 — 연도 꼴이 아니거나(1500·600·300), 연도에만 붙는 말이 없거나(넣으면·기준·
# 연말정산·원천징수되면 — 6차 검토 506~508에서 기준·연말정산·원천징수를 연도 말에서 뺐다)
for q, n in (('IRP 2025 넣으면 세금 얼마나 줄어요?', '2025'), ('IRP 2000 넣으면 세금 얼마나 줄어요?', '2000'),
             ('연금저축 2000 기준으로 세액공제 얼마예요?', '2000'), ('IRP 2000 연말정산하면 얼마 돌려받아요?', '2000'),
             ('IRP 2025 원천징수 세금 얼마예요?', '2025'), ('연금저축 2025 기준 세액공제 얼마예요?', '2025'),
             ('연금저축 2025기준 세액공제 얼마예요?', '2025'), ('연금저축 2025 연말정산 세액공제 얼마예요?', '2025'),
             ('연금저축 2025 연말 정산 세액공제 얼마예요?', '2025'), ('IRP 2025 연말정산하면 얼마 돌려받아요?', '2025'),
             ('IRP 1900 원천징수되면 세금 얼마예요?', '1900'),
             ('IRP 300 원천징수되면 세금 얼마예요?', '300'), ('연금저축 600 원천징수 세금 얼마예요?', '600'),
             ('연금저축 1000 원금 보장 상품에 넣으면 세액공제 얼마예요?', '1000')):
    u, a = ask(q)
    ok((n, ASK) in u and '읽지 못했어요' in a, f'금액일 수 있는 숫자를 놓침: {q} → {u}')
# 'IRP 1500 기준' 꼴은 계산기가 금액으로 읽는다(연도 꼴 아님)
c = taxcalc.compute('연금저축 1500 기준으로 세액공제 얼마예요?') or {}
ok(any(b.get('numbers') for b in c.get('blocks', [])), '1500 기준 계산')
for t, bare in (('IRP 2025 원천징수', ['2025']), ('IRP 2025 영수증', []), ('연금저축 2024 귀속', []), ('연금저축 2024 연도', []),
                ('IRP 2000 기준', ['2000']), ('IRP 1900 원천징수', ['1900']), ('IRP 1500 기준', ['1500']),
                ('IRP 2025 넣으면', ['2025']), ('IRP 2025년', []), ('IRP 2000년', [])):
    ok(orchestrator._BARE_AFTER_ACCT.findall(taxcalc.normalize(t)) == bare,
       f'단위 없는 숫자: {t} → {orchestrator._BARE_AFTER_ACCT.findall(taxcalc.normalize(t))}')

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
