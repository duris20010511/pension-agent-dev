# -*- coding: utf-8 -*-
"""
10-14 결함 검토 — guard 묶음(orchestrator.unread_amounts · unread_amount_answer) 회귀 시험. API 호출 없음.

    python src/test_fix1014_guard.py

되묻기 그물은 '세금 계산을 하려는데 금액을 못 읽은 짧은 질문'에만 걸려야 한다.
결함 15: 평가셋 Q-040(함정: (구)개인연금저축 + '맞죠?')·Q-011(과세이연 효과)에 걸려 총급여·'무엇을 계산할지'를 되물었다.
결함 18: 규칙 설명·확인 질문('16.5% 세금 내야 해요?', '한도 900만 원 맞아요?')을 막았고, '16.5%'·'70대'를 단위 없는 금액으로 읽었다.
결함 19: 퇴직금·퇴직소득세 질문에 총급여·납입액, '무엇을 계산할지'를 되물었다.
결함 20: 규칙 블록이 있는 '55세 전 해지 … 330만 원 내야 하나요?'를 가로채 해지 금액을 '(연금 수령)'으로 보여 줬다.
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


# ── 결함 15 — 평가셋 함정 두 문항, 그리고 평가셋 100문항 전부
ev = json.load(open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
by_id = {it['id']: it['question'] for it in ev}
for qid in ('Q-040', 'Q-011'):
    ok(unread(by_id[qid]) == [], f'{qid} 되묻기 아님: {unread(by_id[qid])}')
hits = [it['id'] for it in ev if unread(it['question'])]
ok(len(ev) == 100 and hits == [], f'평가셋 100문항 중 그물에 걸린 것: {hits}')

# ── 결함 18 — 규칙 설명·확인 질문은 문서 설명으로(그물 밖)
for q in ('연금저축 16.5% 세금 내야 해요?', 'IRP 16.5% 기타소득세는 언제 붙어요?', '연금저축 13.2% 공제는 누가 받아요?',
          '연금저축 70대 세율 몇 %예요?', '연금저축 20대에 가입하면 세액공제 받을 수 있나요?', 'IRP 세액공제 한도 900만 원 맞아요?',
          '연금저축 세액공제 한도가 600만 원이에요?', '연금소득 연 1,500만 원 이하면 분리과세로 끝나요?',
          '연금 받을 때 1년에 1,000만 원이면 종합과세 대상이에요?', '1,500만 원 초과하면 분리과세 16.5%예요?',
          '연금계좌 1,800만 원 한도 넘게 넣으면 세액공제 돼요?', '연금 받을 때 1년에 1,500만 원 넘으면 세금이 어떻게 돼요?'):
    ok(unread(q) == [], f'확인·설명 질문 되묻기 아님: {q} → {unread(q)}')
# 소수·연령대는 단위 없는 금액이 아니다
for q, n in (('연금저축 16.5% 세금', '16'), ('연금저축 13.2% 공제', '13'), ('연금저축 70대 세율', '70'), ('연금저축 20대에 가입', '20')):
    ok(n not in orchestrator._BARE_AFTER_ACCT.findall(taxcalc.normalize(q)), f'단위 없는 금액으로 읽음: {q}')

# ── 결함 19 — 퇴직금·퇴직소득세 질문은 퇴직소득세·연차를 묻는다(총급여·납입액·'무엇을 계산할지' 아님)
for q in ('퇴직금 5천만 원 IRP에 넣으면 세금 얼마나 줄어요?', '퇴직금 2억이면 퇴직소득세 얼마나 감면돼요?',
          '퇴직소득세 1,000만 원이면 연금으로 받을 때 얼마나 아껴요?', '1억 원 퇴직금 IRP로 받으면 세금이 줄어요?'):
    u, a = ask(q)
    if not u:                               # 되묻지 않으면 문서 설명(감면 규칙)으로 간다 — 그것도 기대 결과다
        ok(q.endswith('줄어요?') and '얼마' not in q, f'계산을 묻는 퇴직금 질문인데 되묻지 않음: {q}')
        continue
    ok('총급여' not in a and '납입액' not in a and '무엇을 계산할지' not in a, f'엉뚱한 되묻기: {q}\n{a}')
    ok('연차' in a and '30%' in a and '40%' in a and '50%' in a, f'감면율·연차가 없음: {q}\n{a}')
    ok('읽지 못했어요' not in a, f'숫자를 다 읽었는데 못 읽었다고 함: {q}')
u, a = ask('퇴직금 5천만 원 IRP에 넣으면 세금 얼마나 줄어요?')
ok(('5,000만 원', '퇴직금') in u and '원천징수영수증' in a, f'퇴직금 금액 표시·퇴직소득세 되묻기: {u}')
u, a = ask('퇴직소득세 1,000만 원이면 연금으로 받을 때 얼마나 아껴요?')
ok(('1,000만 원', '퇴직소득세') in u and '원천징수영수증' not in a, f'이미 말한 퇴직소득세를 또 물음: {a}')
# 감면 규칙 문장은 doc39 원문 그대로의 숫자
doc39 = open(os.path.join(ROOT, 'extracted', 'docs', 'doc39.txt'), encoding='utf-8').read()
ok('1~10년차에는 30% 감면, 11~ 20년차에는 40% 감면, 21년차부터는 50% 감면' in doc39 and
   '초과분에 대해서는 기존 퇴직소득세 그대로' in doc39, 'doc39 원문 감면율')
# 예시 질문은 그대로 넣으면 계산된다(되묻기 약속을 지킨다)
ex = re.search(r"예: '([^']+)'", ask('퇴직금 2억이면 퇴직소득세 얼마나 감면돼요?')[1]).group(1)
cx = taxcalc.compute(ex)
ok(cx and any(b.get('numbers') for b in cx['blocks']), f'퇴직금 예시 질문이 계산되지 않음: {ex}')

# ── 결함 20 — 규칙 블록이 있는 해지 확인 질문(같은 뜻 다른 표현 F5)은 블록 설명으로
for q in ('연금저축을 55세 전에 해지해서 2천만 원 받으면 세금 16.5% 일괄로 330만 원 내야 하나요?',
          '아직 55 안 됐는데 연금저축 2천만원 찾으면 세금 얼마예요? 16.5%면 330만원 맞죠?'):
    ok(unread(q) == [], f'해지 확인 질문 되묻기 아님: {q} → {unread(q)}')
c20 = taxcalc.compute('연금저축을 55세 전에 해지해서 2천만 원 받으면 세금 16.5% 일괄로 330만 원 내야 하나요?')
ok(c20 and any(b['topic'].startswith('55세 전·해지') for b in c20['blocks']), '해지 규칙 블록이 있다')

# ── 그대로 되물어야 하는 것 — 금액을 못 읽었거나 계산 조건이 빠진 짧은 계산 질문
u = orchestrator.unread_amounts('비교 좀 해주세요. 연금저축 600 IRP 300 하는 거랑 연금저축 900 하는 거요. 총급여 5000만원', None)
ok(any('단위' in r for _, r in u), f'단위 없는 금액 되묻기: {u}')
for q in ('1억 있는데 연금으로 받으면 세금 얼마예요?', '그럼 1,500 받으면 세금 총 얼마예요?', 'IRP 3천 빼면 세금?',
          '총급여 5000만원 직장인인데요 연금저축600 irp300 vs 연금저축900 비교 ㄱㄱ'):
    ok(unread(q) != [], f'계산 질문인데 되묻지 않음: {q}')
_, a = ask('1억 있는데 연금으로 받으면 세금 얼마예요?')
ok('짐작해서 답하지 않' in a and '만 나이' in a, f'연금 수령 되묻기: {a}')
a = orchestrator.unread_amount_answer([('5만 원', '어디에 쓰는 돈인지 확인 필요')], '월 5만원 연금저축 세액공제')
ok('짐작해서 답하지 않' in a and '총급여' in a and '읽지 못했어요' in a and not re.search(r'\d+만\s*원\s*×', a), '세액공제 되묻기 문장')
# 인출 + 퇴직금이면 퇴직소득세도 묻는다
a = orchestrator.unread_amount_answer([('3,000만 원', '인출')], '퇴직금 섞인 IRP 3천 빼면 세금?')
ok('퇴직소득세' in a and '꺼내려는 금액' in a, f'퇴직금 섞인 인출 되묻기: {a}')
for q in ('총급여 5,000만 원이고 연금저축 600 넣었어요. 12월에 IRP 더 넣으면 얼마나 돌려받아요?', '디폴트옵션은 뭐야?',
          '연금저축은 중간에 빼면 세금이 어떻게 돼요?', '67세인데 연금보험에서 월 100만원 받으면 세금은?'):
    ok(unread(q) == [], f'되묻기 아님: {q}')

# ── 2차 검토 회귀(105·106) — 자세한 시험은 test_fix1014_guard2.py
# 105: 퇴직금과 따로 넣는 내 돈(읽은 숫자 = 납입)은 세액공제 되묻기 — 퇴직소득세·연차가 아니다
for q in ('IRP에 퇴직금 말고 따로 300만원 넣으면 세금 얼마나 줄어요?', '퇴직금 받은 IRP에 내 돈 500만원 더 넣으면 세금 얼마나 줄어요?',
          '퇴직금 들어온 IRP에 개인 돈 900만원 추가 납입하면 세금 얼마 줄어요?'):
    u, a = ask(q)
    ok(u and '총급여' in a and '퇴직소득세' not in a and '연차' not in a, f'2차 105 세액공제 되묻기: {q}\n{a}')
# 106: 결론 확인('맞죠?')이어도 파서가 못 읽은 금액('하루 2천원')이 있으면 되묻는다 — 모델이 금액을 채우지 않게
u, a = ask('연금저축 하루 2천원씩 넣으면 세액공제 12만원 맞죠?')
ok(('12만 원', '어디에 쓰는 돈인지 확인 필요') in u and any('2천원' in t for t, _ in u) and '총급여' in a,
   f'2차 106 낯선 금액 되묻기: {u}')

# ── 3차 검토 회귀(206~210) — 자세한 시험은 test_fix1014_guard3.py
# 206~208: 받는 돈이 낯선 기간이어도 퇴직금·퇴직소득세 질문은 퇴직 갈래(결함 19가 다시 열리지 않게)
for q in ('퇴직금 1억 IRP에 넣고 분기마다 300만원씩 받으면 퇴직소득세 얼마나 줄어요?',
          '퇴직소득세 500만원 냈는데 하루 5만원씩 연금으로 받으면 얼마나 감면돼요?',
          '퇴직금을 매주 50만원씩 연금으로 받으면 퇴직소득세 얼마나 줄어요?'):
    u, a = ask(q)
    ok(u and '연차' in a and '30%' in a and '50%' in a and '총급여' not in a and '납입액' not in a and '1년에 받을 금액' not in a,
       f'3차 206~208 퇴직 갈래: {q}\n{a}')
ok('원천징수영수증' not in ask('퇴직소득세 500만원 냈는데 하루 5만원씩 연금으로 받으면 얼마나 감면돼요?')[1], '3차 207 퇴직소득세를 또 물음')
# 209: '2025 원천징수'의 '원'은 금액 단위가 아니다
for q, shown in (('2025 원천징수영수증에 퇴직소득세 300만원인데 연금으로 받으면 세금 얼마예요?', ('300만 원', '퇴직소득세')),
                 ('퇴직금 1억, 2024 원천징수영수증 기준으로 연금 받으면 세금 얼마예요?', ('1억 원', '퇴직금'))):
    u, a = ask(q)
    ok(u == [shown] and '연차' in a and '30%' in a and '1년에 받을 금액' not in a, f'3차 209: {q} → {u}\n{a}')
# 210: '주당 5만원'은 한 주(株) 값 — 기간이 아니다
ok(unread('연금저축으로 ETF 주당 5만원짜리 사면 세금 얼마예요?') == [('5만 원', '어디에 쓰는 돈인지 확인 필요')], '3차 210 주당')

print(f'{passed}/{total} 통과')
sys.exit(0 if passed == total else 1)
