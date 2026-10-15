# -*- coding: utf-8 -*-
"""
10-14 3차 결함 검토 — guard3 묶음(orchestrator.unread_amounts · unread_amount_answer) 회귀 시험. API 호출 없음.

    python src/test_fix1014_guard3.py

2차 수정(낯선 기간 금액을 모두 납입으로 보고 퇴직금 읽음보다 앞에 둠, '원' 뒤 경계 없음, '주당'을 기간으로 봄) 뒤 회귀:
결함 206: '퇴직금 1억 IRP에 넣고 분기마다 300만원씩 받으면 퇴직소득세 얼마나 줄어요?' — 총급여·계좌별 납입액을 되물었다(1차 결함 19 재발).
결함 207: '퇴직소득세 500만원 냈는데 하루 5만원씩 연금으로 받으면 얼마나 감면돼요?' — 수령 갈래로 가서 연차·감면율이 빠지고
          이미 말한 퇴직소득세를 다시 물었다.
결함 208: '퇴직금을 매주 50만원씩 연금으로 받으면 퇴직소득세 얼마나 줄어요?' — 받는 돈까지 납입으로 보아 수령 갈래로 갔다.
결함 209: '2025 원천징수영수증에 …' — '2025 원(천징수)'를 금액으로 읽어 퇴직 갈래 대신 수령 갈래로 갔다.
결함 210: 'ETF 주당 5만원짜리' — 한 주(株) 값을 기간으로 보아 '주당 5만원(단위·기간 확인 필요)'으로 보였다.
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


def retire_branch(a):
    """퇴직 갈래 되묻기 — 연차·감면율 문장이 있고 총급여·납입액·수령 갈래 질문이 없다."""
    return ('연차' in a and '30%' in a and '40%' in a and '50%' in a and '총급여' not in a and '납입액' not in a
            and '1년에 받을 금액' not in a and '무엇을 계산할지' not in a)


# ── 결함 206·207·208 — 받는 돈이 낯선 기간이어도 퇴직금·퇴직소득세 질문은 퇴직 갈래
u, a = ask('퇴직금 1억 IRP에 넣고 분기마다 300만원씩 받으면 퇴직소득세 얼마나 줄어요?')
ok(('1억 원', '퇴직금') in u and any('분기마다 300만원' in t for t, _ in u), f'206 읽은 숫자: {u}')
ok(retire_branch(a) and '원천징수영수증' in a, f'206 퇴직 갈래(퇴직소득세·연차): {a}')
u, a = ask('퇴직소득세 500만원 냈는데 하루 5만원씩 연금으로 받으면 얼마나 감면돼요?')
ok(('500만 원', '퇴직소득세') in u and any('하루 5만원' in t for t, _ in u), f'207 읽은 숫자: {u}')
ok(retire_branch(a), f'207 퇴직 갈래: {a}')
ok('원천징수영수증' not in a and '퇴직금이 섞였다면' not in a, f'207 이미 말한 퇴직소득세를 또 물음: {a}')
u, a = ask('퇴직금을 매주 50만원씩 연금으로 받으면 퇴직소득세 얼마나 줄어요?')
ok(any('매주 50만원' in t and r == '단위·기간 확인 필요' for t, r in u), f'208 낯선 기간은 그대로 보임: {u}')
ok(retire_branch(a) and '원천징수영수증' in a, f'208 퇴직 갈래: {a}')
# 같은 꼴 — 받는 말이 금액 뒤에 오면 앞의 '넣'(퇴직금을 넣는 말)에 끌리지 않는다
u, a = ask('퇴직금 IRP에 넣어두고 매주 50만원씩 받으면 퇴직소득세 얼마나 줄어요?')
ok(u and retire_branch(a), f'퇴직금 넣고 매주 받기 → 퇴직 갈래: {a}')
# 동사 없는 낯선 금액 — 퇴직소득세를 읽었으면 그 읽음이 앞선다
u, a = ask('퇴직소득세 500만원, 하루 5만원이면 얼마나 감면돼요?')
ok(u and retire_branch(a), f'동사 없는 낯선 금액 + 퇴직소득세 → 퇴직 갈래: {a}')

# ── 결함 209 — '원' 뒤 낱말 경계: '2025 원천징수'는 금액이 아니다
for q, shown, need_rtax in (('2025 원천징수영수증에 퇴직소득세 300만원인데 연금으로 받으면 세금 얼마예요?', ('300만 원', '퇴직소득세'), False),
                            ('퇴직금 1억, 2024 원천징수영수증 기준으로 연금 받으면 세금 얼마예요?', ('1억 원', '퇴직금'), True)):
    u, a = ask(q)
    ok(u == [shown], f'209 읽은 숫자: {q} → {u}')
    ok(retire_branch(a) and ('원천징수영수증' in a) == need_rtax, f'209 퇴직 갈래: {q}\n{a}')
    ok('읽지 못했어요' not in a, f'209 숫자를 다 읽었는데 못 읽었다고 함: {q}')
for t in ('2025 원천징수영수증', '2024원천징수', '원금 1000 원금', '3 원래 그래요', '17 원문'):
    ok(not orchestrator._WON_TEXT.search(taxcalc.normalize(t)), f'원 뒤 다른 낱말을 금액으로 읽음: {t}')
# 금액 '원'은 그대로 — 조사·말이 붙거나 띄어 쓰거나 문장부호가 와도
for t, want in (('월 50000원씩', '50000원'), ('5천원이면', '5천원'), ('2천원넣으면', '2천원'), ('30000원을', '30000원'),
                ('5000원짜리', '5000원'), ('2000원인데', '2000원'), ('1000원/월', '1000원'), ('2천원)', '2천원'),
                ('2천원.', '2천원'), ('2000원 원리금', '2000원'), ('하루 2천원', '2천원')):
    m = orchestrator._WON_TEXT.search(t)
    ok(m and m.group(0).strip() == want, f'금액 원을 놓침: {t} → {m and m.group(0)}')

# ── 결함 210 — '주당'은 한 주(株) 값이다
u, a = ask('연금저축으로 ETF 주당 5만원짜리 사면 세금 얼마예요?')
ok(u == [('5만 원', '어디에 쓰는 돈인지 확인 필요')], f'210 읽은 숫자(고치기 전 동작): {u}')
ok('무엇을 계산할지' in a, f'210 같은 갈래: {a}')
nq = taxcalc.norm_ws(taxcalc.normalize('1주당 5만원인 ETF를 IRP에서 사면 세금 얼마예요?'))
ok(orchestrator._odd_amounts(nq, taxcalc.parse(nq)['spans']) == {}, '1주당 → 낯선 기간 아님')
# 한 주(週) 말은 여전히 낯선 기간
for q, part in (('IRP 매주 3만원씩 넣으면 세금 얼마나 줄어요?', '매주 3만원'), ('IRP 일주일에 3만원씩 넣으면 세금 얼마나 줄어요?', '일주일에 3만원'),
                ('IRP에 주마다 3만원 넣으면 세금 얼마나 줄어요?', '주마다 3만원')):
    ok(any(part in t and r == '단위·기간 확인 필요' for t, r in unread(q)), f'한 주(週) 기간을 놓침: {q} → {unread(q)}')

# ── 1차·2차에서 고친 것은 그대로
# 결함 19: 퇴직금·퇴직소득세 질문은 퇴직 갈래
for q in ('퇴직금 5천만 원 IRP에 넣으면 세금 얼마나 줄어요?', '퇴직금 2억이면 퇴직소득세 얼마나 감면돼요?',
          '퇴직소득세 1,000만 원이면 연금으로 받을 때 얼마나 아껴요?'):
    u, a = ask(q)
    ok(u and retire_branch(a), f'결함 19 퇴직 갈래 유지: {q}\n{a}')
# 결함 105: 퇴직금과 따로 넣는 내 돈은 세액공제 갈래
for q in ('IRP에 퇴직금 말고 따로 300만원 넣으면 세금 얼마나 줄어요?', '퇴직금 받은 IRP에 내 돈 500만원 더 넣으면 세금 얼마나 줄어요?',
          '퇴직금 들어온 IRP에 개인 돈 900만원 추가 납입하면 세금 얼마 줄어요?'):
    u, a = ask(q)
    ok(u and '총급여' in a and '연차' not in a and '30%' not in a, f'결함 105 세액공제 갈래 유지: {q}\n{a}')
# 2차: 낯선 금액도 넣는 돈이면 납입 — 세액공제 갈래(퇴직금을 읽었어도 따로 더 넣는 돈이면 그쪽)
for q in ('퇴직금 받은 IRP에 매주 5만원 넣으면 세금 얼마나 줄어요?', '퇴직금 IRP에 하루 2천원씩 넣으면 세금 얼마나 줄어요?',
          '퇴직금 1억 IRP에 넣고 매주 5만원씩 더 넣으면 세금 얼마나 줄어요?', '퇴직금 IRP 하루 2천원이면 세금 얼마나 줄어요?'):
    u, a = ask(q)
    ok(u and '총급여' in a and '연차' not in a and '30%' not in a, f'2차 넣는 낯선 금액 → 세액공제 갈래 유지: {q}\n{a}')
# 결함 106: 결론 확인이어도 낯선 금액이면 되묻기
u, a = ask('연금저축 하루 2천원씩 넣으면 세액공제 12만원 맞죠?')
ok(('12만 원', '어디에 쓰는 돈인지 확인 필요') in u and any('2천원' in t for t, _ in u) and '총급여' in a, f'결함 106 유지: {u}')
# 공제를 '받'는 말은 받는 돈이 아니다 — '하루 2천원씩 넣어서 받을 수 있는 세액공제'
ok(orchestrator._odd_use('하루 2천원', '퇴직금 IRP에 하루 2천원씩 넣어서 받을 수 있는 세액공제 얼마예요?') == 'in', '넣는 말 먼저')
ok(orchestrator._odd_use('하루 2천원', '퇴직금 IRP 하루 2천원이면 얼마나 돌려받아요?') is None, '돌려받는 말은 쓰임 동사가 아님')
ok(orchestrator._odd_use('매주 50만원', '퇴직금을 매주 50만원씩 연금으로 받으면 퇴직소득세 얼마나 줄어요?') == 'out', '받는 말')

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
