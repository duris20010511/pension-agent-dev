# -*- coding: utf-8 -*-
"""
10-14 4차 결함 검토 — guard4 묶음(orchestrator.unread_amounts · unread_amount_answer · _WON_TEXT) 회귀 시험. API 호출 없음.

    python src/test_fix1014_guard4.py

3차 수정 뒤 회귀:
결함 304: '퇴직금 1억 IRP에 넣었고 하루 2천원이면 세금 얼마나 줄어요?' — 퇴직금 금액을 읽었다는 이유로 동사 없는 낯선 금액을
          퇴직 쪽에 붙여 퇴직 갈래로 갔다(고치기 전 세액공제 갈래). '퇴직금 IRP 하루 2천원이면'과 금액 하나 차이로 뒤집혔다.
결함 305: '퇴직금 1억 IRP에 따로 매주 5만원이면'·'IRP에 내 돈 하루 3천원씩이면'·'…씩 적립하면' — 퇴직금을 읽으면 '따로·내 돈'을
          보지 않고, 적립·저축은 넣는 말로 보지 않아 퇴직소득세·연차를 되물었다.
결함 306: '50000원납입', '2천원저축', '5천원적립', '50000원부으면', '2천원모으면', '2천원뺀', '3천원선', '2천원남짓', '2천원가까이' —
          '원' 뒤 허용 목록이 좁아 붙여 쓴 금액을 놓쳤다(그물이 비어 모델이 숫자를 채울 수 있었다).
결함 307: '연금저축 1000 원금 보장 상품' — _WON_TEXT 는 원금을 뺐는데 _BARE_AFTER_ACCT 는 원금의 '원'을 단위로 보아 어느 그물에도
          걸리지 않았다.
결함 308: 'IRP에 주당 3만원씩 넣으면' — '주당'을 기간 말에서 통째로 빼서 3만 원 납입으로 확실히 읽은 것처럼 보였다.
answer.py 가 /chat 에서 부르는 조건과 같게 taxcalc.compute 결과를 calc 로 넘긴다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import orchestrator
import taxcalc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
passed, total = 0, 0


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


def branch(a):
    """되묻기 갈래 — 퇴직(감면율) / 세액공제(총급여·계좌별 납입액) / 무엇을 계산할지 / 인출 / 수령."""
    if '30%' in a and '연차' in a:
        return 'retire'
    if '계좌별 1년 납입액' in a:
        return 'credit'
    if '무엇을 계산할지' in a:
        return 'what'
    if '꺼내려는 금액' in a:
        return 'withdraw'
    if '1년에 받을 금액' in a:
        return 'recv'
    return 'none' if not a else 'other'


def odd_shown(u, part):
    return any(part in t and r == '단위·기간 확인 필요' for t, r in u)


# ── 결함 304 — 동사 없는 낯선 금액은 퇴직금 금액을 읽었다는 이유로 퇴직 쪽에 붙지 않는다
u, a = ask('퇴직금 1억 IRP에 넣었고 하루 2천원이면 세금 얼마나 줄어요?')
ok(('1억 원', '퇴직금') in u and odd_shown(u, '하루 2천원'), f'304 읽은 숫자: {u}')
ok(branch(a) == 'credit' and '퇴직소득세' not in a, f'304 세액공제 갈래(고치기 전과 같음): {a}')
# 퇴직금 금액만 붙여도 갈래가 뒤집히지 않는다
pair = [branch(ask(q)[1]) for q in ('퇴직금 IRP 하루 2천원이면 세금 얼마나 줄어요?', '퇴직금 1억 IRP 하루 2천원이면 세금 얼마나 줄어요?')]
ok(pair[0] == pair[1] and 'retire' not in pair, f'304 퇴직금 금액 하나로 갈래가 뒤집힘: {pair}')
# '얼마나 감면돼요'도 세금 계산 말 — 그물을 빠져 생성으로 가지 않는다
u, a = ask('퇴직금 1억 IRP에 넣었고 하루 2천원이면 얼마나 감면돼요?')
ok(odd_shown(u, '하루 2천원') and branch(a) != 'retire', f'304 감면 말도 그물에 걸림: {u} / {branch(a)}')

# ── 결함 305 — 내 돈·따로·적립은 퇴직금을 읽었어도 퇴직소득세를 되묻지 않는다
for q, part in (('퇴직금 1억 IRP에 따로 매주 5만원이면 세금 얼마나 줄어요?', '매주 5만원'),
                ('퇴직소득세 300만원 냈고 IRP에 내 돈 하루 3천원씩이면 세금 얼마나 줄어요?', '하루 3천원'),
                ('퇴직금 1억 있는 IRP에 내 돈 하루 2천원씩 더 적립하면 세금 얼마나 줄어요?', '하루 2천원'),
                ('퇴직금 1억 있는 IRP에 매주 5만원씩 적립하면 세금 얼마나 줄어요?', '매주 5만원')):
    u, a = ask(q)
    ok(odd_shown(u, part), f'305 낯선 금액: {q} → {u}')
    ok(branch(a) in ('credit', 'what') and '원천징수영수증' not in a and '감면율' not in a, f'305 퇴직소득세를 되물음: {q}\n{a}')
# 적립은 넣는 말이다 — 세액공제 갈래
ok(branch(ask('퇴직금 1억 있는 IRP에 매주 5만원씩 적립하면 세금 얼마나 줄어요?')[1]) == 'credit', '305 적립 → 세액공제 갈래')
ok(orchestrator._odd_use('매주 5만원', '퇴직금 1억 있는 IRP에 매주 5만원씩 적립하면 세금 얼마나 줄어요?') == 'in', '적립 = 넣는 말')
ok(orchestrator._odd_use('하루 2천원', '하루 2천원 연금저축에서 받으면 세금 얼마예요?') == 'out', "'연금저축'의 저축은 넣는 말이 아님")

# ── 결함 306 — 붙여 쓴 '원' 금액을 놓치지 않는다
for q, part in (('연금저축 매달 50000원납입하면 세액공제 얼마예요?', '50000원'), ('연금저축에 하루 2천원저축하면 세액공제 얼마예요?', '하루 2천원'),
                ('IRP에 5천원적립하면 세금 얼마나 줄어요?', '5천원'), ('연금저축 50000원부으면 세액공제 얼마예요?', '50000원'),
                ('연금저축 하루 2천원모으면 세액공제 얼마예요?', '하루 2천원'), ('연금저축 2천원뺀 금액 세금 얼마예요?', '2천원'),
                ('연금저축에 월 3천원선으로 넣으면 세액공제 얼마예요?', '3천원'), ('연금저축 하루 2천원남짓 넣으면 세액공제 얼마예요?', '하루 2천원'),
                ('IRP 2천원가까이 넣으면 세금 얼마나 줄어요?', '2천원')):
    u, a = ask(q)
    ok(odd_shown(u, part) and '읽지 못했어요' in a, f'306 붙여 쓴 원 금액을 놓침: {q} → {u}')
for t, want in (('50000원납입', '50000원'), ('2천원저축', '2천원'), ('2천원뺀', '2천원'), ('3천원선', '3천원'),
                ('2천원남짓', '2천원'), ('2천원가까이', '2천원'), ('50000원부으면', '50000원'), ('50000원본인', '50000원'),
                ('수수료 2천원래요', '2천원')):
    m = orchestrator._WON_TEXT.search(taxcalc.normalize(t))
    ok(m and m.group(0).strip() == want, f'306 원 금액: {t} → {m and m.group(0)}')

# ── 결함 307 — '원금'의 '원'은 단위가 아니다. 두 정규식이 같은 규칙
u, a = ask('연금저축 1000 원금 보장 상품에 넣으면 세액공제 얼마예요?')
ok(u == [('1000', '단위(만 원인지) 확인 필요')], f'307 단위 없는 숫자로 되물음: {u}')
ok(branch(a) == 'credit', f'307 세액공제 갈래: {a}')
for t, bare in (('연금저축 1000 원금 보장', ['1000']), ('연금저축 300 원리금 보장형', ['300']), ('IRP 600 원래대로', ['600']),
                ('연금저축 1000 원 넣으면', []), ('연금저축 1000원', []), ('IRP 500 원씩', [])):
    n = taxcalc.normalize(t)
    ok(orchestrator._BARE_AFTER_ACCT.findall(n) == bare, f'307 단위 없는 숫자: {t} → {orchestrator._BARE_AFTER_ACCT.findall(n)}')
    # 단위 없는 숫자로 잡으면 '원' 금액으로는 안 잡고, 그 반대도 같다(한쪽만 걸리면 빈틈)
    ok(bool(bare) != bool(orchestrator._WON_TEXT.search(n)), f'307 두 그물 중 하나만: {t}')
for t in ('2025 원천징수영수증', '2024원천징수', '원금 1000 원금', '3 원래 그래요', '17 원문', '2000 원리금', '3 원칙대로'):
    ok(not orchestrator._WON_TEXT.search(taxcalc.normalize(t)), f'원 뒤 다른 낱말을 금액으로 읽음: {t}')

# ── 결함 308 — '주당'은 주식 말이 없으면 한 주(週), '…씩 넣'이면 매주
for q in ('IRP에 주당 3만원씩 넣으면 세금 얼마나 줄어요?', 'IRP에 주당 3만원 넣으면 세금 얼마나 줄어요?',
          'IRP로 주식 주당 3만원씩 넣으면 세금 얼마나 줄어요?'):
    u, a = ask(q)
    ok(odd_shown(u, '주당 3만원') and ('3만 원', '납입') not in u, f'308 주당 금액을 납입으로 확실히 읽음: {q} → {u}')
    ok('읽지 못했어요' in a, f'308 확실히 읽은 것처럼 보임: {q}')
# 주식 말이 있으면 한 주(株) 값 — 기간 아님(3차 결함 210 유지)
for q in ('연금저축으로 ETF 주당 5만원짜리 사면 세금 얼마예요?', 'ETF 한 주당 5만원짜리 IRP에서 사면 세금 얼마예요?',
          'IRP 종목 1주당 2만원이면 세금 얼마예요?'):
    ok(not any(r == '단위·기간 확인 필요' for _, r in unread(q)), f'308 한 주(株) 값을 기간으로 읽음: {q} → {unread(q)}')
nq = taxcalc.norm_ws(taxcalc.normalize('1주당 5만원인 ETF를 IRP에서 사면 세금 얼마예요?'))
ok(orchestrator._odd_amounts(nq, taxcalc.parse(nq)['spans']) == {}, '1주당 ETF → 낯선 기간 아님')

# ── 1~3차에서 고친 것은 그대로
def retire_branch(a):
    return ('연차' in a and '30%' in a and '40%' in a and '50%' in a and '총급여' not in a and '납입액' not in a
            and '1년에 받을 금액' not in a and '무엇을 계산할지' not in a)


for q in ('퇴직금 5천만 원 IRP에 넣으면 세금 얼마나 줄어요?', '퇴직금 2억이면 퇴직소득세 얼마나 감면돼요?',
          '퇴직소득세 1,000만 원이면 연금으로 받을 때 얼마나 아껴요?', '퇴직금 1억을 따로 IRP에 넣으면 세금 얼마나 줄어요?',
          '퇴직금 1억 IRP에 넣고 분기마다 300만원씩 받으면 퇴직소득세 얼마나 줄어요?',
          '퇴직금을 매주 50만원씩 연금으로 받으면 퇴직소득세 얼마나 줄어요?', '퇴직소득세 500만원, 하루 5만원이면 얼마나 감면돼요?',
          '퇴직금 IRP에 넣어두고 매주 50만원씩 받으면 퇴직소득세 얼마나 줄어요?'):
    u, a = ask(q)
    ok(u and retire_branch(a), f'퇴직 갈래 유지: {q}\n{a}')
for q in ('IRP에 퇴직금 말고 따로 300만원 넣으면 세금 얼마나 줄어요?', '퇴직금 받은 IRP에 내 돈 500만원 더 넣으면 세금 얼마나 줄어요?',
          '퇴직금 받은 IRP에 매주 5만원 넣으면 세금 얼마나 줄어요?', '퇴직금 IRP에 하루 2천원씩 넣으면 세금 얼마나 줄어요?',
          '퇴직금 1억 IRP에 넣고 매주 5만원씩 더 넣으면 세금 얼마나 줄어요?'):
    u, a = ask(q)
    ok(u and branch(a) == 'credit', f'세액공제 갈래 유지: {q}\n{a}')

# ── 지금 맞게 도는 질문은 그대로 — 평가셋·같은 뜻 다른 표현·대화 시험 질문에 새 낯선 금액·되묻기가 생기지 않는다
ev = json.load(open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
qs = [it['question'] for it in ev]
for g in json.load(open(os.path.join(ROOT, 'eval', 'paraphrase_set.json'), encoding='utf-8')):
    qs += [g['canonical']] + [it['q'] for it in g['items']]
for d in json.load(open(os.path.join(ROOT, 'eval', 'dialogs.json'), encoding='utf-8'))['dialogues']:
    qs += [t['user'] for t in d['turns']] + [t['expected_standalone'] for t in d['turns'] if t.get('expected_standalone')]
ok(not [q for q in qs if any(r == '단위·기간 확인 필요' for _, r in unread(q))], '평가 질문에 낯선 금액 되묻기가 생김')
ok(not [it['id'] for it in ev if unread(it['question'])], '평가셋 100문항은 그물에 걸리지 않는다')

print(f'{passed}/{total} 통과')
sys.exit(0 if passed == total else 1)
