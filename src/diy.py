# -*- coding: utf-8 -*-
"""
DIY 연금 설계(B) — 고객이 납입 조합과 수령 계획을 바꿔 가며 세금 숫자를 나란히 본다.

왜 — 상담 채팅은 질문 하나에 숫자 하나다. '연금저축 600+IRP 300 과 연금저축 900' 처럼 조합을 견주거나
'60세부터 10년 나눠 받으면 해마다 세금이 얼마' 같은 설계는 표로 봐야 한다.

숫자는 모두 taxcalc 공용 함수(credit_rate·eligible·withdraw_tax·age_rate·won)로 낸다 — 규칙 식은 taxcalc
한 곳에만 둔다. 이 모듈이 하는 일은 입력 정리, 해마다 재원 잔액 굴리기(① → ② → ③), 근거·가정 모으기뿐이다.

고르는 건 고객 — 조합에 순위를 매기지 않고 입력 순서대로 나란히 둔다('추천'·'유리' 같은 판단 표현 없음).

이 모듈은 clova·answer 를 부르지 않는다. test_diy.py 가 API 없이 손계산 값과 맞춰 본다.

입력(plan, 금액은 모두 만 원):
  {'income': {'kind': '총급여'|'종합소득', 'v': 5000},
   'age': 40,                                   # 지금 나이(선택) — 수령 시작까지 남은 해 계산용
   'combos': [{'name': 'A', 'savings': 600, 'irp': 300}, ...],      # 1~5개
   'payout': {'start_age': 60, 'years': 10, 'balance': 10000,      # 선택
              'free': 0, 'retire': 0, 'credit': 10000,              # 재원: ① 공제 안 받은 돈 ② 퇴직금 ③ 공제받은 돈·수익
              'eff': None,                                          # 퇴직소득세 실효세율(%) — 퇴직금이 있을 때 필요
              'return_rate': 0.0,                                   # 연 수익률(0~0.2)
              'year_n_start': 1,                                    # 시작 해의 연금수령연차(선택, 기본 1)
              'actual_n_start': 1}}                                 # 시작 해의 연금 실제수령연차(선택, 기본 1)
잘못된 입력(숫자가 아님·음수·범위 밖)은 ValueError — 서버는 400 으로 돌려주면 된다.
"""
import json
import math
import re

import taxcalc
from taxcalc import won, pct

# ---------------------------------------------------------------------------
# 연금계좌 납입한도 — 규칙표(RULEBOOK R-01~R-15)에 따로 번호가 없어 원문을 여기 둔다.
# test_diy.py 가 extracted/docs 파일 안에 글자 그대로 있는지 검사한다(공백 무시).
# ---------------------------------------------------------------------------
CONTRIB_CAP = 1800          # 만 원, 연금저축+IRP(퇴직연금계좌 포함) 전 금융기관 합산
CAP_QUOTES = [
    ('doc41.txt', '연금저축과 IRP는 합산해서 연1,800만원까지 입금이 가능하다. '
                  '그러나 납입액이 모두 세액공제 받을 수 있는 것은 아니다.'),
    ('doc1.txt', '계약금액 | 전 금융기관 합산 연 1,800만원 이내 (퇴직연금계좌 포함)'),
]
CAP_NAME = '연금계좌 납입한도'

MAX_COMBOS = 5
_BANNED = ('추천', '최적', '유리', '가장 좋은')       # 고객 문구에 쓰지 않는 판단 표현(test 가 검사)

# ③ 재원 연금수령 1,500만 원 판단 — 세금은 taxcalc._row_tax 가 같은 기준(inside > 1500)으로 낸다.
# 여기서는 '그해가 1,500만 원 초과인가' 표시만 한다. test_diy 가 withdraw_tax 세금과 이 표시가 맞는지 검사한다.
SEP_LIMIT = 1500

# 공제율 두 값(R-02) — 소득을 모를 때 둘 다 보여준다. 숫자를 따로 적지 않고 taxcalc.credit_rate 에서 얻는다.
_RATE_HI = taxcalc.credit_rate(0, '총급여')
_RATE_LO = taxcalc.credit_rate(float('inf'), '총급여')


# ---------------------------------------------------------------------------
# 입력 정리
# ---------------------------------------------------------------------------
def _num(x, name, lo=None, hi=None, default=None):
    """숫자 하나. 비었으면 default(없으면 오류), 숫자가 아니거나 범위 밖이면 ValueError."""
    if x is None or (isinstance(x, str) and not x.strip()):
        if default is None:
            raise ValueError(f'{name} 값이 없습니다')
        return float(default)
    if isinstance(x, bool):
        raise ValueError(f'{name} 는 숫자여야 합니다: {x!r}')
    try:
        v = float(str(x).replace(',', '')) if isinstance(x, str) else float(x)
    except OverflowError:               # JSON 의 아주 큰 정수(10**400)는 float 로 못 바꾼다
        raise ValueError(f'{name} 가 너무 큽니다') from None
    except (TypeError, ValueError):
        raise ValueError(f'{name} 는 숫자여야 합니다: {str(x)[:40]!r}') from None
    if not math.isfinite(v):
        raise ValueError(f'{name} 는 유한한 숫자여야 합니다: {x!r}')
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f'{name} 범위({lo}~{hi}) 밖: {v:g}')
    return v


def _r(x):
    """출력용 반올림(만 원 단위 소수 4자리 = 1원)."""
    return None if x is None else round(float(x), 4)


def _tag(rids, files):
    """' (R-01·R-02, doc41)' — 숫자 옆에 붙이는 근거 표기."""
    parts = ['·'.join(rids)] if rids else []
    docs = '·'.join(_doc_name(f) for f in _sort_docs(files))
    if docs:
        parts.append(docs)
    return f' ({", ".join(parts)})' if parts else ''


def _acct(s, i):
    """'연금저축 600만 원 + IRP 300만 원' — 0원인 계좌는 빼고."""
    parts = ([f'연금저축 {won(s)}'] if s else []) + ([f'IRP {won(i)}'] if i else [])
    return ' + '.join(parts) or '납입 없음'


def _doc_name(f):
    return f[:-4] if f.endswith('.txt') else f


def _name(raw, idx):
    """조합 이름 — 고객이 친 글자가 요약 문장(마크다운)에 들어가므로 태그·마크다운 기호·줄바꿈을 뺀다."""
    s = '' if raw is None or isinstance(raw, (dict, list)) else str(raw)
    s = re.sub(r'[<>&"\'`*_#|\[\]\\\x00-\x1f\x7f]', '', s).strip()[:20]
    return s or chr(ord('A') + idx)


def _sort_docs(files):
    def key(f):
        m = re.search(r'\d+', f)
        return (int(m.group()) if m else 10 ** 6, f)
    return sorted(dict.fromkeys(files), key=key)


class _Evidence:
    """쓴 원문 키(taxcalc.RULES)와 규칙표 밖 원문(납입한도)을 모은다."""

    def __init__(self):
        self.keys = []
        self.cap = False

    def add(self, *keys):
        for k in keys:
            taxcalc.rule(k)             # 없는 키면 KeyError — 원문 없는 근거를 달지 않는다
            if k not in self.keys:
                self.keys.append(k)

    def rule_ids(self):
        return taxcalc.rule_ids(self.keys)

    def files(self):
        fs = [taxcalc.RULES[k][0] for k in self.keys]
        if self.cap:
            fs += [f for f, _ in CAP_QUOTES]
        return _sort_docs(fs)

    def items(self):
        out = []
        for k in self.keys:
            rid = taxcalc.RULE_ID.get(k)
            f, text = taxcalc.RULES[k]
            out.append({'rid': rid, 'name': taxcalc.RULE_NAME.get(rid, ''), 'key': k, 'file': f, 'text': text})
        out.sort(key=lambda e: (e['rid'] or 'R-99', _sort_docs([e['file']])[0]))
        if self.cap:
            out += [{'rid': None, 'name': CAP_NAME, 'key': 'contrib_cap', 'file': f, 'text': t} for f, t in CAP_QUOTES]
        return out


# ---------------------------------------------------------------------------
# 납입 조합 — 공제 대상·환급액·공제 못 받는 금액(과세제외금액)·납입한도 (R-01·R-02, R-06)
# ---------------------------------------------------------------------------
def _combo(c, idx, income, years_to_start, ev, out):
    if not isinstance(c, dict):
        raise ValueError(f'combos[{idx}] 는 dict 여야 합니다')
    name = _name(c.get('name'), idx)
    s = _num(c.get('savings'), f'combos[{idx}].savings', 0, 100000, default=0)
    i = _num(c.get('irp'), f'combos[{idx}].irp', 0, 100000, default=0)
    total = s + i
    over_cap = max(0.0, total - CONTRIB_CAP)
    # 한도를 넘는 돈은 넣을 수 없다. 넘는 만큼 연금저축에서 덜 넣는다고 본다 — 연금저축은 600만 원까지만 공제 대상이라
    # 이렇게 빼면 공제 대상이 줄지 않는다(IRP에서 빼면 줄 수 있다). 경고 문장에 이 가정을 적는다.
    cut_s = min(s, over_cap)
    ds, di = s - cut_s, i - (over_cap - cut_s)
    deposit = ds + di
    no_income = income is not None and income[1] <= 0
    # 공제 대상(R-01). 소득이 없으면 한도와 상관없이 공제를 못 받으므로(R-02 '종합소득이 있어야') 공제받는 돈은 0
    elig = 0.0 if no_income else taxcalc.eligible(ds, di)
    excluded = max(0.0, deposit - elig)              # 공제 못 받는 납입 → 나중에 비과세(과세제외금액)
    rids, files, lines, warns = ['R-01', 'R-02'], ['doc41.txt'], [], []
    ev.add('ded_limits', 'ded_rates', 'ded_cap_tax')

    rate, refund = None, None
    if income is not None:
        kind, v = income
        if no_income:
            refund = 0.0
            ev.add('ded_need_income')
            warns.append(f'조합 {name}: 소득이 없으면 세액공제를 받을 수 없고, 넣은 돈 전부가 공제 못 받은 돈으로 쌓입니다'
                         + _tag(['R-02', 'R-06'], ['doc39.txt', 'doc41.txt']))
            lines.append('세액공제 0원 — 소득이 없으면 세액공제를 받을 수 없음' + _tag(['R-02'], ['doc41.txt']))
        else:
            rate = taxcalc.credit_rate(v, kind)
            refund = elig * rate / 100
    if refund is not None and rate is not None:
        lines.append(f'세액공제 {won(refund)} = 공제 대상 {won(elig)} × {pct(rate)}' + _tag(rids, files))
    elif refund is None:
        lines.append(f'공제 대상 {won(elig)} — 세액공제는 소득 구간에 따라 {won(elig * _RATE_HI / 100)}({pct(_RATE_HI)}) 또는 '
                     f'{won(elig * _RATE_LO / 100)}({pct(_RATE_LO)})' + _tag(rids, files))
    if not no_income:
        lines.append(f'공제 대상 {won(elig)}: 연금저축은 600만 원까지, IRP를 더해 합산 900만 원까지')
    s_cap = taxcalc.eligible(ds, 0)                  # 연금저축만의 공제 한도(R-01) — 숫자를 따로 적지 않는다
    if ds > s_cap and not no_income:
        lines.append(f'연금저축 {won(ds)} 중 {won(s_cap)}을 넘는 {won(ds - s_cap)}은 연금저축 공제 한도 밖'
                     + (' (IRP와 합쳐 900만 원까지 공제 대상)' if di else ''))
    if excluded:
        ev.add('free')
        rids_x, files_x = rids + ['R-06'], files + ['doc39.txt']
        lines.append(f'공제 못 받는 {won(excluded)}은 과세제외금액으로 쌓여 나중에 꺼낼 때 그 원금은 비과세(한도를 넘어도 과세 없음). '
                     '그 돈으로 난 운용수익은 ③ 재원이라 과세' + _tag(['R-06'], ['doc39.txt']))
    else:
        rids_x, files_x = rids, files
    if over_cap:
        ev.cap = True
        warns.append(f'조합 {name}: 납입 합계 {won(total)}이 연금계좌 납입한도 연 1,800만 원(전 금융기관 합산, 퇴직연금계좌 포함)을 '
                     f'{won(over_cap)} 넘습니다 — 넘는 돈은 넣을 수 없어 {won(deposit)}까지만 계산'
                     f'(넘는 만큼 연금저축에서 덜 넣는다고 봄: 연금저축 {won(ds)} + IRP {won(di)})'
                     + _tag([], ['doc41.txt', 'doc1.txt']))
        files_x = files_x + ['doc41.txt', 'doc1.txt']
    row = {'name': name, 'savings': _r(s), 'irp': _r(i), 'total': _r(total), 'deposit': _r(deposit),
           'deposit_savings': _r(ds), 'deposit_irp': _r(di),
           'over_cap': _r(over_cap), 'eligible': _r(elig), 'rate': rate, 'refund': _r(refund), 'excluded': _r(excluded),
           'text': {'total': won(total), 'eligible': won(elig), 'refund': None if refund is None else won(refund),
                    'excluded': won(excluded), 'rate': None if rate is None else pct(rate)},
           'lines': lines, 'warnings': warns,
           'rule_ids': sorted(dict.fromkeys(rids_x)), 'docs': _sort_docs(files_x)}
    if years_to_start:
        # 수령 시작 전까지 같은 금액을 해마다 넣는다고 보면 — 참고값(수익 제외)
        row['years_to_start'] = years_to_start
        row['refund_total'] = None if refund is None else _r(refund * years_to_start)
        row['excluded_total'] = _r(excluded * years_to_start)
        row['text']['refund_total'] = None if refund is None else won(refund * years_to_start)
        row['text']['excluded_total'] = won(excluded * years_to_start)
    out['warnings'] += warns
    return row


# ---------------------------------------------------------------------------
# 수령 설계 — 해마다 withdraw_tax 로 재원별 세금, 재원은 ① → ② → ③ 순서로 줄어든다 (R-04~R-10)
# ---------------------------------------------------------------------------
def annual_amount(balance, years, r):
    """평가액을 years 년에 걸쳐 해마다 같은 금액으로 받는 값. 해마다 1월 1일에 꺼내고 남은 돈이 r 로 불어난다고
    보면 마지막 해에 잔액이 0 이 되는 금액(기초 지급 연금식). r=0 이면 평가액 ÷ years."""
    if r == 0:
        return balance / years
    return balance * r / ((1 - (1 + r) ** -years) * (1 + r))


def _year(age, fund, w, yn, act, eff, early):
    """한 해. withdraw_tax 를 불러 재원별 배분·세금을 받고, 재원별 세금은 앞 재원부터 누적 인출로 다시 불러
    차이로 나눈다(배분이 ①→②→③ 순서라 앞쪽 누적 인출의 배분은 전체 배분과 같다 — 식을 복사하지 않으려고)."""
    bal = sum(fund.values())
    args = dict(free=fund['free'], retire=fund['retire'], credit=fund['credit'], balance=bal, year_n=yn,
                actual_n=act, age=age, early=early)
    res = taxcalc.withdraw_tax(w, eff=eff, **args)
    eff_calc = eff if eff is not None else 0.0       # ③·① 세금은 퇴직소득세율과 무관 — 재원별 값을 내려고만 쓴다
    by_src, acc, prev = [], 0.0, 0.0
    for j, (kind, take, inside, over) in enumerate(res['rows']):
        acc += take
        cum = taxcalc.withdraw_tax(acc, eff=eff_calc, **args)['total']
        t = max(0.0, cum - prev)                     # 소수 오차로 -0.0 이 나오지 않게(세금은 음수가 없다)
        prev = cum
        if kind == 'retire' and eff is None:
            t = None                                 # 퇴직소득세율을 몰라 금액을 못 낸다
        by_src.append({'kind': kind, 'name': taxcalc.FUND_NAME[kind], 'take': _r(take), 'inside': _r(inside),
                       'over': _r(over), 'tax': _r(t), 'tax_text': None if t is None else won(t),
                       'line': res['lines'][j] if j < len(res['lines']) else ''})
    return res, by_src


def _payout(po, cur_age, ev_all, out):
    if not isinstance(po, dict):
        raise ValueError('payout 은 dict 여야 합니다')
    start = int(_num(po.get('start_age'), 'payout.start_age', 19, 100))
    years = int(_num(po.get('years'), 'payout.years', 1, 50))
    if start + years - 1 > 120:
        raise ValueError('payout: 수령 끝 나이가 120세를 넘습니다')
    B = _num(po.get('balance'), 'payout.balance', 0, 10 ** 7, default=0)
    free = _num(po.get('free'), 'payout.free', 0, 10 ** 7, default=0)
    retire = _num(po.get('retire'), 'payout.retire', 0, 10 ** 7, default=0)
    credit = _num(po.get('credit'), 'payout.credit', 0, 10 ** 7, default=0)
    eff = po.get('eff')
    eff = None if eff is None or (isinstance(eff, str) and not eff.strip()) else _num(eff, 'payout.eff', 0, 50)
    # 손실이 나면 어느 재원에서 먼저 빠지는지 문서 근거가 없어 음수 수익률은 받지 않는다
    r = _num(po.get('return_rate'), 'payout.return_rate', 0, 0.2, default=0)
    n0 = int(_num(po.get('year_n_start'), 'payout.year_n_start', 1, 30, default=1))
    # 이미 연금을 받아 온 계좌(알림의 62세 고객 등)는 실제수령연차가 1보다 크다 — 퇴직소득세 감면율(R-09)이 달라진다
    m0 = int(_num(po.get('actual_n_start'), 'payout.actual_n_start', 1, 60, default=1))
    assume, warns, needs = out['assumptions'], out['warnings'], out['needs']

    # 재원 구성 맞추기 — 평가액과 재원 합계가 다를 때
    comp = free + retire + credit
    if comp <= 0 and B <= 0:
        needs.append('연금계좌 평가액(수령 시작 때)')
        return None
    if comp <= 0:
        credit = B
        assume.append(f'재원 구성이 없어 평가액 {won(B)} 전부를 ③ 세액공제 받은 돈·운용수익으로 봄' + _tag(['R-06'], ['doc39.txt']))
    elif B <= 0:
        B = comp
    elif comp < B - 1e-9:
        credit += B - comp
        assume.append(f'평가액과 재원 합계의 차이 {won(B - comp)}은 운용수익(③ 재원)으로 봄' + _tag(['R-06'], ['doc39.txt']))
    elif comp > B + 1e-9:
        warns.append(f'재원 합계 {won(comp)}이 평가액 {won(B)}보다 커서 재원 합계를 평가액으로 계산했습니다')
        B = comp
    fund = {'free': free, 'retire': retire, 'credit': credit}

    w = annual_amount(B, years, r)
    ev = _Evidence()                    # 수령 설계 근거만 따로 모아 payout 의 rule_ids·docs 로 낸다
    ev.add('order', 'pension_req')      # 한도·세율 근거는 연금수령 해(55세 이후)가 있을 때 아래에서 더한다
    if free:
        ev.add('free')
    if retire:
        ev.add('no_period_req', 'retire_rate', 'retire_in', 'retire_years', 'actual_year')
        if eff is None:
            needs.append('퇴직소득세 실효세율(퇴직소득 원천징수영수증의 퇴직소득세 ÷ 퇴직금) — 퇴직금 재원 세금 계산에 필요')
            warns.append('퇴직소득세 실효세율이 없어 퇴직금 재원 세금은 비워 두었습니다' + _tag(['R-09'], ['doc39.txt']))

    rows, cum, yn, act = [], 0.0, None, m0 - 1
    early_ages, over_years, over1500_years, need_eff_years = [], [], [], []
    early_kinds, over_kinds = set(), set()          # 연금외수령으로 빠진 재원 — 경고 문장을 해당 재원만으로 쓴다
    for k in range(1, years + 1):
        age = start + k - 1
        early = taxcalc.age_rate(age) is None       # 연금소득세율이 없는 나이(55세 전, R-04·R-07) = 연금수령이 아님
        if not early:
            yn = n0 if yn is None else yn + 1
            act += 1
        bal = sum(fund.values())
        fund_start = {kk: _r(vv) for kk, vv in fund.items()}
        wk = bal if k == years else min(w, bal)      # 마지막 해는 남은 돈 전부(소수 오차 정리)
        res, by_src = _year(age, fund, wk, None if early else yn, act or 1, eff, early)
        over = sum(o for _, _, _, o in res['rows'])
        credit_in = sum(ins for kind, _, ins, _ in res['rows'] if kind == 'credit')
        o1500 = (not early) and credit_in > SEP_LIMIT
        tax = res['total']
        if tax is None:
            need_eff_years.append(k)
            cum = None
        elif cum is not None:
            cum += tax
        if early:
            early_ages.append(age)
            early_kinds |= {kind for kind, take, _, _ in res['rows'] if take > 1e-9}
        elif over > 1e-9:
            over_years.append((k, age, over, res['limit']))
            over_kinds |= {kind for kind, _, _, o in res['rows'] if o > 1e-9}
        if o1500:
            over1500_years.append((k, age, credit_in))
        # 재원 줄이기(① → ② → ③) 뒤 남은 돈이 불어난 몫은 운용수익이라 ③ 재원에 더한다(R-06 원문 '③ … 수익')
        for kind, take, _, _ in res['rows']:
            fund[kind] = max(0.0, fund[kind] - take)
        growth = sum(fund.values()) * r
        fund['credit'] += growth
        lim = res['limit']
        rows.append({
            'year': k, 'age': age, 'early': early, 'year_n': None if early else yn, 'actual_n': None if early else act,
            'balance_start': _r(bal), 'fund_start': fund_start, 'withdraw': _r(wk), 'limit': _r(lim), 'over_limit': _r(over),
            'rate': taxcalc.age_rate(age), 'pension_credit': _r(credit_in), 'over_1500': o1500,
            'by_source': by_src, 'tax': _r(tax), 'cum_tax': _r(cum), 'growth': _r(growth),
            'remain': {kk: _r(vv) for kk, vv in fund.items()}, 'balance_end': _r(sum(fund.values())),
            'text': {'withdraw': won(wk), 'balance_start': won(bal),
                     'limit': '연금외수령(55세 전)' if early else ('한도 없음(11년차부터)' if lim is None else won(lim)),
                     'tax': None if tax is None else won(tax), 'cum_tax': None if cum is None else won(cum),
                     'rate': None if early else pct(taxcalc.age_rate(age))},
            'lines': res['lines'],
        })

    total_w = sum(x['withdraw'] for x in rows)

    def how_taxed(kinds):
        """연금외수령으로 빠진 재원별 과세 — 그 재원이 실제로 빠졌을 때만 적는다(R-06·R-10)."""
        parts = []
        if 'free' in kinds:
            parts.append('① 공제 안 받은 돈은 비과세')
        if 'retire' in kinds:
            parts.append('② 퇴직금은 퇴직소득세 100%(감면 없음)')
        if 'credit' in kinds:
            parts.append('③ 재원은 16.5% 기타소득세')
        return ', '.join(parts) or '재원별 세금'

    # 경고·가정 — 해당하는 해가 있을 때만
    if early_ages:
        ev.add('over_limit')
        if 'credit' in early_kinds:
            ev.add('early_credit')
        if 'retire' in early_kinds:
            ev.add('early_retire')
        if 'free' in early_kinds:
            ev.add('free')
        docs_e = ['doc39.txt'] + (['doc1.txt'] if early_kinds & {'credit', 'retire'} else [])
        rids_e = ['R-04', 'R-10'] + (['R-06'] if 'free' in early_kinds else [])
        warns.append(f'55세 전 {len(early_ages)}년({early_ages[0]}~{early_ages[-1]}세)은 연금수령이 아니라 연금외수령 — '
                     + how_taxed(early_kinds) + '로 계산' + _tag(sorted(rids_e), docs_e))
    if over_years:
        ev.add('over_limit')
        if 'retire' in over_kinds:
            ev.add('retire_over')
        if 'free' in over_kinds:
            ev.add('free')
        ages = ', '.join(f'{a}세' for _, a, _, _ in over_years[:5]) + (' 등' if len(over_years) > 5 else '')
        rids_o = ['R-05', 'R-10'] + (['R-06'] if 'free' in over_kinds else [])
        warns.append(f'연금수령한도를 넘는 해가 {len(over_years)}번({ages}) — 넘는 부분은 연금외수령이라 '
                     + how_taxed(over_kinds) + _tag(sorted(rids_o), ['doc39.txt']))
    if over1500_years:
        ev.add('over_1500', 'whole_amount', 'progressive', 'excluded', 'all_accounts', 'not_always')
        ages = ', '.join(f'{a}세' for _, a, _ in over1500_years[:5]) + (' 등' if len(over1500_years) > 5 else '')
        warns.append(f'③ 재원 연금이 연 1,500만 원을 넘는 해가 {len(over1500_years)}번({ages}) — 넘는 부분만이 아니라 '
                     '그해 ③ 연금 전액이 16.5% 분리과세 또는 종합과세 대상' + _tag(['R-08'], ['doc38.txt']))
        assume.append('연 1,500만 원을 넘는 해의 세금은 16.5% 분리과세로 계산(종합과세를 고르면 다른 소득에 따라 달라짐)'
                      + _tag(['R-08'], ['doc38.txt']))
    elif (free or retire) and w > SEP_LIMIT:
        ev.add('excluded')              # 인출은 1,500만 원을 넘지만 ①·② 재원은 1,500만 원 판단에 들어가지 않는다
    first = next((x['age'] for x in rows if not x['early']), None)     # 연금수령 첫해 나이(55세 전 해는 빼고)
    if first is not None:
        ev.add('limit_formula', 'limit_year1', 'year_count', 'year6', 'rate_table', 'limit_rates', 'all_accounts')
        if n0 == 1:
            note = ''
            if cur_age is not None:
                # 연금수령연차는 받든 안 받든 개시 가능한 해(만 55세·가입 5년)부터 쌓인다(doc39 'year_count').
                # 지금 가입해 있다면 그해는 늦어도 max(55, 지금 나이 + 5)세 — 수령 시작보다 이르면 연차가 더 높다
                ready = max(55, int(cur_age) + 5)
                if ready < first:
                    note = (f' — 지금 {int(cur_age)}세에 이미 가입해 있다면 {ready}세부터 연차가 쌓여 {first}세에는 '
                            f'{first - ready + 1}년차일 수 있고, 그러면 한도가 더 큼')
            assume.append(f'2013년 3월 1일 이후 가입한 계좌로, {first}세에 처음 연금수령 요건을 갖춰 그해가 연금수령연차 1년차라고 봄 '
                          '(그 전에 요건을 갖췄다면 연차가 더 높아 한도가 커짐)' + note + _tag(['R-05'], ['doc39.txt']))
        else:
            assume.append(f'연금수령 첫해({first}세)의 연금수령연차를 {n0}년차로 봄' + _tag(['R-05'], ['doc39.txt']))
        if not retire:
            assume.append('가입 5년 이상 요건을 갖췄다고 봄' + _tag(['R-04'], ['doc39.txt']))
        else:
            assume.append(f'연금 실제수령연차(퇴직소득세 감면율 결정)는 연금수령 첫해({first}세)가 {m0}년차이고 해마다 받는다고 봄'
                          + _tag(['R-09'], ['doc39.txt', 'doc40.txt']))
    if r:
        assume.append(f'해마다 1월 1일 평가액에서 같은 금액을 꺼내고 남은 돈은 연 {pct(round(r * 100, 4))}로 불어난다고 봄 — '
                      '불어난 돈은 운용수익이라 ③ 재원에 더함' + _tag(['R-06'], ['doc39.txt']))
    else:
        assume.append('해마다 1월 1일 평가액에서 같은 금액(평가액 ÷ 기간)을 꺼내고 운용수익은 없다고 봄(수익률 0%)')
    if first is not None:               # 55세 전 해만 있으면 연금소득이 없어 1,500만 원 판단도 없다
        assume.append('다른 금융기관 연금계좌에서 받는 연금은 없다고 봄(1,500만 원 판단은 전 금융기관 합산)'
                      + _tag(['R-08'], ['doc38.txt']))
    if cur_age is not None and start < cur_age:
        warns.append(f'수령 시작 나이 {start}세가 지금 나이 {int(cur_age)}세보다 적습니다')

    ev_all.add(*ev.keys)
    rids, files = ev.rule_ids(), ev.files()
    return {
        'start_age': start, 'years': years, 'balance': _r(B), 'return_rate': r, 'eff': eff, 'year_n_start': n0,
        'actual_n_start': m0,
        'fund': {'free': _r(free), 'retire': _r(retire), 'credit': _r(credit)},
        'annual': _r(w), 'rows': rows,
        'total_withdraw': _r(total_w), 'total_tax': _r(cum),
        'avg_rate': None if cum is None or total_w <= 0 else round(cum / total_w * 100, 2),
        'over_limit_years': [k for k, _, _, _ in over_years], 'over_1500_years': [k for k, _, _ in over1500_years],
        'early_years': [x['year'] for x in rows if x['early']], 'tax_unknown_years': need_eff_years,
        'text': {'annual': won(w), 'total_withdraw': won(total_w), 'total_tax': None if cum is None else won(cum)},
        'rule_ids': rids, 'docs': files,
    }


# ---------------------------------------------------------------------------
# 다음 행동 — 상담 연결·위임 서비스 안내 등(상품명 없이). chat 문장은 taxcalc.compute 가 읽는 꼴로 쓴다
# ---------------------------------------------------------------------------
def _next_actions(res):
    acts = []
    if any(c['excluded'] for c in res['combos']):
        acts.append({'label': '공제 못 받은 돈은 나중에 어떻게 돼요?', 'kind': 'chat',
                     'payload': '세액공제 못 받은 납입금은 나중에 꺼낼 때 세금이 어떻게 되나요?'})
    po = res['payout']
    # 금액은 만 원 단위로 반올림해 묻는다('1,138만 1,627원'의 끝자리는 계산기가 금액으로 못 읽는다)
    if po and po['over_limit_years']:
        y = next(x for x in po['rows'] if x['year'] == po['over_limit_years'][0])
        f = y['fund_start']
        bal = round(y['balance_start'])
        fr, rt = round(f['free']), round(f['retire'])
        cr = bal - fr - rt                  # 반올림한 평가액과 합이 맞게 ③ 은 뺄셈으로
        parts = ([f'세액공제 안 받은 돈 {won(fr)}'] if fr else []) + ([f'퇴직금 {won(rt)}'] if rt else [])
        mix = ''
        if parts:
            # ①·② 를 말하면 ③ 금액도 말해야 상담 계산기가 인출 순서를 끝까지 나눈다(③ 을 모르면 규칙만 낸다)
            if cr > 0:
                parts.append(f'세액공제 받은 돈과 수익 {won(cr)}')
            tail = (f'이고 퇴직소득세 실효세율은 {po["eff"]:g}%예요. ' if rt and po['eff'] is not None
                    else '이에요. ')
            mix = '그중 ' + ', '.join(parts) + tail
        acts.append({'label': '한도를 넘게 받으면 세금은?', 'kind': 'chat',
                     'payload': f'{y["age"]}세이고 연금계좌 평가액이 {won(bal)}이에요. ' + mix
                                + f'연금수령 {y["year_n"]}년차에 {won(round(y["withdraw"]))} 빼면 세금은요?'})
    if po and po['over_1500_years']:
        y = next(x for x in po['rows'] if x['year'] == po['over_1500_years'][0])
        amt = max(round(y['pension_credit']), 1501)
        acts.append({'label': '1년에 1,500만 원 넘게 받으면?', 'kind': 'chat',
                     'payload': f'{y["age"]}세, 연금계좌에서 연 {won(amt)} 받으면 세금은요?'})
    acts.append({'label': '조합·수령 기간 바꿔 다시 보기', 'kind': 'diy', 'payload': ''})
    acts.append({'label': '상담 연결', 'kind': 'handoff', 'payload': ''})
    acts.append({'label': '연금 운용 위임 서비스 안내', 'kind': 'handoff', 'payload': '연금 운용 위임 서비스 안내 요청'})
    return acts


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def simulate(plan):
    """DIY 설계 → 결과 dict(JSON 그대로 낼 수 있음). 잘못된 입력은 ValueError."""
    if plan is None:
        plan = {}
    if not isinstance(plan, dict):
        raise ValueError('plan 은 dict 여야 합니다')
    ev = _Evidence()
    out = {'summary': [], 'income': None, 'combos': [], 'payout': None, 'rule_ids': [], 'docs': [], 'evidence': [],
           'assumptions': [], 'warnings': [], 'needs': [], 'next_actions': []}

    income = None
    inc = plan.get('income')
    if inc:
        if not isinstance(inc, dict):
            raise ValueError('income 은 {"kind", "v"} 여야 합니다')
        kind = inc.get('kind') or '총급여'
        if kind not in ('총급여', '종합소득'):
            raise ValueError(f"income.kind 는 '총급여' 또는 '종합소득': {kind!r}")
        v = _num(inc.get('v'), 'income.v', 0, 10 ** 6)
        income = (kind, v)
        out['income'] = {'kind': kind, 'v': _r(v), 'rate': taxcalc.credit_rate(v, kind) if v > 0 else None,
                         'text': won(v)}
    cur_age = plan.get('age')
    cur_age = None if cur_age is None or cur_age == '' else _num(cur_age, 'age', 0, 120)

    combos = plan.get('combos') or []
    if not isinstance(combos, list):
        raise ValueError('combos 는 목록이어야 합니다')
    if len(combos) > MAX_COMBOS:
        raise ValueError(f'combos 는 {MAX_COMBOS}개까지입니다')
    po = plan.get('payout')
    years_to_start = None
    if po and isinstance(po, dict) and cur_age is not None and po.get('start_age') not in (None, ''):
        gap = int(_num(po.get('start_age'), 'payout.start_age', 19, 100)) - int(cur_age)
        years_to_start = gap if gap > 0 else None
    for idx, c in enumerate(combos):
        out['combos'].append(_combo(c, idx, income, years_to_start, ev, out))
    if combos:
        if income is None:
            out['needs'].append(f'총급여(직장인) 또는 종합소득금액 — 세액공제율 {pct(_RATE_HI)}/{pct(_RATE_LO)}를 정하는 데 필요')
        elif income[1] > 0:
            out['assumptions'].append('올해 낼 세금(결정세액)이 세액공제액보다 크다고 봄 — 낼 세금이 적으면 세액공제도 그만큼 줄어듦'
                                      + _tag(['R-02'], ['doc41.txt']))
        if years_to_start:
            out['assumptions'].append(f'수령 시작 전 {years_to_start}년 동안 해마다 같은 금액을 넣고 소득 구간도 그대로라고 봄 '
                                      '(누적 세액공제·과세제외금액 참고값, 수익 제외)')
    if po:
        out['payout'] = _payout(po, cur_age, ev, out)
    if not combos and not po:
        out['needs'].append('납입 조합(연금저축·IRP 납입액) 또는 수령 계획(시작 나이·기간·평가액)')

    # 결론 먼저
    for c in out['combos']:
        head = f'조합 {c["name"]}: {_acct(c["savings"], c["irp"])} → '
        if c['refund'] is not None:
            line = head + f'세액공제 {c["text"]["refund"]}'
        else:
            line = head + f'공제 대상 {c["text"]["eligible"]}'
        if c['excluded']:
            line += f', 공제 못 받는 {c["text"]["excluded"]}은 나중에 꺼낼 때 비과세'
        if c['over_cap']:
            line += f', 납입한도 연 1,800만 원을 {won(c["over_cap"])} 넘음'
        out['summary'].append(line + _tag(c['rule_ids'], c['docs']))
    p = out['payout']
    if p:
        if p['total_tax'] is not None:
            line = (f'수령: {p["start_age"]}세부터 {p["years"]}년, 해마다 {p["text"]["annual"]}씩 → 세금 합계 '
                    f'{p["text"]["total_tax"]}, 받는 돈의 {pct(p["avg_rate"])}')
        else:
            line = (f'수령: {p["start_age"]}세부터 {p["years"]}년, 해마다 {p["text"]["annual"]}씩 → 퇴직소득세율이 없어 '
                    '세금 합계는 계산하지 않음')
        extra = []
        if p['over_limit_years']:
            extra.append(f'한도를 넘는 해 {len(p["over_limit_years"])}번')
        if p['over_1500_years']:
            extra.append(f'1,500만 원 넘는 해 {len(p["over_1500_years"])}번')
        if extra:
            line += ' — ' + ', '.join(extra)
        out['summary'].append(line + _tag(p['rule_ids'], p['docs']))

    out['rule_ids'] = ev.rule_ids()
    out['docs'] = ev.files()
    out['evidence'] = ev.items()
    for k in ('assumptions', 'warnings', 'needs'):
        out[k] = list(dict.fromkeys(out[k]))
    out['next_actions'] = _next_actions(out)
    return out


def banned_words(result):
    """고객 문구(근거 원문 제외)에 들어간 판단 표현 목록 — 시험·사후 검사용."""
    body = {k: v for k, v in result.items() if k != 'evidence'}
    text = json.dumps(body, ensure_ascii=False)
    return [w for w in _BANNED if w in text]


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    demo = {'income': {'kind': '총급여', 'v': 5000}, 'age': 40,
            'combos': [{'name': 'A', 'savings': 600, 'irp': 300}, {'name': 'B', 'savings': 900, 'irp': 0}],
            'payout': {'start_age': 60, 'years': 10, 'balance': 10000, 'credit': 10000}}
    r = simulate(demo)
    print('\n'.join(r['summary']))
    print(json.dumps({k: r[k] for k in ('rule_ids', 'docs', 'assumptions', 'warnings', 'needs')}, ensure_ascii=False,
                     indent=1))
