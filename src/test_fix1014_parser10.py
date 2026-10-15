# -*- coding: utf-8 -*-
"""
10-15 10차 검토 — parser10 묶음(taxcalc) 회귀 시험. API 호출 없음.

    PYTHONIOENCODING=utf-8 python src/test_fix1014_parser10.py

1000 9차 _clause_kind 가 어미(려고·려는·고 싶)만 보고 동사를 안 봐 '연금저축은 해지하려는데 IRP도 있어서 800만 원 넣으면'의
     연금저축(닫을 계좌)을 넣는 곳으로 읽어 99만 원(HEAD·9차 전 IRP 132만 원). 넣겠다는 말은 넣는 동사(넣·납입·입금·붓·채우·
     적립·모으)에 붙은 어미일 때만, 해지·깨·빼·옮기·이전·갈아타·정리·인출·없애는 닫는 말.
1001 같은 원인 반대 방향 — 'IRP는 해지하려는데 연금저축도 있어서'가 IRP 132만 원(맞는 값 연금저축 99만 원).
1002 '연금저축에 넣었는데 잘 모르겠고 IRP도 있는데'의 '겠'(동사 '모르')을 넣겠다는 말로 읽어 연금저축 99만 원(맞는 값 IRP 132만 원).
1003 _CANCEL_AGE 가 '65세에 받기로 한·60세에 개시하려던'(받는·개시 나이)을 해지 나이로 읽어 고객 나이(50·45세)를 누르고
     '55세 이후 해지' 블록 — 해지 나이는 해지 말에 묶인 나이('65세에 해지')·해지보다 앞선 일의 나이('62세에 개시한')만.
1004 DB 재직 중 인출 블록이 퇴직 뒤 받는 질문('55세 전에 퇴직하면 퇴직금은 어떻게 받나요', '퇴직 후 DB 퇴직금을 일시금으로')에도
     붙었다 — 해지·중도인출·인출을 묻는 재직 중 질문에만.
재현 입력마다 기대 결과를, 지금 맞게 도는 이웃 질문(9차 800·900·901 시험 꼴)은 그대로인지 함께 본다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import taxcalc  # noqa: E402
import orchestrator  # noqa: E402  (되묻기 판단 확인용 — 모델 호출 없음)

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


def parse(q):
    return taxcalc.parse(q)


def accts(q):
    return [(c['acct'], c['v']) for c in parse(q)['contrib']]


def decide(q):
    return orchestrator.decide(q, calc=calc(q))[0]


T = ' 세액공제? 총급여 5천'
EARLY_T = '55세 전·해지 인출 세금'
LATE_T = '55세 이후 해지·인출 세금(규칙)'
DB_T = '퇴직연금 중도인출·해지(규칙)'
WANT = {'IRP': ('irp', '132만 원'), '연금저축': ('savings', '99만 원')}


def fix(s):
    return s.replace('연금저축는', '연금저축은').replace('연금저축가', '연금저축이').replace('연금저축로', '연금저축으로') \
        .replace('연금저축를', '연금저축을')


# ── 결함 1000·1001: 닫는 동사가 붙은 앞 계좌는 넣는 곳이 아니다 — 다른 계좌(HEAD·9차 전과 같은 값)
q = '연금저축은 해지하려는데 IRP도 있어서 800만 원 넣으면' + T
ok(accts(q) == [('irp', 800)] and key(q) == '132만 원' and '납입: IRP 800만 원' in body(q), f'1000 재현: {accts(q)} {key(q)}')
q = 'IRP는 해지하려는데 연금저축도 있어서 800만 원 넣으면' + T
ok(accts(q) == [('savings', 800)] and key(q) == '99만 원' and '납입: 연금저축 800만 원' in body(q), f'1001 재현: {accts(q)} {key(q)}')
SHUT = ['해지하려는데', '깨려고 하는데', '빼려고 하는데', '옮기려고 하는데', '이전하려고 하는데', '갈아타려고 하는데', '해지하고 싶은데',
        '정리하려는데', '없애려고 하는데', '해지할 건데', '인출하려고 하는데', '깰 건데', '해지할까 하는데', '해지하기로 했는데',
        '해지해야 하는데', '깨야 하는데', '정리할 생각인데', '해약하려는데', '해지 신청하려는데', '해지했는데', '깼는데', '옮겼는데']
HAVE = ['{B}도 있어서', '{B}도 있고', '{B}도 있는데', '{B}도 가입돼 있어서', '{B} 계좌도 있는데', '{B}는 있는데', '{B}도 갖고 있는데']
n_shut = 0
for A, B in (('연금저축', 'IRP'), ('IRP', '연금저축')):
    acct, want = WANT[B]
    for v in SHUT:
        for h in HAVE:
            q = fix(f'{A}는 {v} {h.format(B=B)} 800만 원 넣으면') + T
            n_shut += 1
            ok(accts(q) == [(acct, 800)] and key(q) == want and not parse(q)['acct_unsure'], f'1000·1001 닫는 말: {q} → {accts(q)} {key(q)}')
    # 다른 꼴 — 계좌 + 토씨·꾸밈·연결 어미가 달라도
    for v in (f'{A} 계좌는 해지하려는데', fix(f'{A}를 해지하려는데'), fix(f'기존 {A}는 정리하고 싶은데'), f'{A}에서 돈을 빼려고 하는데',
              f'{A} 해지하고', fix(f'{A}는 곧 해지할 거라서'), fix(f'{A}는 다른 증권사로 옮기려는데'), f'{A}에 넣으려다 말고'):
        q = f'{v} {B}도 있어서 800만 원 넣으면' + T
        n_shut += 1
        ok(accts(q) == [(acct, 800)] and key(q) == want, f'1000·1001 닫는 말 다른 꼴: {q} → {accts(q)} {key(q)}')
    # 있다고 한 계좌가 앞에, 닫는 계좌가 뒤에 와도 — HEAD 부터 닫을 계좌로 계산하던 꼴
    top = '은' if A == '연금저축' else '는'
    for v in ('해지하려고 해서', '깨려고 하는데', '해지할 거라', '옮길 거라서'):
        for q in (f'{B}도 있는데 {A}{top} {v} 800만 원 넣으면' + T, f'{B}에 넣으려는데 {A}{top} {v} 800만 원 넣으면' + T):
            n_shut += 1
            ok(accts(q) == [(acct, 800)] and key(q) == want, f'1000·1001 닫는 계좌가 뒤: {q} → {accts(q)} {key(q)}')
    # 없는 계좌를 새로 만들면 그 계좌(9차 그대로)
    q = fix(f'{A}는 해지하려는데 {B}가 없어서 새로 만들어 800만 원 넣으면') + T
    ok(accts(q) == [(acct, 800)], f'1000 닫는 말 + 새 계좌: {q} → {accts(q)}')
# 돈이 옮겨 가는 계좌('연금저축으로 옮기려고')·부정된 닫는 말('해지하지 않고·해지 안 하고')은 닫힌 계좌가 아니다 — 모르면 되묻기
for q in ('연금저축으로 옮기려고 하는데 IRP도 있어서 800만 원 넣으면' + T, '연금저축은 해지하지 않고 IRP도 있어서 800만 원 넣으면' + T,
          '연금저축은 해지 안 하고 IRP도 있는데 800만 원 넣으면' + T, 'IRP는 깨지 않으려고 하는데 연금저축도 있어서 800만 원 넣으면' + T):
    ok(not accts(q) and parse(q)['acct_unsure'] == [800] and not figs(q) and decide(q) == '되묻기',
       f'1000 옮겨 받는 계좌·부정된 닫는 말: {q} → {accts(q)} {figs(q)}')
# 다른 넣는 곳 후보가 없으면 지금처럼 그 계좌('연금저축 깨서 800만 원 넣으면' — 넣는 곳 기본값 그대로)
for q, want in (('연금저축 깨서 800만 원 넣으면' + T, [('savings', 800)]), ('IRP 해지하고 연금저축에 800만 원 넣으면' + T, [('savings', 800)]),
                ('IRP에서 빼서 연금저축에 800만 원 넣으면' + T, [('savings', 800)])):
    ok(accts(q) == want, f'1000 이웃 그대로: {q} → {accts(q)}')
# '이전에'(때)·'빼고'(덜어 냄)는 닫는 말이 아니다
ok(taxcalc._shut_kind('은 이전에 가입했는데 ') is None and taxcalc._shut_kind('은 회사 돈 빼고 ') is None
   and taxcalc._shut_kind('은 해지하려는데 ') == 'shut' and taxcalc._shut_kind('으로 옮기려고 하는데 ') == 'move_in'
   and taxcalc._shut_kind('은 해지하지 않고 ') == 'kept' and taxcalc._shut_kind('은 안 깨고 ') == 'kept', '_shut_kind 판정')

# ── 결함 1002: '모르겠고·알고 싶은데·확인하려고'의 어미는 넣는 말에 붙지 않았다 — 넣은 마디는 닫힘(다른 계좌)
q = '연금저축에 넣었는데 잘 모르겠고 IRP도 있는데 800만 원 더 넣으면' + T
ok(accts(q) == [('irp', 800)] and key(q) == '132만 원', f'1002 재현: {accts(q)} {key(q)}')
for A, B in (('연금저축', 'IRP'), ('IRP', '연금저축')):
    acct, _ = WANT[B]
    for v in ('넣었는데 잘 모르겠고', '넣었는데 세액공제 될지 모르겠고', '넣었는데 어떻게 할지 모르겠고', '넣었는데 한도를 알고 싶은데',
              '넣었는데 얼마인지 알려고 하는데', '이미 넣었고 잘 모르겠지만', '납입했는데 잘 모르겠고', '넣었는데 확인하려고 하는데',
              '넣긴 했는데 어떻게 될지 모르겠고'):
        q = fix(f'{A}에 {v} {B}도 있는데 300만 원 더 넣으면') + T
        ok(accts(q) == [(acct, 300)], f'1002 넣는 말 아닌 어미: {q} → {accts(q)}')
# 넣는 말이 없는 '모르겠는데'는 넣겠다는 말도 이미 한 일도 아니다 — 되묻기(숫자 없음)
q = '연금저축은 잘 모르겠는데 IRP도 있어서 800만 원 넣으면' + T
ok(not accts(q) and parse(q)['acct_unsure'] == [800] and not figs(q), f"1002 '모르겠는데'만: {accts(q)} {figs(q)}")
ok(taxcalc._put_intent('에 넣었는데 잘 모르겠고 ') is None and taxcalc._put_intent('은 해지하려는데 ') is None
   and taxcalc._put_intent('에 넣으려고 하는데 ') == 'intent' and taxcalc._put_intent('에 채워야 하는데 ') == 'intent'
   and taxcalc._put_intent('에 넣어 보려고 하는데 ') == 'intent' and taxcalc._put_intent('에 납입할 계획인데 ') == 'intent'
   and taxcalc._put_intent('를 새로 만들려고 하는데 ') == 'intent' and taxcalc._put_intent('에 넣으려고 했는데 ') == 'past'
   and taxcalc._put_intent('에는 안 넣으려고 하는데 ') == 'neg' and taxcalc._put_intent('에는 넣지 않으려고 하는데 ') == 'neg'
   and taxcalc._put_intent('에 넣으려다 말고 ') == 'neg', '_put_intent 판정')

# 9차 800 그대로 — 넣겠다는 마디(넣는 동사 + 어미)는 그 계좌, 계좌를 새로 만들겠다는 마디도 그 계좌
INTENT = ['넣으려고 하는데', '넣으려는데', '넣고 싶은데', '납입하려고 하는데', '넣을까 하는데', '몰아서 넣으려는데', '넣을 건데',
          '넣을 생각인데', '넣으려 하는데', '넣어야 하는데', '넣기로 했는데', '넣고자 하는데', '넣을 예정인데', '채우려고 하는데',
          '채워야 하는데', '입금하려고 하는데', '부으려고 하는데', '모으려고 하는데', '적립하려는데', '넣어 보려고 하는데', '더 넣을 것 같은데']
for A, B in (('IRP', '연금저축'), ('연금저축', 'IRP')):
    acct, want = WANT[A]
    for v in INTENT:
        q = fix(f'{A}에 {v} {B}도 있어서 800만 원 넣으면') + T
        ok(accts(q) == [(acct, 800)] and key(q) == want, f'800 넣겠다는 계좌(그대로): {q} → {accts(q)} {key(q)}')
    for v in (fix(f'{A}를 새로 만들려고 하는데'), f'{A} 계좌를 개설하려고 하는데', f'{A}에 가입하려는데'):
        q = f'{v} {B}도 있어서 800만 원 넣으면' + T
        ok(accts(q) == [(acct, 800)], f'800 새로 만들 계좌(그대로): {q} → {accts(q)}')
    for v in ('안 넣으려고 하는데', '넣지 않으려고 하는데', '못 넣을 것 같은데'):
        q = fix(f'{A}에는 {v} {B}도 있어서 800만 원 넣으면') + T
        ok(accts(q) == [(WANT[B][0], 800)], f'800 넣지 않겠다는 계좌: {q} → {accts(q)}')

# ── 결함 1003: 받는·개시 나이는 해지 나이가 아니다 — 고객 나이(55세 전)로 55세 전·해지 블록
for q, k in (('50세인데 65세에 받기로 한 연금저축 2000만원을 지금 해지하면 세금은?', '330만 원'),
             ('50세인데 60세에 연금 받으려던 IRP 2000만원 지금 해지하면', '330만 원'),
             ('지금 45세고 60세에 개시하려던 연금저축 1000만원 깨면', '165만 원'),
             ('50세인데 65세부터 받을 IRP 2000만원을 해지하면 세금은?', '330만 원'),
             ('45살인데 60세 되면 받으려던 연금저축 1000만원 중도해지하면 세금은?', '165만 원'),
             ('나이 50인데 65세에 수령하려던 연금저축 2000만원을 해지하면 세금은?', '330만 원'),
             ('나이 50인데 70세에 타려던 IRP 2000만원 깨면 세금은?', '330만 원'),
             ('저는 48살이고 60살에 받으려고 했던 IRP 1500만 원 지금 깨면 세금은?', '247만 5천 원'),
             ('52세인데 65세에 연금 받을 예정인 연금저축 2000만 원 해지하면 세금은?', '330만 원'),
             ('45세인데 연금 개시를 60세에 하려던 연금저축 1000만원 해지하면 세금은?', '165만 원'),
             ('50세인데 65세에 해지하려던 연금저축 2000만원 지금 해지하면 세금은?', '330만 원')):
    t = body(q)
    ok(topics(q) == [EARLY_T] and key(q) == k and '나이 6' not in t and '나이 7' not in t and LATE_T not in topics(q),
       f'1003 받는·개시 나이: {q} → {topics(q)} {key(q)} cancel_age={parse(q)["cancel_age"]}')
ok(parse('50세인데 65세에 받기로 한 연금저축 2000만원을 지금 해지하면 세금은?')['cancel_age'] is None
   and parse('지금 45세고 60세에 개시하려던 연금저축 1000만원 깨면')['age'] == 45, '1003 해지 나이 없음·고객 나이 45')
# 9차 901 그대로 — 해지 말에 묶인 나이, 해지보다 앞선 일(개시한·받기 시작한·퇴직하고)의 55세 이상 나이는 55세 이후 해지
for q in ('65세에 연금저축 해지하면 16.5% 다 떼이나요?', '70세에 IRP 중도인출 1,500만 원 넘게 하면 종합과세돼요?',
          '65세에 연금저축 2000만 원 해지하면 세금은?', '60살 되면 연금저축 해지하려고요 세금은?', '60세 넘어서 IRP 해지하면 세금 얼마나 떼요?',
          '62세에 개시한 연금저축 2000만원 해지하면 세금은?', '60세에 연금 받기 시작한 IRP 2000만원 해지하면 세금은?',
          '56세에 가입한 연금저축 2000만 원 해지하면 세금은?', '60세에 퇴직하고 IRP 2000만 원 해지하면 세금은?',
          '65세에 연금 받기 시작해서 지금 해지하면 세금은?', '65세에 연금 개시 전에 연금저축 2000만 원 해지하면 세금은?',
          '60세인데 연금저축 2000만 원 해지하면 세금 얼마예요?'):
    ok(topics(q) == [LATE_T] and not figs(q), f'901 55세 이후 해지(그대로): {q} → {topics(q)} {figs(q)}')
for q, k in (('50세에 가입한 연금저축 2000만 원 해지하면 세금은?', '330만 원'), ('50세인데 연금저축 2000만 원 해지하면 세금은?', '330만 원'),
             ('60세인데 55세 전에 연금저축 2000만 원 해지했으면 세금은?', '330만 원')):
    ok(topics(q) == [EARLY_T] and key(q) == k, f'901 55세 전(그대로): {q} → {topics(q)} {key(q)}')

# ── 결함 1004: 퇴직 뒤 받는 질문 — DB 재직 중 인출 블록(그리고 연금계좌 해지 블록)을 붙이지 않는다
for q in ('DB형 회사 다니는데 55세 전에 퇴직하면 퇴직금은 어떻게 받나요?', '퇴직 후 DB 퇴직금 5000만원을 일시금으로 찾으면 세금은?',
          'DB형인데 퇴직하면 퇴직금 일시금으로 받으면 세금은?', 'DB형 회사에서 퇴직할 때 퇴직금 3000만원 일시금으로 받으면 세금?',
          'DB 퇴직연금인데 은퇴 후 퇴직금 찾으면 세금은?', '확정급여형인데 퇴사하면 퇴직금 어떻게 받아요?',
          '퇴직한 뒤 DB 퇴직금 3000만원 한 번에 찾으면 세금 얼마?', 'DB형 퇴직연금 퇴직할 때 해지하면 세금?',
          'DB형 다니다 퇴직하고 퇴직금 5000만원 찾으면 세금은?', '은퇴 후 DB 퇴직금 일시금 수령 세금은?',
          'DB 퇴직금 5000만원 일시금으로 찾으면 세금은?'):
    t = body(q)
    ok(DB_T not in topics(q) and '재직 중 DB를 해지' not in t and 'DB(확정급여형) 해지·인출' not in t
       and not any(x.startswith(('55세 전·해지', '재원별 인출')) for x in topics(q)), f'1004 퇴직 뒤 받는 질문: {q} → {topics(q)}')
# 9차 900 그대로 — 재직 중 해지·중도인출 질문은 DB 블록
for q in ('DB형 퇴직연금 3000만 원 해지하면 세금 얼마예요?', 'DB형 퇴직연금 주택 구입으로 1000만 원 중도인출하면 세금은?',
          '45세인데 DB형 퇴직연금 2000만 원 해지하면 세금은?', '퇴직연금 DB 깨면 세금 얼마나 떼요?', 'DB형인데 중도인출 돼요?',
          'DB에서 DC로 바꾸면 중도인출 되나요?', 'DB형 퇴직연금 2000만 원 55세 전에 빼면 세금은?', 'DB형인데 퇴직 전에 중도인출 되나요?',
          'DB형인데 퇴직하기 전에 해지할 수 있어요?', 'DB형 퇴직연금 3000만 원 퇴직 전에 깨면 세금은?', 'DB형 다니면서 해지하면 세금?',
          'DB 3000만 원 해지하면 세금 얼마예요?', 'DB형 퇴직연금 3000만 원 일시금으로 해지하면 세금은?'):
    ok(topics(q) == [DB_T] and 'DB(확정급여형)는 중도인출이 허용되지 않는다' in body(q) and not figs(q), f'900 DB(그대로): {q} → {topics(q)}')
ok(topics('DB형 퇴직연금 담보대출 되나요?') == ['퇴직연금 중도인출·담보(규칙)'], '900 DB 담보(그대로)')
ok(taxcalc._after_retire('퇴직 후 DB 퇴직금') and taxcalc._after_retire('55세 전에 퇴직하면')
   and not taxcalc._after_retire('퇴직 전에 중도인출') and not taxcalc._after_retire('DB형 퇴직연금 해지'), '_after_retire 판정')

print(f'{passed}/{total} 통과 (닫는 말 꼴 {n_shut}개 포함)')
sys.exit(0 if passed == total else 1)
