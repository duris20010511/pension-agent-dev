# -*- coding: utf-8 -*-
"""
10-14 8차 검토 — parser8 묶음(taxcalc 넣는 계좌 읽기) 회귀 시험. API 호출 없음.

    python src/test_fix1014_parser8.py

결함 700~708(7차 회귀): 7차의 _dest_names 가 '있다·없다로만 나온 계좌는 넣는 곳이 아니다'를 넣으며, 이미 넣은 금액이 붙은 앞 계좌
('연금저축에 600만 원 채웠고')를 이 금액을 넣는 곳으로 읽었다 — HEAD 에서 맞던 '…IRP는 있는데 300만 원 더 넣으면' → IRP 148만 5천 원이
연금저축 99만 원('세액공제는 늘지 않는다')이 됐다.
원칙: (1) 앞 계좌 뒤 연결 어미로 마디가 닫히고 다른 계좌의 있다·없다·새로 만든다 말이 이어지면, 앞 계좌는 그 마디의 일(이미 넣은 돈·
          회사가 넣는 돈)이고 이 금액을 넣는 곳은 뒤 계좌다 — '있는데·있고·도 있는데·있어서'면 그 계좌, '없어서 만들고'면 새로 만드는 계좌.
      (2) 뒤 계좌가 없다고만 하면('IRP는 아직 없는데') 넣는 곳을 모른다 — 되묻되 계좌를 아는 납입(연금저축 600만 원 → 99만 원)은 블록에
          남긴다. 어느 계좌에 넣어도 공제 대상이 같으면('연금저축 300 + 300 더') 이미 넣은 계좌로 계산하고 그렇게 밝힌다(7차 시험 유지).
      (3) 'DC에 회사가 넣어주고' 처럼 회사 말이 연결 어미로 닫힌 앞 마디의 말('넣어주는'·'회사에서')은 이 금액이 나온 곳이 아니다.
재현 입력마다 기대 결과를, 지금 맞게 도는 이웃 질문(7차 결함 600~610 포함)은 그대로인지 함께 본다.
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


def key(q):
    for b in blocks(q):
        return b.get('key_figure')
    return None


def figs(q):
    return [f for b in blocks(q) for f in b.get('figures') or []]


def body(q):
    return '\n'.join(b['text'].split('\n[원문]')[0] for b in blocks(q))


def needs(q):
    return [n for b in blocks(q) for n in b.get('needs') or []]


def decide(q):
    return orchestrator.decide(q, calc=calc(q))[0]


def accts(q):
    return [(c['acct'], c['v']) for c in taxcalc.parse(q)['contrib']]


T = ' 세액공제? 총급여 5천'
ACCT_NEED = '넣을 계좌'
DC_LINE = '가입자부담금은 연 900만 원까지'      # DC 본인 납입을 계산했을 때만 붙는 줄
RETIRE_LINE = '퇴직금(퇴직급여·DC·퇴직연금) 쪽에서 온 돈'


def to_irp(q, base, v, want='148만 5천 원'):
    """앞 계좌(연금저축 base)에 더해 뒤 계좌 IRP 에 v 를 넣는다."""
    t = body(q)
    return key(q) == want and accts(q) == [('savings', base), ('irp', v)] and \
        f'납입: 연금저축 {taxcalc.won(base)}, IRP {taxcalc.won(v)}' in t and '더 넣는 연금저축' not in t and not needs(q)


def ask_keep(q, base, v, known='99만 원'):
    """넣는 계좌를 몰라 되묻되 계좌를 아는 납입의 공제는 남긴다."""
    p = taxcalc.parse(q)
    t = body(q)
    return p['acct_unsure'] == [v] and [c['v'] for c in p['contrib']] == [base] and key(q) == known and \
        figs(q) == [known] and any(ACCT_NEED in n for n in needs(q)) and decide(q) == '계산 + 되묻기' and \
        f'이 납입의 세액공제액 = {taxcalc.won(min(base, 600))} × 16.5% = {known}' in t and '148만 5천 원' not in t


def ask_only(q):
    """넣는 계좌를 몰라 숫자 없이 되묻는다(아는 납입 없음)."""
    p = taxcalc.parse(q)
    return not p['contrib'] and p['acct_unsure'] and not figs(q) and any(ACCT_NEED in n for n in needs(q)) \
        and decide(q) == '되묻기' and RETIRE_LINE not in body(q)


# ── 결함 700·703·704·702: 앞 계좌(이미 넣은 연금저축) + 뒤 계좌 IRP '있는데·도 있는데·계좌도 있는데' + 금액 → IRP(HEAD 와 같음)
for q, base, v in (('연금저축에 600만 원 채웠고 IRP는 있는데 300만 원 더 넣으면' + T, 600, 300),          # 700
                   ('연금저축에 400만 원 넣었고 IRP는 있는데 500만 원 더 넣으면' + T, 400, 500),          # 703
                   ('연금저축에 500만 원 넣었고 IRP 계좌도 있는데 400만 원 더 넣으면' + T, 500, 400),     # 704
                   ('연금저축에 매달 50만 원 넣고 있고 IRP도 있는데 300만 원 넣으면' + T, 600, 300),     # 702
                   # 같은 원인의 다른 표현 — '더' 없음·'있어서'·'있으니까'·'하나 있는데'·주어 조사·'개인형 IRP'·월 납입
                   ('연금저축에 600만 원 채웠고 IRP는 있는데 300만 원 넣으면' + T, 600, 300),
                   ('연금저축에 600만 원 넣었는데 IRP도 있어서 300만 원 더 넣으면' + T, 600, 300),
                   ('연금저축은 600만 원 채웠고 IRP도 있으니까 300만 원 더 넣으면' + T, 600, 300),
                   ('연금저축 600만 원 넣었고 IRP 하나 있는데 300만 원 넣으면' + T, 600, 300),
                   ('연금저축에 이미 600만 원 넣었고 IRP도 있는데 300만 원 더 넣으면' + T, 600, 300),
                   ('연금저축펀드에 600만 원 넣었고 개인형 IRP도 있는데 300만 원 더 넣으면' + T, 600, 300),
                   ('연금저축에 600만 원 넣었어요. IRP는 있는데 300만 원 더 넣으면' + T, 600, 300)):
    ok(to_irp(q, base, v), f'결함 700~704 뒤 계좌 IRP: {q} → {key(q)} {accts(q)}\n{body(q)}')
q = '연금저축에 월 50만 원씩 넣고 있는데 IRP도 있어서 매달 25만 원 더 넣으면' + T
ok(key(q) == '148만 5천 원' and accts(q) == [('savings', 600), ('irp', 300)], f'결함 702 월 납입: {q} → {key(q)} {accts(q)}')

# ── 결함 701: 앞 계좌 + 'IRP 계좌가 없어서 만들고·새로 만들어·개설하고' → 새로 만드는 IRP(전엔 plain 목록이 있으면 새 계좌 분기까지 못 갔다)
for q in ('연금저축에 600만 원 넣었는데 IRP 계좌가 없어서 만들고 300만 원 넣으면' + T,            # 701
          '연금저축에 600만 원 채웠고 IRP가 없어서 새로 만들어 300만 원 더 넣으면' + T,
          '연금저축에 600만 원 넣었는데 IRP는 없어서 개설하고 300만 원 넣으면' + T,
          '연금저축에 600만 원 넣었고 IRP도 하나 만들어서 300만 원 넣으면' + T):
    ok(to_irp(q, 600, 300), f'결함 701 새로 만드는 IRP: {q} → {key(q)} {accts(q)}\n{body(q)}')

# ── 결함 707: 반대 방향(앞 IRP + 뒤 연금저축 / 앞 연금저축 + 뒤 IRP '있어서') — 계좌 이름이 질문대로
q = 'IRP에 300만 원 넣었고 연금저축도 있는데 300만 원 더 넣으면' + T
ok(key(q) == '99만 원' and accts(q) == [('irp', 300), ('savings', 300)] and '납입: 연금저축 300만 원, IRP 300만 원' in body(q)
   and '더 넣는 연금저축 300만 원' in body(q), f'결함 707 뒤 계좌 연금저축: {body(q)}')
q = '연금저축 300만 원 넣었는데 IRP도 있어서 300만 원 더 넣으면' + T
ok(key(q) == '99만 원' and accts(q) == [('savings', 300), ('irp', 300)] and '더 넣는 IRP 300만 원' in body(q),
   f'결함 707 뒤 계좌 IRP: {body(q)}')
q = '개인형 IRP에 300만 원 넣었고 연금저축펀드도 있는데 300만 원 더 넣으면' + T
ok(accts(q) == [('irp', 300), ('savings', 300)], f'결함 707 개인형·펀드: {accts(q)}')
q = 'IRP에 600만 원 넣었고 연금저축도 있는데 300만 원 더 넣으면' + T
ok(key(q) == '148만 5천 원' and accts(q) == [('irp', 600), ('savings', 300)], f'결함 707 IRP 600 + 연금저축 300: {key(q)} {accts(q)}')

# ── 결함 705·708: 뒤 계좌가 없다고만 하면 넣는 곳을 모른다 — 되묻되 이미 아는 연금저축 600만 원의 99만 원은 블록에 남긴다
for q in ('연금저축에 600만 원 채웠고 IRP는 없는데 300만 원 더 넣으면' + T,                                    # 705
          '연금저축에 600만 원 넣었어요. IRP는 아직 없는데 300만 원 더 넣으면 얼마 돌려받나요? 총급여 5천',       # 708
          '연금저축에 600만 원 넣었고 IRP는 아직 없는데 300만 원 넣으면' + T,
          '연금저축에 600만 원 넣었고 IRP는 없지만 300만 원 더 넣으면' + T,
          '연금저축에 매달 50만 원 넣고 있고 IRP는 없는데 300만 원 더 넣으면' + T):
    ok(ask_keep(q, 600, 300), f'결함 705·708 되묻되 아는 몫 99만 원: {q} → {key(q)} {figs(q)} {needs(q)} {decide(q)}\n{body(q)}')
q = '연금저축에 400만 원 채웠고 IRP는 없는데 500만 원 더 넣으면' + T          # 연금저축이면 600만 원, IRP면 900만 원 — 갈린다
ok(ask_keep(q, 400, 500, '66만 원'), f'결함 705 400 + 500: {key(q)} {needs(q)}\n{body(q)}')
# 아는 납입이 없으면 숫자 없이 되묻기(7차와 같음) — 708 의 다른 꼴
for q in ('IRP는 없고 연금저축만 있는데 800만 원 더 넣으면' + T, 'IRP 계좌가 아직 없는데 800만 원 넣으면' + T,
          'IRP는 아직 없는데 300만 원 더 넣으면' + T):
    ok(ask_only(q), f'결함 708 아는 납입 없음은 되묻기: {q} → {figs(q)} {needs(q)}')
# 어느 계좌에 넣어도 공제 대상이 같으면 이미 넣은 계좌로 계산하고 그렇게 밝힌다(7차 시험 '300만 원 넣었고 IRP는 없는데 300만 원 더')
for q, acct, want in (('연금저축에 300만 원 넣었고 IRP는 없는데 300만 원 더 넣으면' + T, '연금저축', '99만 원'),
                       ('연금저축에 300만 원 넣었어요. IRP는 아직 없는데 300만 원 더 넣으면' + T, '연금저축', '99만 원'),
                       ('IRP에 600만 원 채웠고 연금저축은 없는데 300만 원 더 넣으면' + T, 'IRP', '148만 5천 원')):
    t = body(q)
    ok(key(q) == want and not needs(q) and '어느 쪽에 넣어도 공제 대상이 같아 ' + acct + '에 넣는 것으로 계산' in t,
       f'결함 705 같은 결과면 이미 넣은 계좌: {q} → {key(q)}\n{t}')

# ── 결함 706: 'DC에 회사가 넣어주고' — 앞 마디의 DC·회사는 이 금액의 계좌도 출처도 아니다
for q in ('DC에 회사가 넣어주고 IRP는 있는데 300만 원 넣으면' + T,           # 706
          'DC는 회사가 넣어주고 IRP도 있는데 300만 원 넣으면' + T,           # 706
          'DC는 회사에서 넣어 주고 IRP는 있어서 300만 원 넣으면' + T,
          'DC에 회사가 넣어주는데 IRP도 있어서 300만 원 넣으면' + T,
          '회사가 DC에 넣어주고 IRP는 있는데 300만 원 넣으면' + T,
          '퇴직연금은 회사가 넣어주고 IRP는 있는데 300만 원 넣으면' + T):
    t = body(q)
    ok(key(q) == '49만 5천 원' and accts(q) == [('irp', 300)] and '납입: IRP 300만 원' in t and DC_LINE not in t,
       f'결함 706 IRP 300만 원: {q} → {key(q)} {accts(q)}\n{t}')
q = 'DC에 회사가 넣어주고 연금저축도 있는데 300만 원 넣으면' + T
ok(accts(q) == [('savings', 300)] and DC_LINE not in body(q), f'결함 706 뒤 계좌 연금저축: {accts(q)}')
# 회사 마디가 닫힌 뒤 '더 넣으면'은 회사 몫에 본인이 더 넣는 DC 가입자부담금 — 전엔 앞 마디의 '넣어주는·회사에서'를 출처로 읽어
# '퇴직금 쪽에서 온 돈'이라고 적었다
for q in ('DC에 회사가 넣어주고 300만 원 더 넣으면' + T, 'DC에 회사가 넣어주는데 300만 원 더 넣으면' + T,
          'DC에 회사에서 매달 넣어 주고 있고 300만 원 더 넣으면' + T, 'DC에 회사가 넣어준 돈이 있는데 300만 원 더 넣으면' + T):
    t = body(q)
    ok(key(q) == '49만 5천 원' and accts(q) == [('irp', 300)] and '납입: DC 300만 원' in t and RETIRE_LINE not in t,
       f'결함 706 이웃 DC 본인 납입: {q} → {key(q)}\n{t}')
# 회사 마디가 닫히고 뒤 계좌가 없다고만 하면 넣는 계좌를 되묻는다('퇴직금 쪽에서 온 돈'이라고 단정하지 않는다)
for q in ('DC에 회사가 넣어주고 IRP는 없는데 300만 원 넣으면' + T, 'DC에 회사가 넣어주고 연금저축은 없는데 300만 원 넣으면' + T):
    ok(ask_only(q), f'결함 706 이웃 넣는 계좌 되묻기: {q} → {figs(q)} {needs(q)}\n{body(q)}')
# '더'도 다른 계좌 말도 없으면 본인 돈인지 퇴직금 쪽인지 모른다 — 숫자도 '퇴직금 쪽' 단정도 없이 되묻는다
for q in ('DC에 회사가 넣어주는데 300만 원 IRP에 넣으면' + T, 'DC는 회사가 넣어주고 300만 원 넣으면' + T):
    p = taxcalc.parse(q)
    ok(not figs(q) and p['retire_unsure'] == [300] and RETIRE_LINE not in body(q) and needs(q),
       f'결함 706 이웃 모름: {q} → {figs(q)} {needs(q)}\n{body(q)}')
# 이웃: 회사가 넣는 돈 그 자체는 그대로 퇴직금 쪽(HEAD 이후 3차 200·7차 600~609 와 같음)
for q in ('DC에 회사가 300만 원 넣어 주면' + T, '회사에서 매달 30만 원 DC에 넣어 주는데' + T,
          '회사에서 성과금 대신 300만 원을 DC에 넣어 준다는데' + T, 'DC에 회사에서 넣어준 돈 외에 300만 원 더 넣어 준대요' + T):
    ok(not figs(q) and not taxcalc.parse(q)['contrib'], f'결함 706 이웃 회사 돈: {q} → {figs(q)}')
# 이웃: 본인 말이 있으면 본인 납입(그대로)
for q in ('DC에 회사가 넣어주고 저는 300만 원 넣으면' + T, 'DC에 회사에서 넣어준 돈 외에 300만 원 더 넣으면' + T,
          'DC에 회사가 넣어주는 거랑 별개로 제가 300만 원 넣으면' + T):
    ok(key(q) == '49만 5천 원' and '납입: DC 300만 원' in body(q), f'결함 706 이웃 본인 DC: {q} → {key(q)}')
# 이웃: DC 를 깨거나 해지해 받은 돈은 그대로 모름(본인 돈으로 읽지 않는다)
for q in ('DC 깨서 300만 원 넣으면' + T, 'DC 해지해서 300만 원 넣으면' + T):
    ok(not figs(q), f'결함 706 이웃 DC 해지: {q} → {figs(q)}')

# ── 원칙을 꼴마다 — 앞 계좌(연금저축·IRP) × 앞 마디 × 뒤 계좌 말 × 금액
SAV, IRP = '연금저축', 'IRP'
heads = ['{A}에 {a}만 원 채웠고 ', '{A}에 {a}만 원 넣었고 ', '{A}에 {a}만 원 넣었는데 ', '{A} {a}만 원 넣었는데 ',
         '{A}에 {a}만 원 넣었어요. ', '{A}에 이미 {a}만 원 넣었고 ']
mids = {'to': ['{B}는 있는데 ', '{B}도 있는데 ', '{B} 계좌도 있는데 ', '{B}도 있어서 ', '{B} 계좌가 없어서 만들고 ',
               '{B}가 없어서 새로 만들어 ', '{B}에 ', '{B}로 '],
        'none': ['{B}는 없는데 ', '{B}는 아직 없는데 ']}
n_form = 0
for A, B in ((SAV, IRP), (IRP, SAV)):
    for a, b in ((600, 300), (400, 500), (300, 300)):
        for h in heads:
            for kind, ms in mids.items():
                for m in ms:
                    for tail in ('{b}만 원 더 넣으면', '{b}만 원 넣으면'):
                        q = (h + m + tail).format(A=A, B=B, a=a, b=b)
                        q = q.replace('연금저축는', '연금저축은').replace('연금저축가', '연금저축이').replace('연금저축로', '연금저축으로') + T
                        aa, bb = ('savings', 'irp') if A == SAV else ('irp', 'savings')
                        p = taxcalc.parse(q)
                        got = [(c['acct'], c['v']) for c in p['contrib']]
                        n_form += 1
                        if kind == 'to':
                            ok(got == [(aa, a), (bb, b)] and not p['acct_unsure'], f'꼴 뒤 계좌: {q} → {got}')
                            continue
                        s0, i0 = (a, 0) if aa == 'savings' else (0, a)
                        same = min(min(s0 + b, 600) + i0, 900) == min(min(s0, 600) + i0 + b, 900)
                        if same:
                            ok(got == [(aa, a), (aa, b)] and not p['acct_unsure'], f'꼴 같은 결과면 앞 계좌: {q} → {got}')
                        else:
                            el = min(min(s0, 600) + i0, 900)
                            ok(got == [(aa, a)] and p['acct_unsure'] == [b] and key(q) == taxcalc.won(el * 0.165),
                               f'꼴 되묻되 아는 몫: {q} → {got} {p["acct_unsure"]} {key(q)}')

# ── 7차 결함 610 이웃 그대로 — 아는 납입 없이 있다·없다로만 나온 계좌는 되묻기, 넣는다고 한 계좌·하나뿐인 계좌는 그 계좌
for q in ('IRP만 있고 연금저축은 없어서 800만 원 넣으면' + T, 'IRP는 있는데 연금저축이 없어서 800만 원 넣으면' + T,
          'IRP만 있고 연금저축은 없어서 500만 원 넣으면' + T, 'IRP가 없어서 800만 원 넣으면' + T,
          'IRP도 있고 연금저축도 있는데 800만 원 넣으면' + T):
    ok(ask_only(q), f'7차 610 이웃 되묻기: {q} → {figs(q)} {needs(q)}')
for q, want in (('IRP만 있고 연금저축은 없어서 IRP에 800만 원 넣으면' + T, '132만 원'),
                ('IRP만 있고 연금저축은 없어서 새로 만들어 800만 원 넣으면' + T, '99만 원'),
                ('IRP만 있는데 연금저축도 하나 만들어서 800만 원 넣으면' + T, '99만 원'),
                ('IRP만 있어서 800만 원 넣으면' + T, '132만 원'), ('IRP 가입해서 800만 원 넣으면' + T, '132만 원'),
                ('IRP에 있는 돈 말고 800만 원 더 넣으면' + T, '132만 원'), ('IRP 말고는 계좌가 없어서 800만 원 넣으면' + T, '132만 원'),
                ('연금저축 600만 원 넣었고 IRP 300만 원 넣으면' + T, '148만 5천 원'),
                ('연금저축에 600만 원, IRP는 있는데 300만 원 더 넣으면' + T, '148만 5천 원'),
                ('연금저축에 600만 원 넣었고 300만 원 더 넣으면' + T, '99만 원')):
    ok(key(q) == want and not taxcalc.parse(q)['acct_unsure'], f'7차 610 이웃 그대로: {q} → {key(q)}')

# ── 판정 단위 시험
ok(taxcalc._dest_parts('연금저축에 600만 원 채웠고 IRP는 있는데 ') == (['IRP'], False, ['연금저축'])
   and taxcalc._dest_parts('연금저축에 600만 원 넣었는데 IRP 계좌가 없어서 만들고 ') == (['IRP'], False, ['연금저축'])
   and taxcalc._dest_parts('연금저축에 600만 원 채웠고 IRP는 없는데 ') == ([], True, ['연금저축'])
   and taxcalc._dest_parts('DC에 회사가 넣어주고 IRP는 있는데 ') == (['IRP'], False, ['DC'])
   and taxcalc._dest_parts('IRP는 없고 연금저축에 ') == (['연금저축'], False, [])
   and taxcalc._dest_parts('IRP만 있는데 연금저축도 하나 만들어서 ') == (['연금저축'], False, [])
   and taxcalc._dest_names('IRP만 있고 연금저축은 없어서 ') == ([], True)
   and taxcalc._dest_names('IRP가 없어서 새로 만들어 ') == (['IRP'], False), '_dest_parts 판정')

print(f'{passed}/{total} 통과 (꼴 {n_form}개 포함)')
sys.exit(0 if passed == total else 1)
