# -*- coding: utf-8 -*-
"""
10-14 2차 결함 검토 — guard2 묶음(orchestrator.unread_amounts · unread_amount_answer) 회귀 시험. API 호출 없음.

    python src/test_fix1014_guard2.py

결함 105: 'IRP에 퇴직금 말고 따로 300만원 넣으면 세금 얼마나 줄어요?' — 읽은 숫자는 300만 원(납입)인데 퇴직 갈래로 가서
          퇴직소득세·평가액·연차를 되묻고 퇴직소득세 감면 문장을 붙였다. 납입이면 세액공제 갈래(총급여·계좌별 1년 납입액)다.
결함 106: '연금저축 하루 2천원씩 넣으면 세액공제 12만원 맞죠?' — '맞죠'(결론 확인) 때문에 그물 밖으로 나가 계산값 없이
          모델 경로로 갔다. 파서는 '2천원'을 금액으로 읽지도 못했다 → 모델이 금액을 채운다. 되물어야 한다.
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


# ── 결함 105 — 퇴직금과 따로 넣는 내 돈은 세액공제 질문이다
for q, shown in (('IRP에 퇴직금 말고 따로 300만원 넣으면 세금 얼마나 줄어요?', '300만 원(납입)'),
                 ('퇴직금 받은 IRP에 내 돈 500만원 더 넣으면 세금 얼마나 줄어요?', '500만 원(납입)'),
                 ('퇴직금 들어온 IRP에 개인 돈 900만원 추가 납입하면 세금 얼마 줄어요?', '900만 원')):
    u, a = ask(q)
    ok(bool(u), f'되묻지 않음: {q}')
    ok(shown in a, f'읽은 숫자 표시: {q}\n{a}')
    ok('총급여' in a and '계좌별 1년 납입액' in a, f'세액공제 되묻기가 아님: {q}\n{a}')
    ok('퇴직소득세' not in a and '연차' not in a and '30%' not in a and '원천징수영수증' not in a,
       f'퇴직 갈래 되묻기·감면 문장이 붙음: {q}\n{a}')
    ok('짐작해서 답하지 않' in a, f'되묻기 문장: {q}')
# 역할로 가른다 — 같은 질문 모양이라도 읽은 숫자가 납입이면 세액공제, 퇴직금이면 퇴직 갈래(낱말보다 읽음이 앞선다)
a = orchestrator.unread_amount_answer([('300만 원', '납입')], '퇴직금 IRP에 300만원 넣으면 세금 얼마나 줄어요?')
ok('총급여' in a and '퇴직소득세' not in a, f'납입 역할인데 퇴직 갈래: {a}')
a = orchestrator.unread_amount_answer([('5,000만 원', '퇴직금')], '퇴직금 5천만 원 따로 IRP에 넣으면 세금 얼마나 줄어요?')
ok('원천징수영수증' in a and '총급여' not in a, f'퇴직금 역할인데 세액공제 갈래: {a}')
# 하루·매주 금액, 파서가 못 읽은 '원' 금액은 퇴직금(한꺼번에 받는 돈)일 수 없다 — 납입으로 보고 세액공제 되묻기
for q in ('퇴직금 받은 IRP에 매주 5만원 넣으면 세금 얼마나 줄어요?', '퇴직금 IRP에 하루 2천원씩 넣으면 세금 얼마나 줄어요?'):
    u, a = ask(q)
    ok(u and '총급여' in a and '퇴직소득세' not in a and '연차' not in a, f'낯선 금액인데 퇴직 갈래: {q}\n{a}')
# 1차에서 고친 퇴직 갈래는 그대로(결함 19)
for q in ('퇴직금 5천만 원 IRP에 넣으면 세금 얼마나 줄어요?', '퇴직금 2억이면 퇴직소득세 얼마나 감면돼요?'):
    u, a = ask(q)
    ok(u and '연차' in a and '30%' in a and '총급여' not in a and '납입액' not in a, f'퇴직 갈래 유지: {q}\n{a}')

# ── 결함 106 — 단위·기간이 낯선 금액은 결론 확인('맞죠?')이어도 되묻는다
q106 = '연금저축 하루 2천원씩 넣으면 세액공제 12만원 맞죠?'
u, a = ask(q106)
ok(('12만 원', '어디에 쓰는 돈인지 확인 필요') in u, f'고치기 전 동작(12만 원 확인 필요): {u}')
ok(any('2천원' in t and '확인 필요' in r for t, r in u), f'못 읽은 하루 2천원을 보여 줌: {u}')
ok('짐작해서 답하지 않' in a and '읽지 못했어요' in a and '총급여' in a and '1년 납입액' in a, f'세액공제 되묻기: {a}')
ok(not re.search(r'×|=\s*\d', a), f'되묻기에 계산식이 들어감: {a}')
ok(not (taxcalc.compute(q106) or {}).get('blocks') or not any(b.get('numbers') for b in taxcalc.compute(q106)['blocks']),
   '계산값이 없는 질문이다(그물이 마지막 길)')
# 같은 꼴 — 파서가 못 읽는 '원' 표기·계산기가 모르는 기간. 되묻지 않으면 계산값 없이 모델 경로로 간다
for q, part in (('연금저축 하루 2천원씩 넣으면 세액공제 얼마예요?', '하루 2천원'),
                ('월 50000원씩 연금저축에 넣으면 세액공제 얼마예요?', '50000원'),
                ('연금저축 월 5천원씩 넣으면 세액공제 얼마예요?', '5천원'),
                ('IRP 일주일에 3만원씩 넣으면 세금 얼마나 줄어요?', '일주일에 3만원'),
                ('IRP에 5천원씩 하루마다 넣으면 세액공제 몇만원이에요?', '하루마다'),
                ('연금저축 하루 2천원씩 넣으면 세액공제 얼마예요? 12만원 맞죠?', '하루 2천원')):
    u = unread(q)
    ok(any(part in t and r == '단위·기간 확인 필요' for t, r in u), f'낯선 금액 되묻기: {q} → {u}')
# 1차에서 고친 것은 그대로 — 전제 금액을 다 읽은 결론 확인, 규칙 확인, 금액 없는 확인은 문서 설명으로(그물 밖)
ev = json.load(open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
by_id = {it['id']: it['question'] for it in ev}
for q in (by_id['Q-040'], '아직 55 안 됐는데 연금저축 2천만원 찾으면 세금 얼마예요? 16.5%면 330만원 맞죠?',
          '연금저축 하루 2천원씩 넣으면 세액공제 받는 거 맞죠?', '연금저축 하루 2천원씩 넣으면 세액공제 16.5% 맞죠?',
          '하루 2천원씩 넣으면 연금저축 세액공제 한도 600만 원 맞죠?', 'IRP 세액공제 한도 900만 원 맞아요?',
          '연금저축 2천원 넣어도 세액공제 돼요?'):
    ok(unread(q) == [], f'되묻기 아님: {q} → {unread(q)}')
# 때를 말하는 '1분기에'는 낯선 기간이 아니다. 쉼표 금액('2,000원')·'만 원' 금액은 파서가 읽는다
nq = taxcalc.norm_ws(taxcalc.normalize('1분기에 IRP 300만원 넣으면 세액공제 얼마예요?'))
ok(orchestrator._odd_amounts(nq, taxcalc.parse(nq)['spans']) == {}, '1분기에 → 낯선 기간 아님')
for q in ('연금저축에 월 50,000원씩 넣으면 세액공제 얼마예요?', '연금저축 33만 6천 원 넣으면 세액공제 얼마예요?'):
    nq = taxcalc.norm_ws(taxcalc.normalize(q))
    ok(orchestrator._odd_amounts(nq, taxcalc.parse(nq)['spans']) == {}, f'파서가 읽는 금액을 못 읽었다고 봄: {q}')

# ── 지금 맞게 도는 질문은 그대로 — 평가셋·같은 뜻 다른 표현·대화 시험 질문에 새 '낯선 금액' 되묻기가 생기지 않는다
qs = [it['question'] for it in ev]
for g in json.load(open(os.path.join(ROOT, 'eval', 'paraphrase_set.json'), encoding='utf-8')):
    qs += [g['canonical']] + [it['q'] for it in g['items']]
for d in json.load(open(os.path.join(ROOT, 'eval', 'dialogs.json'), encoding='utf-8'))['dialogues']:
    qs += [t['user'] for t in d['turns']] + [t['expected_standalone'] for t in d['turns'] if t.get('expected_standalone')]
odd_hits = [q for q in qs if any(r == '단위·기간 확인 필요' for _, r in unread(q))]
ok(not odd_hits, f'평가 질문에 낯선 금액 되묻기가 생김: {odd_hits}')
ok(not [it['id'] for it in ev if unread(it['question'])], '평가셋 100문항은 그물에 걸리지 않는다')

print(f'{passed}/{total} 통과')
sys.exit(0 if passed == total else 1)
