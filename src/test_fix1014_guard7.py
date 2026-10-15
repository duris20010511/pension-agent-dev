# -*- coding: utf-8 -*-
"""
10-14 7차 결함 검토 — guard7 묶음(taxcalc._bare_amounts 연도 판정 · orchestrator._YEAR_CTX · unread_amounts) 회귀 시험. API 호출 없음.

    python src/test_fix1014_guard7.py

6차 수정 뒤 회귀(611·612): 계산기(taxcalc._bare_amounts)는 2020~2079 만 연도로 보아 '연금저축 2019 납입증명서'의 '납입'을 넣는
말로 읽고 2,019만 원 납입으로 계산했다. 그물(orchestrator._YEAR_CTX)은 같은 2019를 연도로 보아 되묻지 않았다 — 611은 연도가
확정된 납입액으로 보였고, 612(총급여가 있을 때)는 그물이 비어 생성으로 갔다.
고친 뒤: 연도 말 목록(서류 이름·귀속·년·년도·연도)을 taxcalc._YEAR_WORD 하나로 두고 그물도 이것을 쓴다. 계산기는 4자리 19xx·20xx와
두 자리 뒤에 연도 말이 오면 금액으로 읽지 않는다. 있을 수 없는 해(1990년 전·내년 뒤)는 그물이 단위를 되묻는다(6차 규칙 그대로).
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
    p = taxcalc.parse(taxcalc.normalize(taxcalc.norm_ws(q)))
    return [(s['v'], s['role']) for s in p['spans']], [n['text'] for n in p.get('amount_notes', [])]


def no_year(q):
    """연도만 지운 같은 질문 — 연도는 금액이 아니므로 결과가 같아야 한다."""
    return re.sub(r'(IRP|저축)\s*(?:19|20)\d\d\s*', r'\1 ', q)


def year_free(q, y):
    """연도 y 를 금액으로 읽은 흔적이 없다 — 읽은 금액·금액 읽기 메모·계산 블록·그물 결과 어디에도."""
    v = float(y)
    sp, notes = spans(q)
    c = calc(q)
    text = ' '.join(b.get('text', '') for b in c.get('blocks', []))
    won = taxcalc.won(v)
    return (all(x != v for x, _ in sp) and not any(y in n for n in notes) and won not in text
            and not any(t == won for t, _ in unread(q)))


# ── 결함 611 — 총급여 없이 연도 + 납입증명서·납입확인서(고치기 전 [('2,019만 원', '납입')], 연도가 확정된 납입액으로 보임)
for q, y in (('연금저축 2019 납입증명서 보면 세액공제 얼마예요?', '2019'),
             ('연금저축 2015 납입확인서 보면 세액공제 얼마예요?', '2015'),
             ('연금저축 2000 납입증명서 보면 세액공제 얼마예요?', '2000')):
    u = unread(q)
    ok(year_free(q, y), f'611 연도를 금액으로 읽음: {q} → {spans(q)} / {u}')
    ok(not any(b.get('numbers') for b in calc(q).get('blocks', [])), f'611 연도로 계산함: {q}')
    ok(u == unread(no_year(q)), f'611 연도 하나로 그물 결과가 달라짐: {q} → {u} / {unread(no_year(q))}')
    # 해가 같은 서류 질문(6차에서 이미 맞던 '2024 납입증명서')과 같은 길로 간다
    q24 = q.replace(y, '2024')
    ok(u == unread(q24) and spans(q)[0] == spans(q24)[0], f'611 2024 서류 질문과 다름: {q} → {u} / {unread(q24)}')

# ── 결함 612 — 총급여가 있을 때(고치기 전 [] — 2,019만 원 납입으로 숫자 없는 블록을 만들고 그물이 비어 생성으로 감)
for q, y in (('총급여 5000만원인데 연금저축 2019 납입증명서 보면 세액공제 얼마예요?', '2019'),
             ('총급여 5000만원인데 연금저축 2015 납입확인서 보면 세액공제 얼마예요?', '2015')):
    sp, notes = spans(q)
    ok(sp == [(5000.0, 'income')] and notes == [], f'612 읽은 금액: {q} → {sp} {notes}')
    ok(year_free(q, y), f'612 연도를 금액으로 읽음: {q}')
    c = calc(q)
    ok(not c.get('assume') and not any(b.get('numbers') for b in c.get('blocks', [])), f'612 연도로 계산·가정: {q} → {c.get("assume")}')
    ok(unread(q) == unread(no_year(q)), f'612 연도 하나로 그물 결과가 달라짐: {q} → {unread(q)} / {unread(no_year(q))}')

# ── 같은 원인의 다른 표현 — 19xx·20xx 전체에서 연도 말(납입증명서·납입확인서·원천징수영수증·귀속·년·년도) 앞 숫자는 계산기가 읽지 않는다.
# 서류에 찍힐 수 있는 해(1990년~내년)는 그물도 연도로 보아 되묻지 않고, 그 밖의 해는 그물이 단위를 되묻는다(6차 규칙)
YEAR_TPLS = ('연금저축 {} 납입증명서 보면 세액공제 얼마예요?', '연금저축 {} 납입 증명서 보면 세액공제 얼마예요?',
             '연금저축 {}납입증명서 보면 세액공제 얼마예요?', 'IRP {} 납입확인서 보면 세액공제 얼마예요?',
             '총급여 5000만원인데 연금저축 {} 납입증명서 보면 세액공제 얼마예요?',
             '총급여 5000만원인데 IRP {} 납입확인서 보면 환급 얼마예요?',
             '연금저축 {} 원천징수영수증 보면 세액공제 얼마예요?', '연금저축 {} 귀속 세액공제 얼마예요?',
             '연금저축 {}년 세액공제 얼마예요?', '연금저축 {}년도 세액공제 얼마예요?')
for y in ('1900', '1950', '1985', '1989', '1990', '1999', '2000', '2010', '2015', '2019', str(THIS_YEAR), str(THIS_YEAR + 1),
          str(THIS_YEAR + 2), '2050', '2079', '2080', '2099'):
    plausible = 1990 <= int(y) <= THIS_YEAR + 1
    for tpl in YEAR_TPLS:
        q = tpl.format(y)
        ok(year_free(q, y), f'연도를 금액으로 읽음: {q} → {spans(q)}')
        u = unread(q)
        if re.search(r'\d년', q):
            continue                     # '2019년'은 그물 정규식이 년 앞 숫자를 아예 보지 않는다
        if plausible:
            ok(not any(t == y and r == ASK for t, r in u), f'있는 해를 단위 없는 금액으로 되물음: {q} → {u}')
        else:
            ok((y, ASK) in u, f'있을 수 없는 해를 되묻지 않음: {q} → {u}')

# 총급여 자리의 연도('총급여 2019 근로소득 원천징수영수증') — 소득 2,019만 원으로 읽지 않는다(_BARE_INCOME 길)
q = '총급여 2019 근로소득 원천징수영수증 보면 5000만원인데 연금저축 600 넣으면 세액공제 얼마예요?'
ok(all(v != 2019.0 for v, _ in spans(q)[0]) and not spans(q)[1], f'총급여 자리의 연도를 소득으로 읽음: {spans(q)}')
# 문장 앞 연도 + 뒤의 실제 금액 — 금액만 계산한다(고치기 전 2,019만 원도 납입으로 더했다)
q = '2019 납입증명서 보니 연금저축 600 넣었는데 총급여 5000만원이면 세액공제 얼마예요?'
c = calc(q)
ok(spans(q)[0] == [(600.0, 'contrib'), (5000.0, 'income')] and not spans(q)[1], f'연도 뒤 금액만 읽기: {spans(q)}')
ok(any(b.get('key_figure') == '99만 원' for b in c.get('blocks', [])) and unread(q) == [], f'연도 뒤 금액 계산: {c.get("blocks")}')

# ── 연도 말이 아니면 그대로 — 넣는 말이 붙은 4자리는 금액일 수 있어 지금처럼 읽고 되묻고, 19xx·20xx 가 아닌 숫자는 서류 앞이라도 금액이다
for q, y in (('연금저축 2019 넣으면 세액공제 얼마예요?', '2019'), ('연금저축 1980 납입하면 세액공제 얼마예요?', '1980'),
             ('연금저축 2015 납입하면 세액공제 얼마예요?', '2015'), ('IRP 1980 원천징수되면 세금 얼마예요?', '1980'),
             ('연금저축 2050 기준으로 세액공제 얼마예요?', '2050')):
    ok((y, ASK) in unread(q), f'금액일 수 있는 숫자를 놓침: {q} → {unread(q)}')
for q, v in (('연금저축 2019 넣으면 세액공제 얼마예요?', 2019.0), ('연금저축에 2019 납입했는데 세액공제 얼마예요?', 2019.0),
             ('연금저축 600 납입증명서 보면 세액공제 얼마예요?', 600.0), ('연금저축 1500 납입증명서 보면 세액공제 얼마예요?', 1500.0),
             ('총급여 5000만원인데 연금저축 1500 넣으면 세액공제 얼마예요?', 1500.0)):
    ok((v, 'contrib') in spans(q)[0], f'금액을 놓침: {q} → {spans(q)}')
ok(any(b.get('numbers') for b in calc('총급여 5000만원인데 연금저축 1500 넣으면 세액공제 얼마예요?').get('blocks', [])),
   '실제 금액 계산이 사라짐')

# ── 정규식 단위 — 연도 말 목록은 한 곳(taxcalc._YEAR_WORD)에 있고 그물도 그것을 쓴다
ok(orchestrator._YEAR_WORD is taxcalc._YEAR_WORD, '그물과 계산기의 연도 말 목록이 갈림')
for t, out in (('연금저축 2019 납입증명서', []), ('연금저축 1985 귀속 세액공제', []), ('연금저축 2019 넣으면 공제', [2019.0]),
               ('연금저축에 2,019 납입증명서 공제', [2019.0]), ('연금저축 600 납입증명서', [600.0]),
               ('연금저축에 1980 넣으면 공제', [1980.0]), ('총급여 2019 귀속 원천징수 공제', [])):
    got = [v for v, _, _ in taxcalc._bare_amounts(t, [])]
    ok(got == out, f'단위 없는 금액: {t} → {got}')

# ── 지금 맞게 도는 질문은 그대로 — 평가셋·같은 뜻 다른 표현·대화 시험 질문에서 7차 연도 규칙을 끄고 켠 결과가 같다
ev = json.load(open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
qs = [it['question'] for it in ev]
for g in json.load(open(os.path.join(ROOT, 'eval', 'paraphrase_set.json'), encoding='utf-8')):
    qs += [g['canonical']] + [it['q'] for it in g['items']]
for d in json.load(open(os.path.join(ROOT, 'eval', 'dialogs.json'), encoding='utf-8'))['dialogues']:
    qs += [t['user'] for t in d['turns']] + [t['expected_standalone'] for t in d['turns'] if t.get('expected_standalone')]
qs = list(dict.fromkeys(qs))


def snap(q):
    c = taxcalc.compute(q)
    return ([(b.get('topic'), bool(b.get('numbers')), b.get('key_figure'), b.get('figures')) for b in (c or {}).get('blocks', [])],
            spans(q), orchestrator.unread_amounts(q, c))


on = [snap(q) for q in qs]
keep = taxcalc._YEAR_AFTER
taxcalc._YEAR_AFTER = re.compile(r'(?!)')
try:
    off = [snap(q) for q in qs]
finally:
    taxcalc._YEAR_AFTER = keep
changed = [q for q, a, b in zip(qs, on, off) if a != b]
ok(not changed, f'평가 질문 결과가 연도 규칙으로 바뀜: {changed}')
ok(not [it['id'] for it in ev if unread(it['question'])], '평가셋 100문항은 그물에 걸리지 않는다')

print(f'{passed}/{total} 통과')
sys.exit(0 if passed == total else 1)
