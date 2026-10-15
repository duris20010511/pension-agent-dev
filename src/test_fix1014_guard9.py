# -*- coding: utf-8 -*-
"""
10-15 9차 결함 검토 — guard9 묶음(taxcalc._bare_amounts 연도 판정 · orchestrator.unread_amounts·_BARE_AFTER_ACCT) 회귀 시험. API 호출 없음.

    python src/test_fix1014_guard9.py

8차 수정 뒤 회귀(801~807): 8차가 기간 말(매달·월·연간·1년에·매년) 바로 뒤 숫자를 '늘 그 기간의 금액'으로 보아 연도 판정을 통째로
껐다. 그래서 HEAD 의 2020~2079(빈티지·연도) 판정까지 꺼져, 숫자가 TDF 앞에 오는 꼴('연금저축 매월 2040 TDF로 50만원')이 2,040만 원
월 납입으로 99만 원, 'IRP에 월 2050 TDF 40만원씩'이 148만 5천 원이 됐고(HEAD 8만 2,500원·6만 6천 원), 금액이 없는 'IRP 매달 2030
TDF에 넣으면'도 2,030만 원으로 계산했다. '연간 2024 납입증명서'는 한 해치 증명서일 수 있는데 2,024만 원으로 계산했다(807).
808: 기간 말 밖의 두 자리('24 납입증명서 보니')를 24만 원으로 계산 — 그물 _YEAR_CTX 의 두 자리 규칙과 달랐다.
고친 뒤: 해 꼴(4자리 19xx·20xx 뒤 연도 말, 또는 2020~2079 / 요즘 해를 줄인 두 자리 뒤 연도 말)은 자리와 상관없이 계산하지 않는다.
2020~2079 뒤에 TDF·빈티지·타겟데이트가 오면 상품 목표 연도라 되묻지도 않는다. 기간 말 뒤 해 꼴은 그물이 늘 단위를 되묻고,
그 밖은 서류에 찍힐 수 있는 해 + 연도 말만 해로 본다. answer.py 가 /chat 에서 부르는 조건과 같게 taxcalc.compute 결과를 calc 로 넘긴다.
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
THIS_YEAR = datetime.date.today().year
YY = (THIS_YEAR + 1) % 100          # 두 자리로 줄여 쓴 해의 끝(내년)


def ok(cond, msg):
    global passed, total
    total += 1
    if cond:
        passed += 1
    else:
        print('실패:', msg)


def calc(q):
    return taxcalc.compute(q) or {}


def unread(q):
    return orchestrator.unread_amounts(q, taxcalc.compute(q))


def spans(q):
    return [(s['v'], s['role'], s['period']) for s in taxcalc.parse(q)['spans']]


def figs(q):
    """숫자 있는 블록의 숫자 목록."""
    return [f for b in calc(q).get('blocks', []) if b.get('numbers') for f in (b.get('figures') or [])]


def text(q):
    return ' '.join(b.get('text', '') for b in calc(q).get('blocks', []))


def no_trace(q, n):
    """숫자 n 을 금액으로 읽은 흔적이 없다 — 읽은 금액·가정·블록 글·그물 결과 어디에도."""
    v = float(n)
    w = taxcalc.won(v)
    c = calc(q)
    return (all(x != v for x, _, _ in spans(q)) and not any(n in a for a in (c.get('assume') or []))
            and w not in text(q) and not any(t in (n, w) for t, _ in unread(q)))


# ── 결함 801·802 — 기간 말 + 빈티지 + TDF 뒤 금액(8차: 2,040만 원 월 납입으로 99만 원/79만 2천 원, 가정 '2040'을 2,040만 원으로 봄)
# 금액은 50만 원이다. 기간(매월)이 50만 원에 붙는지는 금액 쓰임 판정(_role)의 몫이라 두 값 모두 받는다(연 50만 원 / 월 50만 원 × 12)
q = '연금저축 매월 2040 TDF로 50만원 넣으면 세액공제 얼마예요?'
ok(no_trace(q, '2040'), f'802 빈티지를 금액으로 읽음: {spans(q)} / {calc(q).get("assume")}')
ok([v for v, r, _ in spans(q) if r == 'contrib'] == [50.0], f'802 금액: {spans(q)}')
ok(figs(q) in (['8만 2,500원', '6만 6천 원'], ['99만 원', '79만 2천 원']) and '50만 원' in text(q), f'802 계산: {figs(q)}')
ok(unread(q) == [], f'802 그물: {unread(q)}')

# ── 결함 803 — 'IRP에 월 2050 TDF 40만원씩'(8차: 148만 5천 원/118만 8천 원)
q = 'IRP에 월 2050 TDF 40만원씩 넣으면 세액공제는?'
ok(no_trace(q, '2050'), f'803 빈티지를 금액으로 읽음: {spans(q)}')
ok([v for v, r, _ in spans(q) if r == 'contrib'] == [40.0], f'803 금액: {spans(q)}')
ok(figs(q) in (['6만 6천 원', '5만 2,800원'], ['79만 2천 원', '63만 3,600원']), f'803 계산: {figs(q)}')

# ── 결함 804 — '매달 2045 빈티지로 20만원씩'(8차: 99만 원/79만 2천 원)
q = '연금저축에 매달 2045 빈티지로 20만원씩 넣으면 세액공제 얼마예요?'
ok(no_trace(q, '2045'), f'804 빈티지를 금액으로 읽음: {spans(q)}')
ok(figs(q) in (['3만 3천 원', '2만 6,400원'], ['39만 6천 원', '31만 6,800원']), f'804 계산: {figs(q)}')

# ── 결함 805 — 금액이 없는 빈티지 질문(8차: 2,030만 원 월 납입으로 99만 원·148만 5천 원). 숫자를 내지 않는다
for q in ('연금저축 매달 2030 TDF에 넣는데 세액공제 얼마예요?', 'IRP 매달 2030 TDF에 넣으면 세액공제 얼마예요?'):
    ok(figs(q) == [] and no_trace(q, '2030'), f'805 빈티지로 계산함: {q} → {figs(q)} / {unread(q)}')

# ── 결함 806 — 그물이 빈티지를 '2,040만 원'으로 보여 주던 것과, 금액은 맞는데 틀린 가정 줄이 붙던 것
for q, n in (('IRP 매년 2040 TDF 사는데 세액공제 얼마예요?', '2040'), ('연금저축으로 매년 2050 빈티지 TDF 사면 세액공제 얼마예요?', '2050')):
    ok(unread(q) == [] and no_trace(q, n), f'806 그물에 빈티지: {q} → {unread(q)}')
q = 'IRP로 매달 2030 TDF에 30만원씩 넣으면 세액공제 얼마예요?'
ok(no_trace(q, '2030') and not any('2030' in a for a in calc(q).get('assume') or []), f'806 빈티지 가정 줄: {calc(q).get("assume")}')
ok([v for v, r, _ in spans(q) if r == 'contrib'] == [30.0] and figs(q), f'806 금액 계산: {spans(q)} / {figs(q)}')

# ── 결함 807 — 기간 말 뒤 해 꼴 + 연도 말(8차: 2,024만 원 → 99만 원). 계산하지 않고 단위를 되묻는다
q = '연금저축 연간 2024 납입증명서 기준 세액공제 얼마예요?'
ok(figs(q) == [] and all(v != 2024.0 for v, _, _ in spans(q)), f'807 해 꼴로 계산함: {spans(q)} / {figs(q)}')
ok(unread(q) == [('2024', ASK)], f'807 단위를 되묻지 않음: {unread(q)}')
a = orchestrator.unread_amount_answer(unread(q), q)
ok('2,024만 원' not in a and '2024' in a, f'807 되묻기 답: {a}')

# ── 결함 808 — 기간 말 밖의 두 자리 + 연도 말(8차: 24만 원 → 3만 9,600원). 요즘 해를 줄인 꼴이면 해다(그물 _YEAR_CTX 와 같게)
q = '24 납입증명서 보니 연금저축 세액공제 얼마예요?'
ok(figs(q) == [] and no_trace(q, '24'), f'808 두 자리 해를 금액으로 계산: {spans(q)} / {figs(q)}')
ok(unread(q) == [], f'808 있을 수 있는 해를 되물음: {unread(q)}')

# ── 같은 원인의 다른 표현 — 기간 말 × 빈티지 × 상품 이름 × 계좌. 빈티지는 금액도 되물을 숫자도 아니다
PERIODS = ('매달', '매월', '월', '한 달에', '연간', '연', '1년에', '일년에', '매년')
VINTAGES = ('2025', '2030', '2035', '2040', '2045', '2050', '2055', '2060')
NAMES = ('TDF', 'TDF펀드', '빈티지', '타겟데이트 펀드', '타깃데이트', '형 TDF', 'tdf')
for p in PERIODS:
    for n in VINTAGES:
        for nm in NAMES:
            for tpl, amt in (('연금저축 {p} {n} {nm}로 30만원 넣으면 세액공제 얼마예요?', 30.0),
                             ('IRP에 {p} {n} {nm}에 50만원씩 넣으면 세액공제는?', 50.0),
                             ('{p} {n} {nm} 사는데 연금저축 세액공제 얼마예요?', None),
                             ('IRP {p} {n} {nm}에 넣으면 세액공제 얼마예요?', None)):
                q = tpl.format(p=p, n=n, nm=nm)
                ok(no_trace(q, n), f'빈티지를 금액으로 읽음/되물음: {q} → {spans(q)} / {unread(q)}')
                if amt:
                    ok([v for v, r, _ in spans(q) if r == 'contrib'] == [amt] and figs(q), f'빈티지 옆 금액을 놓침: {q} → {spans(q)}')
                else:
                    ok(figs(q) == [], f'금액 없는 빈티지 질문에 숫자: {q} → {figs(q)}')
# 기간 말 없이 계좌 이름 바로 뒤·문장 앞 빈티지 — 그물(_BARE_AFTER_ACCT)도 단위를 되묻지 않는다
for q, n in (('연금저축 2030 TDF에 넣으면 세액공제 얼마예요?', '2030'), ('IRP 2045 빈티지 사면 세액공제 얼마예요?', '2045'),
             ('연금저축에 2040 TDF로 50만원 넣으면 세액공제 얼마예요?', '2040'), ('2050 TDF에 매달 30만원씩 연금저축으로 넣으면 세액공제 얼마예요?', '2050'),
             ('연금저축 2040형 TDF 사면 세액공제 얼마예요?', '2040'), ('IRP 2035 타겟데이트 펀드 넣으면 세액공제 얼마예요?', '2035')):
    ok(no_trace(q, n), f'빈티지를 금액으로 읽음/되물음: {q} → {spans(q)} / {unread(q)}')
# 이름 뒤 빈티지('TDF 2040')는 전처럼 금액이 아니다
for q in ('매달 TDF 2040에 30만원씩 연금저축 넣으면 세액공제 얼마예요?', '연금저축 TDF2045 매년 넣으면 세액공제 얼마예요?'):
    ok(no_trace(q, re.search(r'20\d\d', q).group(0)), f'이름 뒤 빈티지: {q} → {spans(q)} / {unread(q)}')

# ── 기간 말 뒤 해 꼴(19xx·20xx + 연도 말, 2020~2079) — 계산하지 않고 그물이 단위를 한 번 되묻는다(있을 수 있는 해라도)
DOCS = ('납입증명서', '납입확인서', '납입 증명서', '연금소득 원천징수 영수증', '귀속')
for p in PERIODS:
    for n in ('1985', '1990', '2000', '2019', '2024', str(THIS_YEAR), '2050'):
        for d in DOCS:
            for tpl in ('연금저축 {p} {n} {d} 기준 세액공제 얼마예요?', '{p} {n} {d} 보면 IRP 세액공제 얼마예요?',
                        '총급여 5000만원인데 연금저축 {p} {n} {d} 보면 세액공제 얼마예요?'):
                q = tpl.format(p=p, n=n, d=d)
                ok(figs(q) == [] and all(v != float(n) for v, _, _ in spans(q)), f'기간 말 뒤 해 꼴로 계산: {q} → {spans(q)}')
                ok(unread(q).count((n, ASK)) == 1, f'기간 말 뒤 해 꼴을 되묻지 않음(또는 두 번): {q} → {unread(q)}')
    for n in ('2020', '2030', '2079'):
        for q in (f'연금저축 {p} {n} 넣으면 세액공제 얼마예요?', f'{p} {n} 받으면 세금 얼마예요?', f'IRP {p} {n} 정도 넣으면 공제 얼마예요?'):
            ok(figs(q) == [] and unread(q).count((n, ASK)) == 1, f'기간 말 뒤 2020~2079: {q} → {figs(q)} / {unread(q)}')

# ── 기간 말 밖의 해 꼴은 그대로 — 있을 수 있는 해 + 연도 말은 해로 보아 되묻지 않고, 없는 해는 되묻는다(8차 규칙)
for n in ('2019', '2024', str(THIS_YEAR)):
    for q in (f'{n} 납입증명서 보니 연금저축 세액공제 얼마예요?', f'연금저축 {n} 납입증명서 보면 세액공제 얼마예요?'):
        ok(figs(q) == [] and not any(t == n for t, _ in unread(q)), f'있는 해를 되물음: {q} → {unread(q)}')
for n in ('1985', '2050'):
    q = f'{n} 납입증명서 보니 연금저축 세액공제 얼마예요?'
    ok(figs(q) == [] and unread(q).count((n, ASK)) == 1, f'없는 해를 되묻지 않음: {q} → {unread(q)}')

# ── 두 자리 — 요즘 해(2010년~내년)를 줄인 꼴 + 연도 말만 해 꼴. 기간 말 밖이면 해로 보아 되묻지 않고, 기간 말 뒤면 되묻는다.
# 그런 해가 없는 두 자리(50·30·90 …)는 전처럼 금액이다(8차 709~711)
for nn in ('15', '19', '24', str(THIS_YEAR % 100), str(YY)):
    for d in ('납입증명서', '납입확인서', '원천징수영수증', '귀속'):
        q = f'{nn} {d} 보니 연금저축 세액공제 얼마예요?'
        ok(figs(q) == [] and no_trace(q, nn), f'두 자리 해를 금액으로: {q} → {spans(q)} / {unread(q)}')
        # 기간 말 바로 뒤 '15 원천징수영수증'은 기간 말 길(_BARE_STOP)이 '원'을 단위로 보아 숫자를 아예 읽지 않는다(숫자 없음 — 전과 같다)
        for p in (('연간', '매달', '1년에') if d != '원천징수영수증' else ()):
            q = f'연금저축 {p} {nn} {d} 기준 세액공제 얼마예요?'
            ok(figs(q) == [] and unread(q).count((nn, ASK)) == 1, f'기간 말 뒤 두 자리 해 꼴: {q} → {spans(q)} / {unread(q)}')
for nn in ('50', '30', '90', '60', str(YY + 1)):
    for q, per in ((f'연금저축 매달 {nn} 납입확인서 받았는데 세액공제 얼마예요?', 'monthly'),
                   (f'연금저축 연간 {nn} 납입증명서 기준 세액공제 얼마예요?', 'yearly'),
                   (f'{nn} 납입확인서 보니 연금저축 세액공제 얼마예요?', None)):
        ok((float(nn), 'contrib', per) in spans(q) and figs(q), f'두 자리 금액을 놓침: {q} → {spans(q)}')

# ── 그대로여야 하는 것 — 기간 말 뒤 해 꼴이 아닌 4자리·쉼표 숫자·단위 붙은 금액은 지금처럼 계산한다
for q, v, per in (('연금저축 매년 2000 넣으면 세액공제 얼마예요?', 2000.0, 'yearly'), ('연금저축 매년 1800 넣으면 세액공제 얼마예요?', 1800.0, 'yearly'),
                  ('연금저축 매달 2,040 넣으면 세액공제 얼마예요?', 2040.0, 'monthly'), ('IRP 연간 1500 넣으면 세액공제 얼마예요?', 1500.0, 'yearly'),
                  ('연금저축 매달 50 납입확인서 받았는데 세액공제 얼마예요?', 50.0, 'monthly'),
                  ('연금저축 매월 2040만원 넣으면 세액공제 얼마예요?', 2040.0, 'monthly')):
    ok((v, 'contrib', per) in spans(q) and figs(q), f'금액을 놓침: {q} → {spans(q)}')
q = '연 1800 받으면 세금 얼마예요?'
ok((1800.0, 'payout', 'yearly') in spans(q), f'연 1800 수령: {spans(q)}')

# ── 정규식 단위 — _bare_amounts 가 읽는 것, 해 꼴로 years 에 담는 것(한 번만), 빈티지로 아예 빼는 것
for t, out, yrs in (('매월 2040 TDF로 넣으면 공제', [], []), ('매달 2045 빈티지 넣으면 공제', [], []),
                    ('연간 2024 납입증명서 공제', [], ['2024']), ('매년 2000 납입증명서 공제', [], ['2000']),
                    ('매달 2030 넣으면 공제', [], ['2030']), ('매년 2000 넣으면 공제', [2000.0], []),
                    ('24 납입증명서 공제', [], ['24']), ('연간 24 납입증명서 공제', [], ['24']),
                    ('매달 50 납입확인서 공제', [50.0], []), ('50 납입확인서 공제', [50.0], []),
                    ('연금저축에 2030 넣으면 공제', [], ['2030']), ('2040 TDF에 넣으면 공제', [], [])):
    got_y = []
    got = [v for v, _, _ in taxcalc._bare_amounts(t, [], None, got_y)]
    ok(got == out and [r for r, _, _ in got_y] == yrs, f'단위 없는 금액: {t} → {got} / 해 꼴 {got_y}')
ok(orchestrator._BARE_AFTER_ACCT.findall('연금저축 2030 TDF에 넣으면') == [], '계좌 이름 뒤 빈티지를 그물이 잡음')
ok(orchestrator._BARE_AFTER_ACCT.findall('연금저축 2030 넣으면') == ['2030'], '계좌 이름 뒤 2030(빈티지 말 없음)을 그물이 놓침')

# ── 지금 맞게 도는 질문은 그대로 — 평가셋·같은 뜻 다른 표현·대화 시험 질문에 이번에 바뀐 꼴(기간 말 뒤 해 꼴, 두 자리 해 꼴,
# 숫자 앞 빈티지)이 없다. 고치기 전·후 compute·parse·unread 전체 비교는 손으로 했다(평가 질문 변화 0건)
ev = json.load(open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
qs = [it['question'] for it in ev]
for g in json.load(open(os.path.join(ROOT, 'eval', 'paraphrase_set.json'), encoding='utf-8')):
    qs += [g['canonical']] + [it['q'] for it in g['items']]
for d in json.load(open(os.path.join(ROOT, 'eval', 'dialogs.json'), encoding='utf-8'))['dialogues']:
    qs += [t['user'] for t in d['turns']] + [t['expected_standalone'] for t in d['turns'] if t.get('expected_standalone')]
qs = list(dict.fromkeys(qs))
touched = []
for q in qs:
    nq = taxcalc.norm_ws(taxcalc.normalize(q))
    for rx in (taxcalc._BARE_PERIOD, taxcalc._BARE_VERB, taxcalc._BARE_INCOME):
        for m in rx.finditer(nq):
            raw, e = m.group(1), m.end(1)
            if ',' in raw:
                continue
            if (rx is taxcalc._BARE_PERIOD and re.fullmatch(r'(?:19|20)\d\d', raw)
                    and (2020 <= int(raw) <= 2079 or taxcalc._YEAR_AFTER.match(nq, e))
                    or len(raw) == 2 and int(raw) <= YY and taxcalc._YEAR_AFTER.match(nq, e)
                    or re.fullmatch(r'20[2-7]\d', raw) and taxcalc._VINTAGE_AFTER.match(nq, e)):
                touched.append(q)
    if re.search(r'(?:연금저축|IRP|개인형)\s*' + orchestrator._VINTAGE_CTX, nq, re.I):
        touched.append(q)
ok(not touched, f'평가 질문 중 바뀐 판정에 걸리는 것: {touched}')
ok(not [it['id'] for it in ev if unread(it['question'])], '평가셋 100문항은 그물에 걸리지 않는다')

print(f'{passed}/{total} 통과')
sys.exit(0 if passed == total else 1)
