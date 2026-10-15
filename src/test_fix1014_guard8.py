# -*- coding: utf-8 -*-
"""
10-14 8차 결함 검토 — guard8 묶음(taxcalc._bare_amounts 연도 판정 · orchestrator.unread_amounts) 회귀 시험. API 호출 없음.

    python src/test_fix1014_guard8.py

7차 수정 뒤 회귀(709~712): 7차 연도 판정이 기간 말(매달·월·연간·1년에·매년) 바로 뒤 숫자와 두 자리 숫자까지 연도 말
(납입확인서·납입증명서) 앞이면 연도로 보아 지웠다 — '매달 50 납입확인서', '연간 90 납입증명서', '매년 2000 납입증명서'의 금액이
사라져 계산 블록도 되묻기도 없이 생성으로 갔다(HEAD 에서는 계산했다).
713: '연금저축에 1900 납입증명서'·'1985 납입증명서 보니 연금저축'은 계산기가 연도로 보아 읽지 않았는데, 그물은 계좌 이름 바로 뒤
숫자만 보아 있을 수 없는 해인데도 되묻지 않았다.
고친 뒤: 연도는 4자리 19xx·20xx 만이고, 기간 말 바로 뒤 숫자는 그 기간의 금액이다. 계산기가 연도·빈티지일 수 있어 뺀 숫자는
_bare_amounts(years=…)로 그물에 넘기고, 그물은 서류에 찍힐 수 있는 해(1990년~내년)에 연도 말이 붙은 것만 연도로 보고 나머지는
자리와 상관없이 단위를 되묻는다. answer.py 가 /chat 에서 부르는 조건과 같게 taxcalc.compute 결과를 calc 로 넘긴다.
9차(검토 801~808, test_fix1014_guard9.py): '기간 말 뒤 숫자는 늘 금액'이 틀렸다('매월 2040 TDF로 50만원', '연간 2024 납입증명서') —
기간 말 뒤 4자리 해 꼴은 계산하지 않고 단위를 되묻게 바뀌어 712 와 같은 원인 꼴 중 4자리 기대를 옮겼다(두 자리 50·30·90 은 그대로 금액).
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


def blk(q):
    """세액공제 블록 (숫자 있음, 핵심 숫자, 숫자 목록)."""
    for b in calc(q).get('blocks', []):
        if b.get('topic') == '세액공제':
            return bool(b.get('numbers')), b.get('key_figure'), b.get('figures') or []
    return None


# ── 결함 709 — 기간 말 + 두 자리 + 납입확인서(고치기 전 spans []·blocks []·unread [], 계산도 되묻기도 없음)
q = '연금저축 매달 50 납입확인서 받았는데 세액공제 얼마예요?'
ok(spans(q) == [(50.0, 'contrib', 'monthly')], f'709 읽은 금액: {spans(q)}')
b = blk(q)
ok(b and b[0] and '99만 원' in b[2], f'709 계산 블록: {b}')
ok("'50'을 50만 원으로 봄" in (calc(q).get('assume') or []), f'709 가정: {calc(q).get("assume")}')
ok(unread(q) == [], f'709 그물: {unread(q)}')

# ── 결함 710 — 총급여가 있을 때(고치기 전 숫자 없는 블록에 그물이 비어 생성으로 감)
q = '총급여 5000만원인데 연금저축 매달 50 납입확인서 받았는데 세액공제 얼마예요?'
ok(spans(q) == [(5000.0, 'income', None), (50.0, 'contrib', 'monthly')], f'710 읽은 금액: {spans(q)}')
b = blk(q)
ok(b and b[0] and b[1] == '99만 원', f'710 핵심 숫자: {b}')
ok(unread(q) == [], f'710 그물: {unread(q)}')

# ── 결함 711 — 월·연간·매달·1년에 + 두 자리 + 서류 이름(고치기 전 모두 blocks [])
for q, sp, fig in (('연금저축 월 50 납입확인서 보면 세액공제 얼마예요?', (50.0, 'contrib', 'monthly'), '99만 원'),
                   ('IRP 연간 90 납입증명서 기준으로 세액공제 얼마예요?', (90.0, 'contrib', 'yearly'), '14만 8,500원'),
                   ('매달 30 납입증명서 나오는데 연금저축 세액공제 얼마예요?', (30.0, 'contrib', 'monthly'), '59만 4천 원'),
                   ('연금저축 1년에 50 납입증명서 기준 세액공제 얼마예요?', (50.0, 'contrib', 'yearly'), '8만 2,500원')):
    b = blk(q)
    ok(spans(q) == [sp], f'711 읽은 금액: {q} → {spans(q)}')
    ok(b and b[0] and fig in b[2], f'711 계산: {q} → {b}')
    ok(unread(q) == [], f'711 그물: {q} → {unread(q)}')

# ── 결함 712 — 매년 + 4자리(연도 꼴) + 납입증명서(고치기 전 blocks [], 계산도 되묻기도 없이 생성으로 감)
# 8차는 '기간 말 뒤 4자리도 금액'으로 보아 2,000만 원으로 계산했다. 9차 검토 807('연간 2024 납입증명서'는 한 해치 증명서일 수 있다)이
# 그 전제를 뒤집어, 기간 말 뒤 해 꼴 + 연도 말은 계산하지 않고 그물이 단위를 되묻는다 — 712 의 결함(그물이 비어 생성으로 감)은 되묻기로 막는다
q = '연금저축 매년 2000 납입증명서 받았는데 세액공제 얼마예요?'
b = blk(q)
ok(spans(q) == [], f'712 해 꼴을 금액으로 읽음: {spans(q)}')
ok(not (b and b[0]), f'712 해 꼴로 계산함: {b}')
ok(unread(q) == [('2000', ASK)], f'712 단위를 되묻지 않음: {unread(q)}')

# ── 결함 713 — 계좌 이름 바로 뒤가 아닌 자리의 있을 수 없는 해(고치기 전 unread [] — 되묻지 않음)
for q, y in (('연금저축에 1900 납입증명서 받았는데 세액공제 얼마예요?', '1900'),
             ('1985 납입증명서 보니 연금저축 세액공제 얼마예요?', '1985')):
    u = unread(q)
    ok((y, ASK) in u, f'713 되묻지 않음: {q} → {u}')
    ok(not any(b.get('numbers') for b in calc(q).get('blocks', [])), f'713 연도 꼴로 계산함: {q}')
    ok(all(v != float(y) for v, _, _ in spans(q)), f'713 금액으로 읽음: {q} → {spans(q)}')
    a = orchestrator.unread_amount_answer(u, q)
    ok(taxcalc.won(float(y)) not in a, f'713 되묻기 답에 금액으로 읽은 흔적: {a}')
# 계좌 이름 바로 뒤('연금저축 1985 …')는 전처럼 되묻는다 — 같은 숫자를 두 번 묻지 않는다
q = '연금저축 1985 납입증명서 보니 세액공제 얼마예요?'
ok(unread(q) == [('1985', ASK)], f'713 계좌 이름 뒤: {unread(q)}')

# ── 같은 원인의 다른 표현 — 기간 말 × 숫자(두 자리·4자리 연도 꼴) × 서류 이름. 해가 될 수 없는 두 자리(50·30·90)는 그 기간의 금액이다.
# 4자리 해 꼴(2000·1985·2019·2030)은 9차부터 계산하지 않고 단위를 되묻는다(9차 검토 801~807 — 8차는 이것도 금액으로 읽었다)
PERIODS = (('매달', 'monthly'), ('매월', 'monthly'), ('월', 'monthly'), ('한 달에', 'monthly'),
           ('연간', 'yearly'), ('연', 'yearly'), ('1년에', 'yearly'), ('일년에', 'yearly'), ('매년', 'yearly'))
for pw, per in PERIODS:
    for n in ('50', '30', '90', '2000', '1985', '2019', '2030'):
        for doc in ('납입확인서', '납입증명서', '납입 증명서'):
            for tpl in ('연금저축 {p} {n} {d} 받았는데 세액공제 얼마예요?', '{p} {n} {d} 나오는데 IRP 세액공제 얼마예요?',
                        '총급여 5000만원인데 연금저축 {p} {n} {d} 보면 세액공제 얼마예요?'):
                q = tpl.format(p=pw, n=n, d=doc)
                sp = [x for x in spans(q) if x[1] == 'contrib']
                b = blk(q)
                if len(n) == 2:
                    ok(sp == [(float(n), 'contrib', per)], f'기간 말 뒤 금액을 놓침: {q} → {spans(q)}')
                    ok(b and b[0], f'기간 말 뒤 금액 계산이 없음: {q} → {b}')
                    ok(not any(t == n for t, _ in unread(q)), f'읽은 금액을 되물음: {q} → {unread(q)}')
                else:
                    ok(sp == [], f'기간 말 뒤 해 꼴을 금액으로 읽음: {q} → {spans(q)}')
                    ok(not (b and b[0]), f'기간 말 뒤 해 꼴로 계산함: {q} → {b}')
                    ok(unread(q).count((n, ASK)) == 1, f'기간 말 뒤 해 꼴을 되묻지 않음(또는 두 번): {q} → {unread(q)}')

# ── 계좌 이름 뒤가 아닌 자리의 연도 꼴 — 계산기가 뺀 숫자는 서류에 찍힐 수 있는 해(1990년~내년) + 연도 말만 연도로 보고 되묻지 않는다.
# 그 밖(1990년 전·내년 뒤)은 자리와 상관없이 단위를 되묻는다
POS_TPLS = ('연금저축에 {} 납입증명서 받았는데 세액공제 얼마예요?', 'IRP에 {} 납입확인서 받았는데 세액공제 얼마예요?',
            '{} 납입증명서 보니 연금저축 세액공제 얼마예요?', '{} 납입 확인서 보면 IRP 세액공제 얼마예요?',
            '연금저축 {} 납입증명서 보니 세액공제 얼마예요?')
for y in ('1900', '1950', '1985', '1989', '1990', '1999', '2005', '2019', str(THIS_YEAR), str(THIS_YEAR + 1),
          str(THIS_YEAR + 2), '2050', '2079', '2080', '2099'):
    plausible = 1990 <= int(y) <= THIS_YEAR + 1
    for tpl in POS_TPLS:
        q = tpl.format(y)
        u = unread(q)
        ok(all(v != float(y) for v, _, _ in spans(q)), f'연도 꼴을 금액으로 읽음: {q} → {spans(q)}')
        if plausible:
            ok(not any(t == y for t, _ in u), f'있는 해를 되물음: {q} → {u}')
        else:
            ok(u.count((y, ASK)) == 1, f'있을 수 없는 해를 되묻지 않음(또는 두 번): {q} → {u}')
# 연도 말이 없는 빈티지 꼴(2020~2079)도 계좌 이름 바로 뒤가 아니어도 되묻는다('연금저축 2030 넣으면'과 같게)
for q, y in (('연금저축에 2030 넣으면 세액공제 얼마예요?', '2030'), ('연금저축 2030 넣으면 세액공제 얼마예요?', '2030'),
             ('IRP에 2045 납입하면 세액공제 얼마예요?', '2045')):
    ok(unread(q).count((y, ASK)) == 1, f'빈티지 꼴을 되묻지 않음: {q} → {unread(q)}')
# 총급여 자리의 있을 수 없는 해 + 서류 이름 — 소득으로 읽지 않고 되묻는다
q = '총급여 1985 근로소득 원천징수영수증 보면 세액공제 얼마예요?'
ok(spans(q) == [] and ('1985', ASK) in unread(q), f'총급여 자리의 연도 꼴: {spans(q)} / {unread(q)}')

# ── 그대로여야 하는 것 — '년'이 붙은 해·상품 이름의 숫자는 되묻지 않고, 서류 앞 실제 금액·연도 뒤 금액은 그대로 계산한다
for q in ('2030년부터 연금 받으면 세금 얼마예요?', '연금저축 2030년 세액공제 얼마예요?', 'TDF 2045 넣으면 세액공제 얼마예요?',
          'TDF2040에 600만원 넣으면 세액공제 얼마예요?', '연금저축 2024 납입증명서 보면 세액공제 얼마예요?',
          'IRP 2025 원천징수영수증에 퇴직소득세 300만원인데 연금으로 받으면 세금 얼마예요?'):
    ok(not any(r == ASK for _, r in unread(q)), f'연도·상품 숫자를 되물음: {q} → {unread(q)}')
for q, v in (('연금저축에 50 납입확인서 받았는데 세액공제 얼마예요?', 50.0), ('연금저축 600 납입증명서 보면 세액공제 얼마예요?', 600.0),
             ('연금저축에 1900 넣었는데 세액공제 얼마예요?', 1900.0), ('연금저축에 2,019 납입증명서 받았는데 세액공제 얼마예요?', 2019.0)):
    ok((v, 'contrib') in [(x, r) for x, r, _ in spans(q)], f'금액을 놓침: {q} → {spans(q)}')
q = '2019 납입증명서 보니 연금저축 600 넣었는데 총급여 5000만원이면 세액공제 얼마예요?'
ok(blk(q) and blk(q)[1] == '99만 원' and unread(q) == [], f'연도 뒤 금액 계산: {blk(q)} / {unread(q)}')

# ── 정규식 단위 — _bare_amounts 가 읽는 것과 연도로 빼서 years 에 담는 것
for t, out, yrs in (('매달 50 납입확인서 공제', [50.0], []), ('월 50 납입확인서 공제', [50.0], []),
                    ('연간 90 납입증명서 공제', [90.0], []), ('1년에 50 납입증명서 공제', [50.0], []),
                    ('매년 2000 납입증명서 공제', [], ['2000']), ('매년 2030 넣으면 공제', [], ['2030']),   # 9차: 기간 말 뒤 해 꼴
                    ('50 납입확인서 공제', [50.0], []), ('1985 납입증명서 공제', [], ['1985']),
                    ('연금저축 2019 납입증명서', [], ['2019']), ('연금저축에 2030 넣으면 공제', [], ['2030']),
                    ('총급여 2019 귀속 원천징수 공제', [], ['2019']), ('연금저축에 2,019 납입증명서 공제', [2019.0], []),
                    ('TDF 2045 넣으면 공제', [], []), ('2030년 넣으면 공제', [], [])):
    got_y = []
    got = [v for v, _, _ in taxcalc._bare_amounts(t, [], None, got_y)]
    ok(got == out and [r for r, _, _ in got_y] == yrs, f'단위 없는 금액: {t} → {got} / 연도 {got_y}')
# years 를 주지 않아도(parse 의 호출) 읽는 결과는 같다
ok([v for v, _, _ in taxcalc._bare_amounts('1985 납입증명서 공제', [])] == [], 'years 없이 부를 때 결과가 다름')

# ── 지금 맞게 도는 질문은 그대로 — 평가셋·같은 뜻 다른 표현·대화 시험 질문에 이번에 바뀐 꼴(기간 말 뒤 연도 꼴, 두 자리 + 연도 말)이
# 없고, 새 그물(계산기가 뺀 연도 꼴)을 끄고 켠 결과가 같다
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
    for m in taxcalc._BARE_PERIOD.finditer(nq):
        raw = m.group(1)
        if ',' not in raw and (re.fullmatch(r'(?:19|20)\d\d', raw) and (2020 <= int(raw) <= 2079 or taxcalc._YEAR_AFTER.match(nq, m.end(1)))
                               or re.fullmatch(r'\d\d', raw) and taxcalc._YEAR_AFTER.match(nq, m.end(1))):
            touched.append(q)
    for rx in (taxcalc._BARE_VERB, taxcalc._BARE_INCOME):
        for m in rx.finditer(nq):
            if re.fullmatch(r'\d\d', m.group(1)) and taxcalc._YEAR_AFTER.match(nq, m.end(1)):
                touched.append(q)
ok(not touched, f'평가 질문 중 바뀐 연도 판정에 걸리는 것: {touched}')
on = [unread(q) for q in qs]
keep = taxcalc._bare_amounts
taxcalc._bare_amounts = lambda q, taken, notes=None, years=None: keep(q, taken, notes)
try:
    off = [unread(q) for q in qs]
finally:
    taxcalc._bare_amounts = keep
changed = [q for q, a, b in zip(qs, on, off) if a != b]
ok(not changed, f'평가 질문 그물 결과가 새 연도 그물로 바뀜: {changed}')
ok(not [it['id'] for it in ev if unread(it['question'])], '평가셋 100문항은 그물에 걸리지 않는다')

print(f'{passed}/{total} 통과')
sys.exit(0 if passed == total else 1)
