# -*- coding: utf-8 -*-
"""
먼저 알려주기(A) — 고객이 묻기 전에, 프로필과 날짜만 보고 챙길 일을 찾아 알린다.

왜 — 연금 세금은 '때'를 놓치면 되돌릴 수 없는 것이 많다. 12월 31일이 지나면 올해 공제는 끝이고,
ISA 만기 후 60일이 지나면 연금계좌로 옮길 수 없고, 한도를 넘겨 받으면 퇴직소득세 감면이 사라진다.
상담 질문을 기다리지 않고 달력·나이·인출 계획을 훑어 먼저 알린다(멘토 피드백: 에이전틱 확장).

원칙
  - 숫자는 모두 taxcalc 공용 함수(credit_rate·eligible·isa_extra·pension_limit·age_rate·withdraw_tax)로 낸다.
    식을 여기 다시 쓰지 않는다 — 규칙이 바뀌면 taxcalc 한 곳만 고치면 된다. 1,500만 원 초과 전액 16.5%(R-08)·
    나이별 세율(R-07)·실제수령연차 감면율(R-09)도 withdraw_tax 를 불러 읽는다(비공개 함수는 쓰지 않는다).
  - 프로필은 먼저 _clean 으로 고른다 — 글자·음수·NaN·날짜 형식 오류는 그 필드만 빼고(리포트에 '확인 필요'로 표시),
    빠진 값으로 계산이 안 되는 알림은 내지 않는다. 틀린 숫자로 알리느니 알리지 않는다.
  - 기준값(1,500만 원·16.5%·70%·만 55세·가입 5년·2013.3.1·60일)도 RULES 원문 문장에서 읽는다.
  - 근거 문서 번호는 RULES 의 파일에서 가져온다(규칙 번호 R-xx 는 RULEBOOK).
  - 프로필에 정보가 없으면 그 알림은 내지 않는다(대신 채운 값은 alert['assumptions'] 에 적는다).
  - 비교만 보여주고 고르지 않는다 — '추천'·'최적'·'유리' 같은 판단 표현과 상품명을 쓰지 않는다.
  - API(CLOVA)를 부르지 않는다. test_alerts.py 가 API 없이 검사한다.

알림 종류(kind)
  year_end_credit  10~12월, 올해 공제 한도가 남음 → 남은 공제 대상 × 공제율        R-01·R-02  doc41
  isa_maturity     ISA 만기 60일 전 ~ 만기 후 60일 → 추가 공제 대상·환급, 전환 마감  R-03       doc4·doc23
  age55            올해(또는 3개월 안에) 만 55세 → 연금 개시 가능, 첫해 한도       R-04·R-05  doc39
  over_limit_plan  올해 인출 계획이 연금수령한도 초과 → 한꺼번에 vs 나눠 받기 세금  R-05·R-06·R-09·R-10  doc39
  over_1500        올해 ③ 재원 연금수령 합계 1,500만 원 초과 → 전액 16.5% 또는 종합과세  R-08  doc38
  rate_step        내년까지 70·80세가 되어 연금소득세율이 내려감               R-07       doc38
  risk_limit       DC·IRP 위험자산 70% 초과(높음)·65% 이상(참고), 조건 충족 TDF 예외  R-14  doc14·doc53
  actual_year_11   연금실제수령연차가 내년 11년차(actual_year_21: 21년차) → 퇴직소득세 감면 30→40%(40→50%)
                   올해 계획으로 퇴직금이 다 빠지면 내지 않는다                             R-09  doc39·doc40

프로필 필드(금액은 만 원) — data/demo_profiles.json 의 '_설명' 과 같다
  id, name, summary            화면용
  birth                        생년월일 → 만 나이, 55세 도래, 70·80세 세율 구간
  income {kind, v}             세액공제율(총급여 5,500만 / 종합소득 4,500만 기준)
  retire_plan {date, amount}   퇴직 예정일·예상 퇴직급여 → 55세 알림의 IRP 이전 안내
  isa {maturity, amount}       ISA 만기일·옮길 수 있는 만기 지급액
  accounts[]                   type('연금저축'|'IRP'|'DC'), opened(가입일), paid_this_year(올해 납입),
                               balance(평가액), funds{free, retire, credit}(재원), retire_eff(퇴직소득세 실효세율 %),
                               plan_this_year(올해 인출 계획), paid_years(올해 전까지 실제로 받은 해 수),
                               risky_pct(위험자산 비중 %)

alert = {'id','kind','kind_label','severity':'높음|중간|참고','title','message'(결론 먼저 2~3문장),
         'figures':{이름: 표기}, 'rule_ids', 'rule_names', 'docs', 'quotes':[{'rid','file','text'}],
         'when':'YYYY-MM-DD', 'when_label', 'dday', 'detail':[계산 줄], 'assumptions', 'needs',
         'next_actions':[{'label','kind':'chat'|'diy'|'handoff','payload'}], 'profile_id'}
  next_actions 의 payload — chat 이면 상담 탭에 보낼 질문 문장, diy 면 diy.simulate 의 plan 일부(화면 미리 채우기),
  handoff 면 상담 연결 사유.
"""
import copy
import json
import math
import os
import re
import threading
from datetime import date, datetime, timedelta

from taxcalc import (RULES, RULE_ID, RULE_NAME, age_rate, credit_rate, eligible, isa_extra, pct, pension_limit,
                     rule, rule_ids, withdraw_tax, won)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_PATH = os.path.join(ROOT, 'data', 'demo_profiles.json')

# ---------------------------------------------------------------------------
# RULES 에 없는 기한 원문 — ISA 전환 60일. test_alerts 가 문서 안에 글자 그대로 있는지 검사한다.
# ---------------------------------------------------------------------------
EXTRA_QUOTES = {
    'isa_60': ('R-03', 'doc4.txt', 'ISA만기일로부터 60일이 경과 한 경우 전환 불가 (60일은 캘린더 기준, 초일불산입방식 적용)'),
    # 위험자산 비중에 조건 충족 TDF 는 넣지 않는다(100% 투자 가능) — DC 에도 같은 한도가 걸린다는 근거이기도 하다
    'tdf_100': ('R-14', 'doc53.txt', 'TDF는 금융감독원장이 정한 조건을 충족한 경우 DC와 IRP 적립금의 100% 투자 가능함.'),
}


# ---------------------------------------------------------------------------
# 기준값 — 원문 문장에서 읽는다(코드에 숫자를 따로 박지 않는다)
# ---------------------------------------------------------------------------
def _num(key, rx):
    return float(re.search(rx, rule(key)).group(1).replace(',', ''))


SEP_CAP = _num('over_1500', r'([\d,]+)만원')                 # 1,500만 원 (R-08)
SEP_RATE = _num('over_1500', r'(\d+(?:\.\d+)?)%')           # 16.5% (R-08)
RISK_CAP = _num('risk_limit', r'(\d+)%\s*이내')              # 70% (R-14)
RISK_NEAR = RISK_CAP - 5                                     # 65% 이상이면 '근접'(계약서 기준)
PENSION_AGE = int(_num('pension_req', r'만\s*(\d+)세'))      # 만 55세 (R-04)
MIN_YEARS = int(_num('pension_req', r'가입기간\s*(\d+)년'))   # 가입 5년 (R-04)
_y, _m, _d0 = map(int, re.search(r'(\d{4})년\s*(\d+)월\s*(\d+)일', rule('year6')).groups())
OLD_CUT = date(_y, _m, _d0)                                  # 2013-03-01 전 가입이면 (R-05)
OLD_START = int(_num('year6', r'(\d+)년차로'))               # 6년차부터
ISA_DAYS = int(re.search(r'(\d+)일', EXTRA_QUOTES['isa_60'][2]).group(1))   # 만기 후 60일(초일 불산입)
_BIG = 10 ** 9
CAP_ALL = eligible(_BIG, _BIG)                               # 합산 공제 한도 900만 (R-01)
CAP_SAVINGS = eligible(_BIG, 0)                              # 연금저축 공제 한도 600만 (R-01)

PENSION_TYPES = ('연금저축', 'IRP')
SEVERITY = {'높음': 0, '중간': 1, '참고': 2}
KIND_LABEL = {
    'year_end_credit': '연말 세액공제', 'isa_maturity': 'ISA 만기 전환', 'age55': '만 55세 연금 개시',
    'over_limit_plan': '연금수령한도 초과', 'over_1500': '연 1,500만 원 초과', 'rate_step': '연금소득세율 변화',
    'risk_limit': '위험자산 한도', 'actual_year_11': '퇴직소득세 감면 확대', 'actual_year_21': '퇴직소득세 감면 확대',
}
FUND_SHORT = {'free': '세액공제 안 받은 돈', 'retire': '퇴직금', 'credit': '세액공제 받은 돈과 수익'}


# ---------------------------------------------------------------------------
# 날짜
# ---------------------------------------------------------------------------
def _date(s):
    if isinstance(s, datetime):             # datetime 은 date 의 하위형이지만 date 와 비교·뺄셈이 안 된다
        return s.date()
    return s if isinstance(s, date) else date.fromisoformat(str(s)[:10])


def _today(today):
    """기준일. None·빈 글자면 오늘, 형식이 틀리면 ValueError(서버가 400 으로 돌려주게)."""
    if today is None or (isinstance(today, str) and not today.strip()):
        return date.today()
    try:
        return _date(today.strip() if isinstance(today, str) else today)
    except (TypeError, ValueError):
        raise ValueError(f'기준일은 YYYY-MM-DD 형식이어야 합니다: {today!r}') from None


def _add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:                      # 2월 29일생 — 평년에는 2월 28일로 본다
        return d.replace(year=d.year + n, day=28)


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue


def _age(birth, today):
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _kdate(d, today):
    return f'{d.month}월 {d.day}일' if d.year == today.year else f'{d.year}년 {d.month}월 {d.day}일'


def _dday(d, today):
    n = (d - today).days
    return f'D-{n}' if n > 0 else 'D-day' if n == 0 else f'{-n}일 지남'


# ---------------------------------------------------------------------------
# 프로필 고르기 — 서버는 화면이 보낸 profile 을 그대로 넘길 수 있다(POST /alerts 의 profile).
# 잘못된 값 하나 때문에 '231만 원 더 환급'(음수 납입) 같은 틀린 숫자가 나가거나 알림 전체가 조용히 사라지지 않게
# 계산 전에 필드 단위로 거른다.
# ---------------------------------------------------------------------------
_INCOME_KINDS = ('총급여', '종합소득')      # 다른 말('연봉' 등)은 어느 기준(5,500만/4,500만)인지 정하지 않는다
# 계좌 숫자 필드 → (최소, 최대). 비율·세율은 0~100%
_ACCT_NUM = {'paid_this_year': (0, None), 'balance': (0, None), 'plan_this_year': (0, None),
             'paid_years': (0, 100), 'risky_pct': (0, 100), 'retire_eff': (0, 100)}


def _fnum(x, lo=0.0, hi=None):
    """숫자(또는 '1,800' 같은 숫자 글자) → float. 참/거짓·NaN·무한대·범위 밖은 None."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, str):
        x = x.strip().replace(',', '')
        if not x:
            return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < lo or (hi is not None and v > hi):
        return None
    return v


def _fdate(x):
    """날짜 → 'YYYY-MM-DD', 못 읽으면 None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        return _date(x).isoformat()
    except (TypeError, ValueError):
        return None


def _clean(profile):
    """→ (계산용 프로필 사본, 뺀 필드 이름 목록)."""
    if not isinstance(profile, dict):
        return {}, ['프로필 형식']
    drop = []
    p = {k: v for k, v in profile.items() if k not in ('birth', 'income', 'isa', 'retire_plan', 'accounts')}
    if profile.get('birth') is not None:
        b = _fdate(profile['birth'])
        if b:
            p['birth'] = b
        else:
            drop.append('birth')
    inc = profile.get('income')
    if inc:
        v = _fnum(inc.get('v')) if isinstance(inc, dict) else None
        if v and inc.get('kind') in _INCOME_KINDS:
            p['income'] = {'kind': inc['kind'], 'v': v}
        else:
            drop.append('income')
    isa = profile.get('isa')
    if isa:
        m = _fdate(isa.get('maturity')) if isinstance(isa, dict) else None
        x = _fnum(isa.get('amount')) if isinstance(isa, dict) else None
        if m and x:
            p['isa'] = {'maturity': m, 'amount': x}
        else:
            drop.append('isa')
    rp = profile.get('retire_plan')
    if rp:
        q = {}
        if isinstance(rp, dict):
            d, x = _fdate(rp.get('date')), _fnum(rp.get('amount'))
            if d:
                q['date'] = d
            elif rp.get('date') is not None:
                drop.append('retire_plan.date')
            if x:
                q['amount'] = x
            elif rp.get('amount') is not None:
                drop.append('retire_plan.amount')
        if q:
            p['retire_plan'] = q
        elif not isinstance(rp, dict):
            drop.append('retire_plan')
    raw = profile.get('accounts')
    if raw is not None and not isinstance(raw, (list, tuple)):
        drop.append('accounts')
        raw = []
    accts = []
    for j, a in enumerate(raw or []):
        if not isinstance(a, dict) or not isinstance(a.get('type'), str):
            drop.append(f'accounts[{j}]')
            continue
        c = {k: v for k, v in a.items() if k not in _ACCT_NUM and k not in ('opened', 'funds')}
        if a.get('opened') is not None:
            o = _fdate(a['opened'])
            if o:
                c['opened'] = o
            else:
                drop.append(f'accounts[{j}].opened')
        for k, (lo, hi) in _ACCT_NUM.items():
            if a.get(k) is None:
                continue
            v = _fnum(a[k], lo, hi)
            if v is None:
                drop.append(f'accounts[{j}].{k}')
            else:
                c[k] = int(v) if k == 'paid_years' else v
        f = a.get('funds')
        if f is not None:
            vals = {k: _fnum(f.get(k) if f.get(k) is not None else 0) for k in ('free', 'retire', 'credit')} \
                if isinstance(f, dict) else {'free': None}
            if all(v is not None for v in vals.values()):
                c['funds'] = vals
            else:
                drop.append(f'accounts[{j}].funds')     # 재원 하나라도 모르면 인출 순서를 못 정한다
        accts.append(c)
    p['accounts'] = accts
    return p, drop


# ---------------------------------------------------------------------------
# 연금수령연차 — R-04·R-05
# ---------------------------------------------------------------------------
def _has_retire(acct):
    return (acct.get('funds') or {}).get('retire', 0) > 0


def start_date(acct, birth):
    """연금 개시가 가능해지는 날 = 만 55세와 가입 5년 중 늦은 날. 퇴직금이 있으면 가입기간 요건이 없다(R-04)."""
    opened = _date(acct['opened'])
    return max(_add_years(birth, PENSION_AGE), opened if _has_retire(acct) else _add_years(opened, MIN_YEARS))


def year_n(acct, birth, year):
    """그해 연금수령연차(R-05). 개시 가능한 해가 1년차(2013.3.1 전 가입은 6년차), 실제 수령과 무관하게 해마다 는다.
    아직 개시 전이면 None."""
    if not acct.get('opened') or birth is None:
        return None
    s = start_date(acct, birth)
    if s.year > year:
        return None
    return year - s.year + (OLD_START if _date(acct['opened']) < OLD_CUT else 1)


def _funds(acct):
    f = acct.get('funds') or {}
    return {k: float(f.get(k, 0) or 0) for k in ('free', 'retire', 'credit')}


def _wd(acct, w, funds, balance, n, m, age):
    """taxcalc.withdraw_tax 로 한 해 인출 세금."""
    return withdraw_tax(w, free=funds['free'], retire=funds['retire'], credit=funds['credit'], balance=balance,
                        year_n=n, eff=acct.get('retire_eff'), actual_n=m, age=age)


def _left(funds, res):
    """인출 뒤 남은 재원."""
    taken = {k: t for k, t, _, _ in res['rows']}
    return {k: max(0.0, v - taken.get(k, 0.0)) for k, v in funds.items()}


def _acct_name(p, i):
    accts = p.get('accounts', [])
    t = accts[i].get('type', '계좌')
    same = [j for j, a in enumerate(accts) if a.get('type') == t]
    return t if len(same) == 1 else f'{t} {same.index(i) + 1}'


# ---------------------------------------------------------------------------
# 알림 모양
# ---------------------------------------------------------------------------
def _docnum(f):
    m = re.search(r'(\d+)', f)
    return int(m.group(1)) if m else 0


def _alert(p, today, kind, sev, title, message, figures, keys, when, when_label, actions, key='',
           detail=(), assumptions=(), needs=(), extra=()):
    quotes = [{'rid': RULE_ID.get(k), 'file': RULES[k][0], 'text': rule(k)} for k in keys]
    quotes += [{'rid': EXTRA_QUOTES[k][0], 'file': EXTRA_QUOTES[k][1], 'text': EXTRA_QUOTES[k][2]} for k in extra]
    rids = sorted(set(rule_ids(keys)) | {EXTRA_QUOTES[k][0] for k in extra})
    return {
        'id': f'{p.get("id", "?")}:{kind}' + (f':{key}' if key else '') + f':{when.isoformat()}',
        'profile_id': p.get('id'),
        'kind': kind, 'kind_label': KIND_LABEL.get(kind, kind), 'severity': sev,
        'title': title, 'message': message, 'figures': figures,
        'rule_ids': rids, 'rule_names': {r: RULE_NAME[r] for r in rids},
        'docs': sorted({q['file'] for q in quotes}, key=_docnum), 'quotes': quotes,
        'when': when.isoformat(), 'when_label': when_label,
        'dday': '지금 확인' if when_label == '지금' else _dday(when, today),
        'detail': list(detail), 'assumptions': list(assumptions), 'needs': list(needs),
        'next_actions': actions,
    }


def _chat(label, q):
    return {'label': label, 'kind': 'chat', 'payload': q}


def _handoff(label='상담 연결', why=''):
    return {'label': label, 'kind': 'handoff', 'payload': why}


def _diy(label, plan):
    return {'label': label, 'kind': 'diy', 'payload': plan}


# ---------------------------------------------------------------------------
# 1. 연말 세액공제 — R-01·R-02 (doc41)
# ---------------------------------------------------------------------------
def _year_end(p, today, ctx):
    inc = p.get('income')
    if today.month < 10 or not inc:
        return []
    accts = [a for a in p.get('accounts', []) if a.get('type') in PENSION_TYPES and a.get('paid_this_year') is not None]
    if not accts:
        return []
    s = sum(a['paid_this_year'] for a in accts if a['type'] == '연금저축')
    i = sum(a['paid_this_year'] for a in accts if a['type'] == 'IRP')
    now = eligible(s, i)
    left = CAP_ALL - now
    if left <= 0:
        return []
    # 연금저축으로 더 채울 수 있는 몫 = 연금저축을 끝없이 더 넣었을 때 늘어나는 공제 대상(R-01 을 eligible 로만 계산)
    s_room = eligible(s + _BIG, i) - now
    i_room = left - s_room                                             # 나머지는 IRP 로만 채워진다
    r = credit_rate(inc['v'], inc['kind'])
    more = left * r / 100
    end = date(today.year, 12, 31)
    parts = ([f'연금저축 {won(s_room)}'] if s_room else []) + ([f'IRP {won(i_room)}'] if i_room else [])
    has_irp = any(a.get('type') == 'IRP' for a in p.get('accounts', []))
    # 직장인은 연말정산, 종합소득자는 종합소득세 신고에서 공제된다(doc41)
    head = (f'올해 연말정산에서 {won(more)}을 더 돌려받을 수 있습니다. ' if inc['kind'] == '총급여' else
            f'올해분 종합소득세 신고 때 세금을 {won(more)} 더 줄일 수 있습니다. ')
    msg = (head + f'연금저축·IRP 공제 대상이 {won(left)} 남아 있어({" + ".join(parts)}), {_kdate(end, today)}까지 채우면 '
           f'{won(left)} × {pct(r)} = {won(more)}입니다. '
           + (f'IRP로만 {won(left)}을 채워도 공제 대상은 같습니다.' if s_room else      # IRP 는 남은 몫을 다 받는다
              '납부할 세금이 공제액보다 적으면 그만큼만 공제됩니다.'))
    figures = {'올해 납입': f'연금저축 {won(s)} · IRP {won(i)}', '지금 공제 대상': won(now), '남은 공제 대상': won(left)}
    if s_room and i_room:
        figures.update({'그중 연금저축': won(s_room), '그중 IRP': won(i_room)})
    figures.update({'세액공제율': pct(r), '더 돌려받을 금액': won(more), '다 채웠을 때 공제액': won(CAP_ALL * r / 100),
                    '납입 마감': f'{_kdate(end, today)} ({_dday(end, today)})'})
    detail = [f'지금 공제 대상: 연금저축 {won(s)}, IRP {won(i)} → {won(now)} '
              f'(연금저축 {won(CAP_SAVINGS)}, IRP 포함 합산 {won(CAP_ALL)} 한도 — R-01)',
              f'남은 공제 대상 {won(left)} = 합산 한도 {won(CAP_ALL)} − {won(now)}'
              + (f' (연금저축은 {won(CAP_SAVINGS)}까지라 {won(s_room)}, 나머지 {won(i_room)}은 IRP)' if s_room and i_room else ''),
              f'세액공제율: {inc["kind"]} {won(inc["v"])} → {pct(r)} (R-02)',
              f'더 돌려받을 금액 = {won(left)} × {pct(r)} = {won(more)}; 다 채우면 올해 공제액 {won(CAP_ALL)} × {pct(r)} '
              f'= {won(CAP_ALL * r / 100)}']
    if s_room:
        detail.append('어느 계좌로 채워도 공제 대상이 같은 몫은 고객이 고른다 — 연금저축은 부분 인출이 자유롭고, '
                      'IRP는 법정 사유가 있어야 부분 인출이 된다(doc41)')
    assume = ['지금까지 낸 금액이 올해 납입 전부라고 봄(12월 31일까지 들어갈 자동이체 없음)',
              '납부할 세금(결정세액)이 공제액보다 많다고 봄 — 적으면 그만큼만 공제(R-02 원문)']
    unknown = [_acct_name(p, j) for j, a in enumerate(p.get('accounts', []))
               if a.get('type') in PENSION_TYPES and a.get('paid_this_year') is None]
    if unknown:
        assume.append(f'{"·".join(unknown)}은 올해 납입액이 프로필에 없어 0원으로 봄')
    if not has_irp:                       # 남은 몫은 늘 IRP 로도 채울 수 있다고 안내하므로
        assume.append('IRP가 없으므로 IRP로 채우는 몫은 새로 만들어 넣는 경우')
    q_parts = ([f'연금저축에 {won(s + s_room)}'] if s + s_room else []) + ([f'IRP에 {won(i + i_room)}'] if i + i_room else [])
    combos = [{'name': '지금', 'savings': s, 'irp': i},
              {'name': '남은 한도 채우기', 'savings': s + s_room, 'irp': i + i_room}]
    if s_room:
        combos.append({'name': 'IRP로만 채우기', 'savings': s, 'irp': i + left})
    actions = [_chat('다 채우면 공제액은?', f'{inc["kind"]} {won(inc["v"])}인데 {", ".join(q_parts)} 넣으면 세액공제 얼마예요?'),
               _diy('납입 조합 비교', {'income': {'kind': inc['kind'], 'v': inc['v']}, 'age': ctx['age'],
                                    'combos': combos}),
               _handoff('상담 연결', '연말 세액공제 납입 상담')]
    title = f'올해 세액공제 {won(more)} 더 받을 수 있어요'
    return [_alert(p, today, 'year_end_credit', '높음' if today.month == 12 else '중간', title, msg, figures,
                   ['ded_limits', 'ded_rates', 'ded_cap_tax'], end, '납입 마감', actions,
                   detail=detail, assumptions=assume)]


# ---------------------------------------------------------------------------
# 2. ISA 만기 전환 — R-03 (doc4·doc23)
# ---------------------------------------------------------------------------
def _isa(p, today, ctx):
    isa = p.get('isa') or {}
    if not isa.get('maturity') or not isa.get('amount'):
        return []
    mat = _date(isa['maturity'])
    end = mat + timedelta(days=ISA_DAYS)            # 초일 불산입 — 만기 다음 날이 1일째
    if not (mat - timedelta(days=ISA_DAYS) <= today <= end):
        return []
    x = float(isa['amount'])
    extra = isa_extra(x)
    inc = p.get('income')
    keys = ['isa_extra', 'isa_formula', 'isa_to_savings', 'isa_to_irp', 'isa_separate']
    figures = {'ISA 만기일': _kdate(mat, today), '전환 마감': f'{_kdate(end, today)} ({_dday(end, today)})',
               '옮길 수 있는 금액': won(x), '추가 공제 대상': won(extra)}
    needs = []
    if inc:
        r = credit_rate(inc['v'], inc['kind'])
        refund = extra * r / 100
        keys.append('ded_rates')
        figures.update({'세액공제율': pct(r), '더 돌려받을 금액': won(refund)})
        settle = '연말정산' if inc['kind'] == '총급여' else '종합소득세 신고'
        gain = f'공제율 {pct(r)}면 {won(refund)}을 더 돌려받습니다(옮긴 해 {settle})'
        title = f'ISA 만기금 옮기면 {won(refund)} 더 공제'
    else:
        lo, hi = credit_rate(_BIG), credit_rate(0)
        gain = f'공제율 {pct(hi)}면 {won(extra * hi / 100)}, {pct(lo)}면 {won(extra * lo / 100)}을 더 돌려받습니다'
        needs.append('소득(총급여 또는 종합소득금액) — 공제율을 정하는 데 필요')
        title = f'ISA 만기금 옮기면 공제 대상 {won(extra)} 늘어요'
    when_txt = f'만기({_kdate(mat, today)})에 받는 {won(x)}을' if today <= mat else f'만기된 ISA {won(x)}을'
    msg = (f'{when_txt} {_kdate(end, today)}까지 연금계좌로 옮기면 세액공제 대상이 {won(extra)} 늘어납니다. '
           f'{gain}. 옮긴 돈은 연 1,800만 원 납입한도와 별도로 넣을 수 있습니다.')
    figures['전환 연도 공제 대상 한도'] = (f'연금저축으로 옮기면 {won(CAP_SAVINGS + extra)} · '
                                   f'IRP로 옮기면 {won(CAP_ALL + extra)}')
    detail = [f'추가 공제 대상 = min(옮긴 금액 {won(x)} × 10%, 300만 원) = {won(extra)} (R-03)',
              f'옮긴 돈은 그해 남은 일반 공제 한도도 채운다 — 전환 연도 공제 대상 한도: 연금저축 {won(CAP_SAVINGS)} + '
              f'{won(extra)}, IRP 포함 {won(CAP_ALL)} + {won(extra)} (doc4 산식)',
              f'기한: ISA 만기일로부터 {ISA_DAYS}일(만기일 다음 날부터 셈) = {end.isoformat()} — 지나면 전환 불가(doc4)']
    if inc:
        r = credit_rate(inc['v'], inc['kind'])
        detail.append(f'더 돌려받을 금액 = {won(extra)} × {pct(r)} (R-02, {inc["kind"]} {won(inc["v"])})')
        # 상담 질문(taxcalc)은 옮긴 해 공제 대상 전체(일반 한도분 + 추가분)로 답한다 — 같은 숫자를 여기서도 보여 준다
        s_all, i_all = (CAP_SAVINGS + extra) * r / 100, (CAP_ALL + extra) * r / 100
        figures['옮긴 해 세액공제(일반 한도까지 채울 때)'] = (f'연금저축으로 옮기면 {won(s_all)} · '
                                                  f'IRP로 옮기면 {won(i_all)}')
        detail.append(f'옮긴 해 세액공제(일반 한도까지 채울 때): 연금저축 {won(CAP_SAVINGS + extra)} × {pct(r)} = {won(s_all)}, '
                      f'IRP 포함 {won(CAP_ALL + extra)} × {pct(r)} = {won(i_all)} — 그해 다른 납입으로 이미 채운 몫은 '
                      '연말 세액공제 알림과 겹친다')
    assume = ['만기 지급액 전부를 옮긴다고 봄', '옮긴 해의 소득이 지금과 같다고 봄(공제율)',
              '이 ISA 만기금으로 추가 공제를 받은 적 없다고 봄 — 해를 나눠 넣어도 인당 최대 300만 원(doc4)']
    q = (f'{inc["kind"]} {won(inc["v"])}인데 ' if inc else '') + f'ISA 만기 {won(x)}을 연금계좌로 옮기면 세액공제 얼마나 더 받아요?'
    actions = [_chat('옮기면 얼마나 더?', q), _handoff('전환 절차 상담', 'ISA 만기 연금계좌 전환')]
    sev = '높음' if today > mat or (end - today).days <= 14 else '중간'
    return [_alert(p, today, 'isa_maturity', sev, title, msg, figures, keys, end, '전환 마감', actions,
                   detail=detail, assumptions=assume, needs=needs, extra=['isa_60'])]


# ---------------------------------------------------------------------------
# 3. 만 55세 도래 — R-04·R-05 (doc39)
# ---------------------------------------------------------------------------
def _age55(p, today, ctx):
    birth = ctx['birth']
    if birth is None:
        return []
    d55 = _add_years(birth, PENSION_AGE)
    if not (d55.year == today.year or today <= d55 <= _add_months(today, 3)):
        return []
    accts = [(i, a) for i, a in enumerate(p.get('accounts', [])) if a.get('type') in PENSION_TYPES and a.get('opened')]
    plan = p.get('retire_plan') or {}
    if not accts and not plan:
        return []
    keys = ['pension_req', 'limit_formula', 'limit_year1', 'year_count', 'limit_rates']
    r55 = age_rate(PENSION_AGE)
    figures = {'만 55세 되는 날': f'{_kdate(d55, today)} ({_dday(d55, today)})'}
    detail, lims, later, assume = [], [], [], []
    for i, a in accts:
        name = _acct_name(p, i)
        s = start_date(a, birth)
        n1 = year_n(a, birth, s.year)
        old = _date(a['opened']) < OLD_CUT
        if old:
            keys.append('year6')
        if s > d55:
            why = f'가입 {MIN_YEARS}년이 되는 ' if not _has_retire(a) else ''
            later.append(f'{name}은 {why}{_kdate(s, today)}부터')
            figures[f'{name} 개시 가능일'] = _kdate(s, today)
            continue
        if a.get('balance') is not None:
            lim = pension_limit(a['balance'], n1)
            lims.append(f'{name} {won(lim)}')
            figures[f'{name} 첫해 연금수령한도'] = won(lim) + (f' ({n1}년차)' if n1 != 1 else '')
            detail.append(f'{name}: {_date(a["opened"]).isoformat()} 가입'
                          + (f' — 2013년 3월 1일 전 가입이라 첫해가 {n1}년차' if old else ' — 첫해 1년차')
                          + f', 연금수령한도 = {won(a["balance"])} ÷ (11 − {n1}) × 120% = {won(lim)} (R-05)')
    if lims:
        assume.append('연금 개시일 평가액이 지금 평가액과 같다고 봄')
    names = '·'.join(dict.fromkeys(a['type'] for _, a in accts if start_date(a, birth) <= d55))
    past = d55 < today
    tax = f'연금수령한도 안에서 받으면 연금소득세 {pct(r55)}가 붙습니다.'
    if names:
        s1 = (f'{_kdate(d55, today)} 만 55세가 되어 {names}에서 연금을 받을 수 있' + ('습니다.' if past else '게 됩니다.'))
        s2 = f'그해 연금수령한도는 {", ".join(lims)}이고, 한도 안에서 받으면 연금소득세 {pct(r55)}가 붙습니다.' if lims else tax
        if later:
            s2 = s2[:-1] + f'({", ".join(later)}).'
    elif later:
        # 계좌는 있지만 55세 생일에 가입 5년이 안 됨 — '받을 수 있게 됩니다'라고 하면 틀린 안내다(R-04)
        s1 = (f'{_kdate(d55, today)} 만 55세가 ' + ('됐지만' if past else '되지만')
              + f', 지금 연금계좌는 가입 {MIN_YEARS}년이 지나야 연금으로 받을 수 있습니다({", ".join(later)}).')
        s2 = '그 전에 꺼내면 연금수령으로 인정되지 않습니다.'
    else:
        # 연금계좌 없이 퇴직 예정만 있는 경우
        s1 = f'{_kdate(d55, today)} 만 55세가 되어 연금계좌의 돈을 연금으로 받을 수 있는 나이가 ' + ('됐습니다.' if past else '됩니다.')
        s2 = tax
    s3 = ''
    if plan.get('date'):
        keys.append('no_period_req')
        rd = _date(plan['date'])
        amt = f'(예상 {won(plan["amount"])})' if plan.get('amount') else ''
        s3 = f' {_kdate(rd, today)} 퇴직 때 받는 퇴직금{amt}을 IRP로 옮기면 가입기간 {MIN_YEARS}년 요건 없이 연금으로 받을 수 있습니다.'
        figures['퇴직 예정일'] = _kdate(rd, today)
        if plan.get('amount'):
            figures['예상 퇴직급여'] = won(plan['amount'])
        olds = [_acct_name(p, i) for i, a in accts if _date(a['opened']) < OLD_CUT]
        if olds:
            # 비교 사실만 적는다(어느 쪽으로 옮길지는 고객이 고른다) — doc39 '6년차 특례'
            detail.append(f'{"·".join(olds)}은 2013년 3월 1일 전에 가입해 {OLD_START}년차부터 센다 — 퇴직금을 이 계좌로 옮길 때와 '
                          '새 IRP로 옮길 때 첫해 연금수령한도가 다르다. 2013년 3월 1일 이후 DC 가입자는 옛 계좌로 옮길 수 없다(doc39)')
    figures[f'연금소득세율(만 {PENSION_AGE}~69세)'] = pct(r55)
    detail.append('연금수령연차는 실제로 받지 않아도 개시 가능한 해부터 해마다 늘어난다 — 늦게 받을수록 한도가 커진다(R-05)')
    actions = [_chat('55세부터 어떻게 받나요?', '만 55세가 되면 연금수령한도와 연금 세금은 어떻게 돼요?'),
               _handoff('퇴직금 IRP 이전 상담' if plan else '연금 개시 상담', '만 55세 연금 개시')]
    if names:
        title = f'{_kdate(d55, today)}부터 연금을 받을 수 있어요' if not past else '올해부터 연금을 받을 수 있어요'
    else:
        title = f'{_kdate(d55, today)} 만 55세가 돼요' if not past else '올해 만 55세가 됐어요'
    return [_alert(p, today, 'age55', '중간', title, s1 + ' ' + s2 + s3, figures, list(dict.fromkeys(keys)), d55,
                   '만 55세 생일', actions, detail=detail, assumptions=assume)]


# ---------------------------------------------------------------------------
# 올해 인출 계획 — over_limit_plan·over_1500·rate_step 이 함께 쓴다
# ---------------------------------------------------------------------------
def _plans(p, today, ctx):
    """올해 인출 계획이 있고 재원·평가액을 아는 연금계좌마다 dict(올해 계산 결과 포함)."""
    out = []
    birth, age = ctx['birth'], ctx['age']
    if birth is None:
        return out
    for i, a in enumerate(p.get('accounts', [])):
        if a.get('type') not in PENSION_TYPES or not a.get('plan_this_year') or a.get('funds') is None \
                or a.get('balance') is None:
            continue
        n = year_n(a, birth, today.year)
        if n is None:
            continue                      # 아직 개시 전 — 55세 전 인출은 다른 규칙(연금외수령)
        m = int(a.get('paid_years') or 0) + 1
        f = _funds(a)
        B, w = float(a['balance']), float(a['plan_this_year'])
        notes = []
        # 재원 합계와 평가액이 다르면 — 평가액 중 재원 목록에 없는 돈은 운용수익(③ 재원, doc38·doc39)으로 본다.
        # 그냥 두면 인출이 재원에서 다 빠지지 않아(잔액 부족) 세금이 실제보다 작게 나온다.
        gap = B - sum(f.values())
        if gap > 1e-6:
            f['credit'] += gap
            notes.append(f'평가액 중 재원 목록에 없는 {won(gap)}은 운용수익(세액공제 받은 돈과 같은 ③ 재원)으로 봄')
        elif gap < -1e-6:
            if f['credit'] + gap < 0:
                continue                  # 재원 합계가 평가액보다 훨씬 커서 어느 재원이 줄었는지 모른다
            f['credit'] += gap
            notes.append(f'재원 합계가 평가액보다 {won(-gap)} 많아 그만큼을 운용손실로 보고 ③ 재원에서 뺌')
        if w > B:
            notes.append(f'올해 인출 계획 {won(w)}이 평가액 {won(B)}보다 많아 평가액까지만 계산')
            w = B
        s = start_date(a, birth)
        age_w = age
        if s > today:
            notes.append(f'연금 개시가 가능해지는 {s.isoformat()} 뒤에 인출한다고 봄(그 전 인출은 연금외수령)')
            age_w = max(age, PENSION_AGE)     # 세율은 받는 날 나이 — 그날은 이미 만 55세 이상(R-07)
        if a.get('paid_years') is None and f['retire']:
            notes.append('연금을 실제로 받은 해 수가 프로필에 없어 올해를 연금 실제수령 1년차로 봄')
        res = _wd(a, w, f, B, n, m, age_w)
        out.append({'i': i, 'acct': a, 'name': _acct_name(p, i), 'n': n, 'm': m, 'funds': f, 'res': res,
                    'w': w, 'B': B, 'notes': notes, 'age': age_w})
    return out


def _annuity_tax(x, age):
    """③ 세액공제 받은 돈·수익 x 를 한 해에 연금수령(한도 안)할 때 세금 — withdraw_tax 그대로
    (R-07 나이별 세율, R-08 연 1,500만 원 초과면 전액 16.5%). 55세 전 나이로 1,500만 원 이하면 None."""
    return withdraw_tax(x, credit=x, age=age)['total']


def _retire_ratio(m):
    """연금실제수령연차 m 년차에 원래 퇴직소득세 중 내는 비율(R-09: 0.7/0.6/0.5).
    taxcalc 의 감면 표를 여기 다시 쓰지 않으려고 withdraw_tax 에 퇴직금 1·실효세율 100% 를 넣어 읽는다."""
    return round(withdraw_tax(1.0, retire=1.0, eff=100.0, actual_n=m)['total'], 6)


def _credit_in(plans):
    """③ 세액공제 받은 돈·수익 중 한도 안(연금수령) 금액 합계 — 1,500만 원 판단 대상(R-08)."""
    return sum(inside for pl in plans for k, _, inside, _ in pl['res']['rows'] if k == 'credit')


def _fund_q(f):
    names = {'free': '세액공제 안 받은 돈', 'retire': '퇴직금', 'credit': '세액공제 받은 돈과 수익'}
    return ', '.join(f'{names[k]} {won(v)}' for k, v in f.items() if v)


# ---------------------------------------------------------------------------
# 4. 인출 계획이 연금수령한도 초과 — R-05·R-06·R-09·R-10 (doc39)
# ---------------------------------------------------------------------------
def _over_limit(p, today, ctx):
    out = []
    for pl in ctx['plans']:
        a, lump, w, B, n, m, f = pl['acct'], pl['res'], pl['w'], pl['B'], pl['n'], pl['m'], pl['funds']
        age = pl['age']
        lim = lump['limit']
        if lim is None or w <= lim:
            continue
        this = _wd(a, lim, f, B, n, m, age)
        nxt = _wd(a, w - lim, _left(f, this), B - lim, n + 1, m + 1, age + 1 if age else None)
        over_kinds = [k for k, _, _, o in lump['rows'] if o > 0]
        keys = ['limit_formula', 'order']
        if n == 1:
            keys.append('limit_year1')
        if _date(a['opened']) < OLD_CUT:
            keys.append('year6')
        if f['free']:
            keys.append('free')
        if any(k == 'retire' for k, *_ in lump['rows']):
            keys += ['retire_in', 'retire_years', 'retire_over']
        if 'credit' in over_kinds:
            keys.append('over_limit')
        name = pl['name']
        end = date(today.year, 12, 31)
        figures = {'올해 인출 계획': won(w), '올해 연금수령한도': won(lim), '한도 초과': won(w - lim)}
        detail = [f'연금수령한도 = {won(B)} ÷ (11 − {n}) × 120% = {won(lim)} (R-05, 올해 {n}년차)']
        detail += [f'[한꺼번에 {won(w)}] ' + x for x in lump['lines']]
        detail += [f'[나눠 받기 · 올해 {won(lim)}] ' + x for x in this['lines']]
        detail += [f'[나눠 받기 · 내년 {won(w - lim)}] ' + x for x in nxt['lines']]
        over_txt = []
        if 'retire' in over_kinds:
            over_txt.append('한도를 넘긴 퇴직금은 감면 없이 퇴직소득세를 그대로 냅니다')
        if 'credit' in over_kinds:
            over_txt.append(f'한도를 넘긴 세액공제 받은 돈·수익에는 {pct(SEP_RATE)} 기타소득세가 붙습니다')
        tail = (' ' + ', '.join(over_txt) + '.') if over_txt else ''
        needs = []
        t1, ta, tb = lump['total'], this['total'], nxt['total']
        # 한도를 넘는 몫이 모두 ① 세액공제 안 받은 돈이면 한도를 넘어도 과세가 없다(R-06 원문) — 급한 알림이 아니다
        sev = '높음' if [k for k in over_kinds if k != 'free'] else '참고'
        if sev == '참고':
            msg = (f'올해 계획한 {won(w)}은 올해 연금수령한도 {won(lim)}보다 {won(w - lim)} 많습니다. '
                   f'다만 한도를 넘는 몫까지 모두 세액공제 안 받은 돈에서 나가, 한도를 넘어도 세금이 붙지 않습니다.')
            title = f'올해 인출 계획이 한도를 {won(w - lim)} 넘지만 세금은 같아요'
            if t1 is not None:
                figures['한꺼번에 받으면'] = won(t1)
        elif t1 is not None and ta is not None and tb is not None:
            t2 = ta + tb
            figures.update({'한꺼번에 받으면': won(t1), '나눠 받으면(두 해 합계)': won(t2), '나눠 받기 올해분': won(ta),
                            '나눠 받기 내년분': won(tb), '차이': won(abs(t1 - t2))})
            msg = (f'올해 계획한 {won(w)}은 올해 연금수령한도 {won(lim)}보다 {won(w - lim)} 많습니다. '
                   f'한꺼번에 받으면 세금이 {won(t1)}, 올해 {won(lim)}·내년 {won(w - lim)}으로 나눠 받으면 '
                   f'두 해 합계 {won(t2)}입니다(차이 {won(abs(t1 - t2))}).' + tail)
            title = f'올해 인출 계획이 한도를 {won(w - lim)} 넘어요'
        else:
            if f['retire'] and a.get('retire_eff') is None:
                needs.append('퇴직소득세 실효세율(퇴직소득 원천징수영수증) — 퇴직금 재원 세금 계산에 필요')
            if age_rate(age) is None:
                needs.append('연금을 받는 때의 나이(만 55세 이상인지) — 연금소득세율을 정하는 데 필요')
            msg = (f'올해 계획한 {won(w)}은 올해 연금수령한도 {won(lim)}보다 {won(w - lim)} 많습니다. '
                   f'한도를 넘긴 부분은 연금외수령이라 세금이 달라집니다.' + tail)
            title = f'올해 인출 계획이 한도를 {won(w - lim)} 넘어요'
        s = start_date(a, ctx['birth'])
        assume = [f'올해가 연금수령연차 {n}년차({s.isoformat()}부터 연금 개시 가능'
                  + (', 2013년 3월 1일 전 가입이라 6년차부터 셈' if _date(a['opened']) < OLD_CUT else '') + ')',
                  '나눠 받을 때 내년 평가액은 수익률 0%로 봄(올해 인출만 뺌)', '내년에는 이 나머지만 받는다고 봄']
        if f['retire'] and a.get('paid_years') is not None:
            assume.insert(1, f'연금 실제수령연차 {m}년차(올해 전 {m - 1}년 수령)')
        assume += pl['notes']
        # withdraw_tax 는 계좌 하나만 본다 — 다른 계좌 ③ 재원과 합쳐 1,500만 원을 넘으면 세금 방식이 바뀐다(R-08)
        ci_all = _credit_in(ctx['plans'])
        ci_this = sum(i_ for k, _, i_, _ in lump['rows'] if k == 'credit')
        if ci_this and ci_all > SEP_CAP and ci_all > ci_this:
            assume.append(f'이 계산은 이 계좌만 본 값 — 다른 계좌와 합친 ③ 재원 연금수령이 연 {won(ci_all)}로 '
                          f'{won(SEP_CAP)}을 넘어 실제로는 전액 {pct(SEP_RATE)} 분리과세 또는 종합과세(연 1,500만 원 초과 알림)')
        q = (f'{age}세인데 {a["type"]} {won(B)}({_fund_q(f)})이 있고 연금수령연차 {n}년차예요. '
             + (f'퇴직소득세 실효세율 {a["retire_eff"]:g}%예요. ' if a.get('retire_eff') is not None and f['retire'] else '')
             + (f'실제 수령 {m}년차예요. ' if f['retire'] and m > 1 else '')
             + f'올해 {won(w)}을 인출하면 세금은요?')
        actions = [_chat('한꺼번에 받으면 세금은?', q),
                   _diy('연도별 수령 설계', {'age': age, 'payout': {
                       'start_age': age, 'years': 10, 'balance': B, 'free': f['free'], 'retire': f['retire'],
                       'credit': f['credit'], 'eff': a.get('retire_eff'), 'return_rate': 0.0,
                       # 올해 연차 그대로 넘긴다 — 빼면 DIY 가 1년차로 보고 한도·감면율이 알림과 달라진다
                       'year_n_start': min(n, 30), 'actual_n_start': min(m, 60)}}),
                   _handoff('상담 연결', '연금수령한도 초과 인출 상담')]
        out.append(_alert(p, today, 'over_limit_plan', sev, title, msg, figures, list(dict.fromkeys(keys)), end,
                          '올해 인출', actions, key=str(pl['i']), detail=detail, assumptions=assume, needs=needs))
    return out


# ---------------------------------------------------------------------------
# 5. ③ 재원 연금수령 연 1,500만 원 초과 — R-08 (doc38)
# ---------------------------------------------------------------------------
def _over_1500(p, today, ctx):
    ci = _credit_in(ctx['plans'])
    if ci <= SEP_CAP:
        return []
    age = max(pl['age'] for pl in ctx['plans'])   # 받는 날 나이(개시 전이면 만 55세로 본 값)
    r = age_rate(age)
    t_sep = _annuity_tax(ci, age)                 # 1,500만 원 초과 → 전액 16.5%(taxcalc)
    end = date(today.year, 12, 31)
    figures = {'올해 ③ 재원 연금수령 합계': won(ci), '기준': f'연 {won(SEP_CAP)} (전 금융기관 합산)',
               f'{pct(SEP_RATE)} 분리과세를 고르면': won(t_sep), '종합과세를 고르면': '다른 소득과 합산해 6.6~49.5%'}
    s3 = ''
    if r:
        t_cap = _annuity_tax(SEP_CAP, age)        # 1,500만 원 이하 → 나이별 세율(taxcalc)
        figures[f'{won(SEP_CAP)} 이하로 받을 때 세율'] = pct(r)
        figures[f'{won(SEP_CAP)}을 받을 때 세금'] = won(t_cap)
        s3 = f' {won(SEP_CAP)} 이하로 받으면 전액 {pct(r)}만 붙어, {won(SEP_CAP)}일 때 {won(t_cap)}입니다.'
    msg = (f'올해 세액공제 받은 돈·수익에서 받을 연금이 {won(ci)}으로 {won(SEP_CAP)}을 넘습니다. '
           f'넘은 부분이 아니라 전액({won(ci)})이 종합과세 또는 {pct(SEP_RATE)} 분리과세 대상이고, '
           f'{pct(SEP_RATE)}를 고르면 {won(t_sep)}입니다.' + s3)
    detail = []
    for pl in ctx['plans']:
        rows = {k: (t, i_, o) for k, t, i_, o in pl['res']['rows']}
        part = ', '.join(f'{FUND_SHORT[k]} {won(t)}' for k, (t, _, _) in rows.items())
        cin = rows.get('credit', (0, 0, 0))[1]
        detail.append(f'{pl["name"]}: 인출 {won(pl["w"])} = {part} → 1,500만 원 판단에 드는 몫 {won(cin)}')
    detail += ['세액공제 안 받은 돈과 퇴직금 재원은 1,500만 원 판단에 들어가지 않는다(R-08 원문)',
               '다른 소득이 적어 합산 세율이 16.5%보다 낮으면 종합과세 쪽 세금이 더 적을 수 있다(R-08 원문)']
    assume = ['프로필에 있는 연금계좌만 합산(다른 금융기관 연금계좌 없음 — 기준은 전 금융기관 합산)',
              '올해 계획대로 인출한다고 봄'] + [x for pl in ctx['plans'] for x in pl['notes']]
    # ③ 재원이 IRP 에서 나올 수도 있어 계좌 이름 대신 '연금계좌'로 묻는다(taxcalc 가 같은 금액으로 계산)
    actions = [_chat('1,500만 원 넘으면 세금은?', f'{age}세인데 연금계좌에서 연 {won(ci)} 받으면 세금은요?'),
               _handoff('세무 상담 연결', '사적연금 1,500만 원 초과 — 분리과세·종합과세 선택')]
    return [_alert(p, today, 'over_1500', '높음', f'올해 연금이 {won(SEP_CAP)}을 넘어 전액 과세 방식이 바뀌어요', msg,
                   figures, ['over_1500', 'whole_amount', 'progressive', 'excluded', 'all_accounts', 'not_always',
                             'rate_table'], end, '올해 말', actions, detail=detail, assumptions=assume)]


# ---------------------------------------------------------------------------
# 6. 70·80세 세율 변화 — R-07 (doc38)
# ---------------------------------------------------------------------------
def _rate_step(p, today, ctx):
    birth, age = ctx['birth'], ctx['age']
    if birth is None or age is None or age < PENSION_AGE:
        return []
    # 연금을 받고 있거나 받을 계획이 있고, 세액공제 받은 돈·수익이 있을 수 있는 계좌가 있어야 한다
    paying = [a for a in p.get('accounts', []) if a.get('type') in PENSION_TYPES
              and (a.get('plan_this_year') or a.get('paid_years'))
              and (a.get('funds') is None or (a.get('funds') or {}).get('credit', 0) > 0)]
    if not paying:
        return []
    end = date(today.year + 1, 12, 31)
    out = []
    for a_ in range(age + 1, age + 3):
        d = _add_years(birth, a_)
        r0, r1 = age_rate(a_ - 1), age_rate(a_)
        if d <= today or d > end or not (r0 and r1 and r1 < r0):
            continue
        ci = _credit_in(ctx['plans'])
        ex = ci if 0 < ci <= SEP_CAP else SEP_CAP
        t0, t1 = _annuity_tax(ex, a_ - 1), _annuity_tax(ex, a_)     # 1,500만 원 이하 → 나이별 세율(taxcalc)
        figures = {'세율 바뀌는 날': f'{_kdate(d, today)} ({_dday(d, today)})', '지금 세율': pct(r0), '바뀐 뒤 세율': pct(r1),
                   f'{won(ex)} 받을 때 세금': f'{won(t0)} → {won(t1)}'}
        s2 = (f'세율은 연금을 받는 날의 나이로 정해져, {won(ex)}을 받으면 세금이 {won(t0)}에서 {won(t1)}으로 줄어듭니다.')
        s3 = ''
        if ci > SEP_CAP:
            s3 = (f' 다만 올해처럼 연 {won(SEP_CAP)}을 넘게 받으면 이 세율 대신 전액 {pct(SEP_RATE)} 분리과세 또는 '
                  f'종합과세입니다.')
        msg = f'{_kdate(d, today)} 만 {a_}세가 되면 그 뒤에 받는 연금의 연금소득세율이 {pct(r0)}에서 {pct(r1)}로 내려갑니다. ' + s2 + s3
        detail = [f'연금소득세율(연금수령일 현재 나이): 만 {a_ - 1}세 {pct(r0)} → 만 {a_}세 {pct(r1)} (R-07)',
                  f'예: {won(ex)} × {pct(r0)} = {won(t0)}, {won(ex)} × {pct(r1)} = {won(t1)}']
        assume = [f'세액공제 받은 돈·수익을 연 {won(SEP_CAP)} 이하로 연금수령하는 경우의 세율'] + \
                 ([f'예시 금액은 올해 계획한 ③ 재원 연금수령액 {won(ci)}'] if 0 < ci <= SEP_CAP else
                  [f'예시 금액은 기준선 {won(SEP_CAP)}'])
        actions = [_chat('나이에 따라 세율이?', f'만 {a_}세가 되면 연금소득세율이 어떻게 바뀌어요?')]
        out.append(_alert(p, today, 'rate_step', '참고', f'{_kdate(d, today)}부터 연금소득세율 {pct(r1)}', msg, figures,
                          ['limit_rates', 'rate_table'], d, f'만 {a_}세 생일', actions, key=str(a_),
                          detail=detail, assumptions=assume))
    return out


# ---------------------------------------------------------------------------
# 7. DC·IRP 위험자산 70% — R-14 (doc14)
# ---------------------------------------------------------------------------
def _risk(p, today, ctx):
    out = []
    for i, a in enumerate(p.get('accounts', [])):
        x = a.get('risky_pct')
        if a.get('type') not in ('DC', 'IRP') or x is None or x < RISK_NEAR:
            continue
        name = _acct_name(p, i)
        B = a.get('balance')
        figures = {'위험자산 비중': f'{x:g}%', '한도': f'{RISK_CAP:g}%'}
        if x > RISK_CAP:
            sev = '높음'
            title = f'{name} 위험자산 {x:g}% — {RISK_CAP:g}% 한도 넘음'
            s2 = ''
            if B is not None:
                figures['적립금'] = won(B)
                figures['한도를 넘은 금액(대략)'] = won(B * (x - RISK_CAP) / 100)
                s2 = f' 적립금 {won(B)} 기준으로 약 {won(B * (x - RISK_CAP) / 100)}이 한도 밖입니다.'
            msg = (f'{name}의 위험자산 비중이 {x:g}%로 한도({RISK_CAP:g}%)를 넘었습니다.{s2} '
                   f'주식형 펀드·ETF 같은 위험자산은 전체 적립금의 {RISK_CAP:g}% 이내로 제한되니, '
                   f'운용 지시를 바꾸기 전에 비중을 확인해 보세요.')
        else:
            sev = '참고'
            title = f'{name} 위험자산 {x:g}% — {RISK_CAP:g}% 한도에 가까워요'
            s2 = ''
            if B is not None:
                room = B * (RISK_CAP - x) / 100
                figures['적립금'] = won(B)
                figures['한도까지 여유(대략)'] = won(room)
                s2 = f' 적립금 {won(B)} 기준으로 위험자산을 약 {won(room)} 더 담으면 한도에 닿습니다.'
            msg = (f'{name}의 위험자산 비중이 {x:g}%로 한도({RISK_CAP:g}%)에 가깝습니다.{s2} '
                   f'주식형 펀드·ETF 같은 위험자산은 전체 적립금의 {RISK_CAP:g}% 이내로 제한됩니다.')
        actions = [_chat('위험자산 한도는?', f'{a["type"]} 위험자산 {RISK_CAP:g}% 한도는 어떻게 적용돼요?'),
                   _handoff('운용 상담 연결', f'{name} 위험자산 비중 {x:g}%')]
        out.append(_alert(p, today, 'risk_limit', sev, title, msg, figures, ['risk_limit'], today, '지금', actions,
                          key=str(i), detail=[f'{name} 위험자산 비중 {x:g}% / 한도 {RISK_CAP:g}% (R-14)',
                                              '조건을 충족한 TDF는 DC·IRP 적립금의 100%까지 담을 수 있다(doc53)'],
                          assumptions=['위험자산 비중은 프로필 값(최근 평가 기준) — 조건을 충족한 TDF는 빼고 센 값이라고 봄'],
                          extra=['tdf_100']))
    return out


# ---------------------------------------------------------------------------
# 8. 연금실제수령연차 11년차(21년차) — R-09 (doc40·doc39)
# ---------------------------------------------------------------------------
def _actual_year(p, today, ctx):
    out = []
    for i, a in enumerate(p.get('accounts', [])):
        if a.get('type') not in PENSION_TYPES or not _has_retire(a) or a.get('paid_years') is None:
            continue
        paid = int(a['paid_years'])
        receiving = bool(a.get('plan_this_year'))      # 1만 원이라도 받아야 그해 실제수령연차가 쌓인다(doc40)
        m_next = paid + (1 if receiving else 0) + 1
        k0, k1 = _retire_ratio(m_next - 1), _retire_ratio(m_next)
        if not k1 < k0:
            continue                      # 감면율이 그대로면 알릴 것이 없다
        pl = next((x for x in ctx['plans'] if x['i'] == i), None)
        # 내년에 남는 퇴직금 — 올해 계획으로 퇴직금이 다 빠지면 감면이 커져도 쓸 데가 없다
        retire_left = _left(pl['funds'], pl['res'])['retire'] if pl else _funds(a)['retire']
        if retire_left <= 0:
            continue
        p0, p1 = round((1 - k0) * 100), round((1 - k1) * 100)
        name = _acct_name(p, i)
        nd = date(today.year + 1, 1, 1)
        figures = {'내년 실제수령연차': f'{m_next}년차', '퇴직소득세 감면': f'{p0}% → {p1}%'}
        s2, detail, needs, assume = '', [f'연금실제수령연차 {m_next - 1}년차 → {m_next}년차: 퇴직소득세 감면 {p0}% → {p1}% (R-09)',
                                         '실제수령연차는 연금을 실제로 받은 해만 센다 — 한도를 정하는 연금수령연차와 다르다(doc40)'], [], []
        eff = a.get('retire_eff')
        if pl and eff is not None:
            # 감면율 차이만 보이도록 같은 퇴직금 x 를 한도 안에서 받을 때 세금을 올해·내년 연차로 나란히 낸다
            # (재원이 섞이거나 한도를 넘는 효과는 '연금수령한도 초과' 알림이 따로 다룬다)
            x = min(pl['w'], retire_left)
            r0 = withdraw_tax(x, retire=x, eff=eff, actual_n=m_next - 1)
            r1 = withdraw_tax(x, retire=x, eff=eff, actual_n=m_next)
            t0, t1 = r0['total'], r1['total']
            figures[f'퇴직금 {won(x)} 받을 때 세금'] = f'{won(t0)} → {won(t1)}'
            s2 = (f' 퇴직금 {won(x)}을 연금수령한도 안에서 받으면 세금이 {won(t0)}에서 {won(t1)}으로 줄어듭니다'
                  f'(퇴직소득세 실효세율 {eff:g}% 기준).')
            detail += [f'[올해 {m_next - 1}년차] ' + y for y in r0['lines']]
            detail += [f'[내년 {m_next}년차] ' + y for y in r1['lines']]
            assume = ['연금수령한도 안에서 받는다고 봄',
                      f'예시 금액은 올해 인출 계획 중 내년에도 남는 퇴직금 범위({won(x)})']
        elif eff is None:
            needs.append('퇴직소득세 실효세율 — 금액 비교에 필요')
        msg = (f'내년이면 {name}에서 연금을 실제로 받은 지 {m_next}년째가 되어, 퇴직금 재원 연금의 퇴직소득세 감면이 '
               f'{p0}%에서 {p1}%로 커집니다.' + s2)
        actions = [_chat('감면이 어떻게 바뀌나요?', f'퇴직금 재원 연금을 실제로 {m_next}년째 받으면 퇴직소득세 감면이 어떻게 돼요?'),
                   _handoff('상담 연결', f'{name} 연금실제수령연차 {m_next}년차')]
        kind = f'actual_year_{m_next}'          # 감면율이 바뀌는 해(11·21년차)만 여기까지 온다
        out.append(_alert(p, today, kind, '참고', f'내년부터 퇴직소득세 감면 {p1}%로 커져요', msg, figures,
                          ['actual_year', 'retire_years', 'retire_in'], nd, '내년 1월', actions, key=str(i),
                          detail=detail, assumptions=assume, needs=needs))
    return out


DETECTORS = [_year_end, _isa, _age55, _over_limit, _over_1500, _rate_step, _risk, _actual_year]


# ---------------------------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------------------------
def load_profiles(path=PROFILES_PATH):
    """data/demo_profiles.json 의 가상 고객 목록."""
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)['profiles']


def profile_list(path=PROFILES_PATH):
    """고객 고르기 목록 — [{'id','name','summary'}] (GET /alerts/profiles)."""
    return [{'id': p.get('id'), 'name': p.get('name', p.get('id')), 'summary': p.get('summary', '')}
            for p in load_profiles(path)]


def get_profile(pid, path=PROFILES_PATH):
    return next((p for p in load_profiles(path) if p.get('id') == pid), None)


def _context(p, today):
    birth = _date(p['birth']) if p.get('birth') else None
    ctx = {'birth': birth, 'age': _age(birth, today) if birth else None}
    ctx['plans'] = _plans(p, today, ctx)
    return ctx


def scan(profile, today=None, strict=False):
    """프로필·날짜 → 알림 목록(중요도 순, 같은 중요도는 날짜 빠른 순).
    잘못된 필드는 _clean 이 먼저 뺀다. 기준일 형식이 틀리면 ValueError.
    strict=False 면 알림 하나가 프로필 오류로 깨져도 나머지는 낸다(시연 서버가 멈추지 않게)."""
    t = _today(today)
    p, _ = _clean(profile)
    try:
        ctx = _context(p, t)
    except Exception:
        if strict:
            raise
        return []
    out = []
    for det in DETECTORS:
        try:
            out += det(p, t, ctx)
        except Exception:
            if strict:
                raise
    order = {k: j for j, k in enumerate(KIND_LABEL)}
    out.sort(key=lambda a: (SEVERITY[a['severity']], a['when'], order.get(a['kind'], 99)))
    return out


# ---------------------------------------------------------------------------
# 리포트(마크다운) — HTML 태그 없이 제목·굵게·목록·표만 쓴다
# ---------------------------------------------------------------------------
def _cell(s):
    return str(s).replace('|', '/').replace('\n', ' ')


def _summary_rows(p, today):
    rows = []
    if p.get('birth'):
        b = _date(p['birth'])
        rows.append(('나이', f'만 {_age(b, today)}세 ({b.isoformat()}생)'))
    inc = p.get('income')
    if inc:
        rows.append(('소득', f'{inc["kind"]} {won(inc["v"])} → 세액공제율 {pct(credit_rate(inc["v"], inc["kind"]))} (R-02)'))
    for i, a in enumerate(p.get('accounts', [])):
        bits = []
        if a.get('opened'):
            bits.append(f'{a["opened"]} 가입')
        if a.get('balance') is not None:
            bits.append(f'평가액 {won(a["balance"])}')
        if a.get('funds'):
            bits.append('재원 ' + (', '.join(f'{FUND_SHORT[k]} {won(v)}' for k, v in _funds(a).items() if v) or '0원'))
        if a.get('retire_eff') is not None:
            bits.append(f'퇴직소득세 실효세율 {a["retire_eff"]:g}%')
        if a.get('paid_this_year') is not None:
            bits.append(f'올해 납입 {won(a["paid_this_year"])}')
        if a.get('plan_this_year'):
            bits.append(f'올해 인출 계획 {won(a["plan_this_year"])}')
        if a.get('paid_years') is not None:
            bits.append(f'연금 받은 해 {a["paid_years"]}년')
        if a.get('risky_pct') is not None:
            bits.append(f'위험자산 {a["risky_pct"]:g}%')
        rows.append((_acct_name(p, i), ' · '.join(bits) or '-'))
    isa = p.get('isa')
    if isa:
        rows.append(('ISA', f'만기 {isa.get("maturity", "?")} · 만기 지급액 {won(isa.get("amount", 0))}'))
    rp = p.get('retire_plan')
    if rp:
        rows.append(('퇴직 예정', f'{rp.get("date", "?")}' + (f' · 예상 퇴직급여 {won(rp["amount"])}' if rp.get('amount') else '')))
    return rows


def _action_txt(x):
    kind = {'chat': '상담 질문', 'diy': 'DIY 설계', 'handoff': '상담 연결'}.get(x['kind'], x['kind'])
    if x['kind'] == 'chat':
        return f'{x["label"]} ({kind}: "{x["payload"]}")'
    return f'{x["label"]} ({kind})'


def report(profile, today=None, alerts=None):
    """'이번 달 연금 점검 리포트' 마크다운. 고객 요약 표, 알림 목록, 알림마다 근거(R-xx·문서), 다음 행동.
    프로필 값이 잘못돼도 깨지지 않는다(뺀 필드는 요약 표의 '확인 필요' 줄에)."""
    t = _today(today)
    p, dropped = _clean(profile)
    al = scan(p, t) if alerts is None else alerts
    cnt = {s: sum(1 for a in al if a['severity'] == s) for s in SEVERITY}
    L = [f'# 이번 달 연금 점검 리포트 ({t.year}년 {t.month}월)', '',
         f'**{p.get("name", "고객")}** 님 · 기준일 {t.isoformat()} · 알림 {len(al)}건 '
         f'(높음 {cnt["높음"]} · 중간 {cnt["중간"]} · 참고 {cnt["참고"]})', '']
    L += ['## 한눈에 보기', '']
    if al:
        L += [f'- **[{a["severity"]}] {a["title"]}** — {a["message"].split(". ")[0].rstrip(".")}. '
              + (f'({a["dday"]})' if a['when_label'] == '지금' else f'({a["when_label"]} {a["when"]}, {a["dday"]})')
              for a in al]
    else:
        L.append('- 이번 달은 먼저 알려 드릴 일이 없습니다.')
    L += ['', '## 고객 요약', '', '| 항목 | 내용 |', '| --- | --- |']
    L += [f'| {_cell(k)} | {_cell(v)} |' for k, v in _summary_rows(p, t)]
    if dropped:
        L.append(f'| 확인 필요 | {_cell(", ".join(dropped))} — 값이 비었거나 형식이 맞지 않아 계산에서 뺌 |')
    for k, a in enumerate(al, 1):
        L += ['', f'## {k}. [{a["severity"]}] {a["title"]}', '', a['message'], '',
              '| 항목 | 값 |', '| --- | --- |']
        L += [f'| {_cell(n)} | {_cell(v)} |' for n, v in a['figures'].items()]
        L.append('')
        L += [f'- 계산: {x}' for x in a['detail']]
        L.append(f'- 근거: ' + ' · '.join(f'{r} {a["rule_names"][r]}' for r in a['rule_ids'])
                 + ' — 문서 ' + ', '.join(d.replace('.txt', '') for d in a['docs']))
        q = a['quotes'][0]
        L.append(f'- 원문({q["file"].replace(".txt", "")}, {q["rid"]}): "{q["text"].replace(chr(10), " / ")}"')
        if a['assumptions']:
            L.append('- 가정: ' + ' · '.join(a['assumptions']))
        if a['needs']:
            L.append('- 더 필요한 정보: ' + ' · '.join(a['needs']))
        L.append('- 다음 행동: ' + ' · '.join(_action_txt(x) for x in a['next_actions']))
    if al:
        L += ['', '## 다음 행동', '']
        for a in al:
            L.append(f'- [{a["severity"]}] {a["title"]} ({a["when_label"]} {a["when"]}) — '
                     + ' / '.join(x['label'] for x in a['next_actions']))
    L += ['', '숫자는 문서 원문 규칙(R-xx)으로 계산한 값이며, 가정이 바뀌면 결과도 달라집니다. '
              '여러 선택지는 비교만 보여 드리며, 고르는 것은 고객님입니다.']
    # 화면이 보낸 프로필(이름·계좌 종류)에 '<b>' 같은 글자가 있어도 리포트에 태그가 생기지 않게 꺾쇠를 전각으로 바꾼다
    return ('\n'.join(L) + '\n').replace('<', '＜').replace('>', '＞')


# ---------------------------------------------------------------------------
# outbox — 시연에서 'push' 를 보여 주는 메모리 큐(스레드 안전). 서버를 껐다 켜면 비워진다.
# ---------------------------------------------------------------------------
_OUTBOX = {}
_LOCK = threading.Lock()
OUTBOX_MAX = 100                            # 고객별로 쌓아 두는 최대 개수 — 오래된 것부터 버린다


def push(profile_id, alerts):
    """알림을 고객 큐에 쌓는다. 큐에 이미 있는 같은 id 는 다시 넣지 않는다(알림 dict 가 아니면 건너뜀). → 새로 넣은 개수."""
    if isinstance(alerts, dict):
        alerts = [alerts]
    elif not isinstance(alerts, (list, tuple)):
        alerts = []
    with _LOCK:
        q = _OUTBOX.setdefault(profile_id, [])
        known = {a.get('id') for a in q}
        new = []
        for a in alerts:
            if not isinstance(a, dict) or a.get('id') in known:
                continue
            known.add(a.get('id'))
            new.append(copy.deepcopy(a))
        q.extend(new)
        del q[:-OUTBOX_MAX]
        return len(new)


def pull(profile_id):
    """고객 큐를 꺼내고 비운다."""
    with _LOCK:
        return _OUTBOX.pop(profile_id, [])


def pending(profile_id):
    """꺼내지 않고 개수만."""
    with _LOCK:
        return len(_OUTBOX.get(profile_id, []))


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    day = sys.argv[1] if len(sys.argv) > 1 else None
    for prof in load_profiles():
        print(report(prof, day))
