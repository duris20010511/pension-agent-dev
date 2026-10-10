# -*- coding: utf-8 -*-
"""
'먼저 알려주기'(alerts.py) 회귀 테스트. API 호출 없음.

    python -X utf8 src/test_alerts.py

알림마다 손으로 계산한 값을 고정한다(아래 '손계산' 주석). 숫자는 alerts 가 taxcalc 공용 함수로 내므로,
taxcalc 의 식이 바뀌어 값이 달라지면 여기서 걸린다 — 그때는 문서 원문과 손계산을 다시 확인한다.
"""
import json
import os
import re
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import alerts
import taxcalc
from alerts import load_profiles, pull, push, report, scan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = '2026-10-08'                       # 시연 기준일
BANNED = re.compile(r'추천|최적|유리|가장\s*좋은')
REQUIRED = {'id', 'kind', 'severity', 'title', 'message', 'figures', 'rule_ids', 'docs', 'when', 'next_actions'}
# 계약서의 알림별 근거 — 이 규칙 번호·문서는 반드시 들어 있어야 한다
CONTRACT = {
    'year_end_credit': ({'R-01', 'R-02'}, {'doc41.txt'}),
    'isa_maturity': ({'R-03'}, {'doc4.txt', 'doc23.txt'}),
    'age55': ({'R-04', 'R-05'}, {'doc39.txt'}),
    'over_limit_plan': ({'R-05', 'R-06', 'R-09', 'R-10'}, {'doc39.txt'}),
    'over_1500': ({'R-08'}, {'doc38.txt'}),
    'rate_step': ({'R-07'}, {'doc38.txt'}),
    'risk_limit': ({'R-14'}, {'doc14.txt'}),
    'actual_year_11': ({'R-09'}, {'doc40.txt'}),
}

PROFILES = {p['id']: p for p in load_profiles()}
fail = []
total = 0


def check(cond, msg):
    global total
    total += 1
    if not cond:
        fail.append(msg)


def one(pid, kind, today=TODAY):
    al = [a for a in scan(PROFILES[pid], today, strict=True) if a['kind'] == kind]
    return al[0] if len(al) == 1 else None


def fig(a, name, want):
    got = (a or {}).get('figures', {}).get(name)
    check(got == want, f'[{a and a["kind"]}] {name}: {got!r} ≠ {want!r}')


def doc_text(f):
    with open(os.path.join(ROOT, 'extracted', 'docs', f), encoding='utf-8') as fh:
        return re.sub(r'\s+', ' ', fh.read())


# ---------------------------------------------------------------------------
# 1. 연말 세액공제 (R-01·R-02, doc41)
# ---------------------------------------------------------------------------
def test_year_end():
    # 손계산: 총급여 5,000만 원(≤5,500만 → 16.5%), 연금저축 300만 원 납입, 10월
    #   지금 공제 대상 300 / 합산 한도 900 → 남은 600 = 연금저축 300(600까지) + IRP 300
    #   600만 × 16.5% = 99만 원 더, 다 채우면 900만 × 16.5% = 148만 5천 원
    a = one('young_worker', 'year_end_credit')
    check(a is not None, '[연말공제] 30대 직장인에게 알림이 없음')
    fig(a, '남은 공제 대상', '600만 원')
    fig(a, '그중 연금저축', '300만 원')
    fig(a, '그중 IRP', '300만 원')
    fig(a, '세액공제율', '16.5%')
    fig(a, '더 돌려받을 금액', '99만 원')
    fig(a, '다 채웠을 때 공제액', '148만 5천 원')
    check(a and a['severity'] == '중간' and a['when'] == '2026-12-31', f'[연말공제] 중요도·마감 {a and (a["severity"], a["when"])}')
    check(a and a['message'].startswith('올해 연말정산에서 99만 원을 더 돌려받을 수 있습니다.'), '[연말공제] 결론이 먼저 오지 않음')
    # 다음 행동의 상담 질문을 taxcalc.compute 에 넣으면 '다 채웠을 때' 공제액과 같은 값이 나와야 한다
    q = next(x['payload'] for x in a['next_actions'] if x['kind'] == 'chat')
    r = taxcalc.compute(q)
    check(r and '148만 5천 원' in r['figures'], f'[연말공제] 상담 질문 계산: {r and r["figures"]} / {q}')
    diy = next(x['payload'] for x in a['next_actions'] if x['kind'] == 'diy')
    check([(c['savings'], c['irp']) for c in diy['combos']] == [(300, 0), (600, 300), (300, 600)],
          f'[연말공제] DIY 조합 {diy["combos"]}')
    # 손계산: 총급여 8,000만 원(>5,500만 → 13.2%), 연금저축 400 + IRP 200 = 600 → 남은 300(연금저축 200 + IRP 100)
    #   300만 × 13.2% = 39만 6천 원
    b = one('pre_retiree', 'year_end_credit')
    fig(b, '남은 공제 대상', '300만 원')
    fig(b, '그중 연금저축', '200만 원')
    fig(b, '그중 IRP', '100만 원')
    fig(b, '더 돌려받을 금액', '39만 6천 원')
    # 12월이면 중요도 '높음'
    c = one('young_worker', 'year_end_credit', '2026-12-10')
    check(c and c['severity'] == '높음', '[연말공제] 12월인데 높음이 아님')
    # 9월에는 내지 않는다, 소득이 없으면 내지 않는다
    check(one('young_worker', 'year_end_credit', '2026-09-30') is None, '[연말공제] 9월에 알림이 나옴')
    p = dict(PROFILES['young_worker'])
    p.pop('income')
    check(not [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'year_end_credit'], '[연말공제] 소득 없는데 알림')
    # 이미 다 채웠으면 내지 않는다(연금저축 600 + IRP 300)
    p = json.loads(json.dumps(PROFILES['young_worker']))
    p['accounts'] = [{'type': '연금저축', 'opened': '2023-01-15', 'paid_this_year': 600},
                     {'type': 'IRP', 'opened': '2023-01-15', 'paid_this_year': 300}]
    check(not [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'year_end_credit'], '[연말공제] 한도를 다 채웠는데 알림')
    # 연금저축만 700 넣었으면 연금저축 한도(600) 초과분은 공제 안 됨 → 남은 300 은 IRP 로만
    p['accounts'] = [{'type': '연금저축', 'opened': '2023-01-15', 'paid_this_year': 700}]
    d = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'year_end_credit'][0]
    fig(d, '남은 공제 대상', '300만 원')
    check('그중 연금저축' not in d['figures'] and 'IRP 300만 원' in d['message'], f'[연말공제] 연금저축 초과 납입 처리: {d["message"]}')
    # IRP 만 800 → 남은 100 은 연금저축으로도 IRP 로도 채울 수 있다(연금저축 몫 100, IRP 몫 0)
    p['accounts'] = [{'type': 'IRP', 'opened': '2023-01-15', 'paid_this_year': 800}]
    e = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'year_end_credit'][0]
    fig(e, '남은 공제 대상', '100만 원')
    check('연금저축 100만 원' in e['message'] and 'IRP로만 100만 원' in e['message'] and '그중 IRP' not in e['figures'],
          f'[연말공제] IRP 800: {e["message"]}')
    diy_e = next(x['payload'] for x in e['next_actions'] if x['kind'] == 'diy')
    check([(c['savings'], c['irp']) for c in diy_e['combos']] == [(0, 800), (100, 800), (0, 900)],
          f'[연말공제] IRP 800 DIY 조합 {diy_e["combos"]}')
    # 종합소득자는 연말정산이 아니라 종합소득세 신고에서 공제된다(doc41). 손계산: 종합소득 3,000(≤4,500 → 16.5%) → 99만 원
    p = json.loads(json.dumps(PROFILES['young_worker']))
    p['income'] = {'kind': '종합소득', 'v': 3000}
    f = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'year_end_credit'][0]
    check(f['message'].startswith('올해분 종합소득세 신고 때 세금을 99만 원 더 줄일 수 있습니다.') and '연말정산' not in f['message'],
          f'[연말공제] 종합소득 문구: {f["message"]}')
    p['income'] = {'kind': '종합소득', 'v': 5000}           # 4,500 초과 → 13.2% (총급여 기준 5,500 이 아니다)
    g = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'year_end_credit'][0]
    fig(g, '세액공제율', '13.2%')
    # 어느 쪽으로 채워도 같은 몫이 있으면 계좌별 인출 차이(doc41)를 함께 적는다 — 고르는 건 고객
    check(any('doc41' in x and 'IRP' in x for x in a['detail']), '[연말공제] 중도인출 차이 설명 없음')
    # 납입액을 모르는 연금계좌는 0원으로 본다고 가정에 적는다
    p = json.loads(json.dumps(PROFILES['young_worker']))
    p['accounts'].append({'type': 'IRP', 'opened': '2024-01-01'})
    h = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'year_end_credit'][0]
    check(any('IRP' in x and '0원으로 봄' in x for x in h['assumptions']), f'[연말공제] 납입액 모르는 계좌: {h["assumptions"]}')


# ---------------------------------------------------------------------------
# 2. ISA 만기 전환 (R-03, doc4·doc23)
# ---------------------------------------------------------------------------
def test_isa():
    # 손계산: 만기 2026-11-20, 2,000만 원 → 추가 공제 대상 min(200, 300) = 200만, × 16.5% = 33만 원
    #   마감: 만기일 다음 날부터 60일 → 2027-01-19
    a = one('young_worker', 'isa_maturity')
    check(a is not None, '[ISA] 만기 43일 전인데 알림이 없음')
    fig(a, '추가 공제 대상', '200만 원')
    fig(a, '더 돌려받을 금액', '33만 원')
    fig(a, '전환 연도 공제 대상 한도', '연금저축으로 옮기면 800만 원 · IRP로 옮기면 1,100만 원')
    check(a and a['when'] == '2027-01-19', f'[ISA] 마감 {a and a["when"]}')
    check(a and a['severity'] == '중간', '[ISA] 만기 전 중요도')
    # 창 밖: 만기 61일 전, 만기 후 61일
    check(one('young_worker', 'isa_maturity', '2026-09-20') is None, '[ISA] 만기 61일 전에 알림')
    check(one('young_worker', 'isa_maturity', '2026-09-21') is not None, '[ISA] 만기 60일 전에 알림 없음')
    check(one('young_worker', 'isa_maturity', '2027-01-19') is not None, '[ISA] 마감일 당일에 알림 없음')
    check(one('young_worker', 'isa_maturity', '2027-01-20') is None, '[ISA] 마감 지난 뒤 알림')
    b = one('young_worker', 'isa_maturity', '2026-12-01')
    check(b and b['severity'] == '높음', '[ISA] 만기 지난 뒤(전환 기간) 높음이 아님')
    # 손계산: 5,000만 원이면 10% = 500 → 상한 300만, × 16.5% = 49만 5천 원
    p = json.loads(json.dumps(PROFILES['young_worker']))
    p['isa']['amount'] = 5000
    c = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'isa_maturity'][0]
    fig(c, '추가 공제 대상', '300만 원')
    fig(c, '더 돌려받을 금액', '49만 5천 원')
    # 소득이 없으면 두 공제율로 보여 주고 '더 필요한 정보'에 소득
    p.pop('income')
    d = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'isa_maturity'][0]
    check('16.5%면 49만 5천 원' in d['message'] and '13.2%면 39만 6천 원' in d['message'] and d['needs'],
          f'[ISA] 소득 없을 때 문구: {d["message"]}')
    # 상담(chat) 경로와 같은 숫자 — taxcalc 는 옮긴 해 공제 대상 전체로 답한다.
    #   손계산: 연금저축으로 (600 + 200) × 16.5% = 132만 원, IRP로 (900 + 200) × 16.5% = 181만 5천 원
    fig(a, '옮긴 해 세액공제(일반 한도까지 채울 때)', '연금저축으로 옮기면 132만 원 · IRP로 옮기면 181만 5천 원')
    q = next(x['payload'] for x in a['next_actions'] if x['kind'] == 'chat')
    r = taxcalc.compute(q)
    txt = '\n'.join(b['text'] for b in (r or {}).get('blocks', []))
    check(all(s in txt for s in ('= 200만 원', '공제 대상 800만 원', '132만 원', '공제 대상 1,100만 원', '181만 5천 원')),
          f'[ISA] 상담 질문 계산이 알림과 다름: {q}\n{txt}')
    # 300만 원 상한은 '이 ISA 만기금' 기준이다(doc4: 해를 나눠 넣어도 인당 300만 원) — 가정에 그 뜻대로 적는다
    check(any('300만 원(doc4)' in x for x in a['assumptions']), f'[ISA] 300만 원 상한 가정 {a["assumptions"]}')
    # 종합소득자는 '옮긴 해 종합소득세 신고'
    p = json.loads(json.dumps(PROFILES['young_worker']))
    p['income'] = {'kind': '종합소득', 'v': 3000}
    e = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'isa_maturity'][0]
    check('종합소득세 신고' in e['message'] and '연말정산' not in e['message'], f'[ISA] 종합소득 문구: {e["message"]}')


# ---------------------------------------------------------------------------
# 3. 만 55세 도래 (R-04·R-05, doc39)
# ---------------------------------------------------------------------------
def test_age55():
    # 손계산: 1971-12-15생 → 2026-12-15 만 55세
    #   연금저축 2010 가입(2013.3.1 전) → 첫해 6년차: 8,000 ÷ (11−6) × 1.2 = 1,920만
    #   IRP 2019 가입(가입 5년 2024 충족) → 첫해 1년차: 1,800 ÷ 10 × 1.2 = 216만
    a = one('pre_retiree', 'age55')
    check(a is not None, '[55세] 알림 없음')
    check(a and a['when'] == '2026-12-15', f'[55세] 날짜 {a and a["when"]}')
    fig(a, '연금저축 첫해 연금수령한도', '1,920만 원 (6년차)')
    fig(a, 'IRP 첫해 연금수령한도', '216만 원')
    fig(a, '연금소득세율(만 55~69세)', '5.5%')
    check(a and '가입기간 5년 요건 없이' in a['message'], '[55세] 퇴직금 IRP 이전 안내 없음')
    check(a and 'R-15' not in a['rule_ids'] and 'R-07' in a['rule_ids'], f'[55세] 규칙 {a and a["rule_ids"]}')
    # 퇴직 예정 + 2013.3.1 전 계좌 → 어느 계좌로 옮기느냐에 따라 첫해 한도가 다르다는 사실만(doc39 6년차 특례)
    check(a and any('6년차부터' in x and 'DC 가입자' in x and 'doc39' in x for x in a['detail']),
          f'[55세] 옛 계좌 비교 설명 없음 {a and a["detail"]}')
    # 3개월 창: 생일이 다음 해 1월이면 10월에는 아니고 11월에는 알림
    p = json.loads(json.dumps(PROFILES['pre_retiree']))
    p['birth'] = '1972-01-20'               # 2027-01-20 만 55세
    check(not [x for x in scan(p, '2026-10-08', strict=True) if x['kind'] == 'age55'], '[55세] 3개월 밖인데 알림')
    check([x for x in scan(p, '2026-10-20', strict=True) if x['kind'] == 'age55'], '[55세] 3개월 안인데 알림 없음')
    # 가입 5년이 안 된 연금저축은 개시 가능일이 뒤로 밀린다
    p = json.loads(json.dumps(PROFILES['pre_retiree']))
    p['accounts'][0]['opened'] = '2023-03-02'
    p.pop('retire_plan')
    b = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'age55'][0]
    check('2028년 3월 2일부터' in b['message'] and '연금저축 개시 가능일' in b['figures'],
          f'[55세] 가입 5년 미충족 처리: {b["message"]}')
    # 모든 계좌가 가입 5년 미충족이면 '받을 수 있게 됩니다'라고 하면 안 된다(R-04)
    #   (고치기 전: '연금계좌에서 연금을 받을 수 있게 됩니다 … (연금저축은 2029년 1월 1일부터)' 로 앞뒤가 어긋났다)
    p = {'id': 't', 'birth': '1971-12-15', 'accounts': [{'type': '연금저축', 'opened': '2024-01-01', 'balance': 1000}]}
    c = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'age55'][0]
    check('받을 수 있게 됩니다' not in c['message'] and '2029년 1월 1일부터' in c['message']
          and '인정되지 않습니다' in c['message'] and '받을 수 있어요' not in c['title'],
          f'[55세] 가입 5년 미충족 계좌만: {c["title"]} / {c["message"]}')
    # 연금계좌 없이 퇴직 예정만 있으면 계좌 이름을 지어내지 않는다
    p = {'id': 't', 'birth': '1971-12-15', 'retire_plan': {'date': '2026-12-31'}}
    d = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'age55'][0]
    check('연금계좌에서 연금을' not in d['message'] and '가입기간 5년 요건 없이' in d['message'],
          f'[55세] 계좌 없음: {d["message"]}')
    # 계좌도 퇴직 예정도 없으면 내지 않는다
    check(not [x for x in scan({'id': 't', 'birth': '1971-12-15'}, TODAY, strict=True) if x['kind'] == 'age55'],
          '[55세] 정보 없는데 알림')
    # 생일이 올해 이미 지났으면 '올해부터'
    e = [x for x in scan(PROFILES['pre_retiree'], '2026-12-20', strict=True) if x['kind'] == 'age55'][0]
    check(e['title'] == '올해부터 연금을 받을 수 있어요' and '받을 수 있습니다' in e['message'], f'[55세] 지난 생일 {e["title"]}')
    # 연금수령연차 계산(R-05): 2013.3.1 전 가입은 6년차부터, 해마다 1씩
    acct = PROFILES['pre_retiree']['accounts'][0]
    birth = alerts._date(PROFILES['pre_retiree']['birth'])
    check([alerts.year_n(acct, birth, y) for y in (2025, 2026, 2027)] == [None, 6, 7], '[연차] 옛 계좌 6년차 기산')
    irp = PROFILES['retiree_irp']['accounts'][0]
    check(alerts.year_n(irp, alerts._date(PROFILES['retiree_irp']['birth']), 2026) == 1,
          '[연차] 올해 퇴직금 넣은 IRP(62세)는 1년차 — 퇴직금이 있으면 가입 5년 요건 없음')


# ---------------------------------------------------------------------------
# 4. 인출 계획 한도 초과 (R-05·R-06·R-09·R-10, doc39) — withdraw_tax
# ---------------------------------------------------------------------------
def test_over_limit():
    # 손계산: 62세, IRP 평가액 1억(퇴직금 6,000 · 공제받은 돈 4,000), 퇴직소득세 실효세율 4%, 올해 1,800 인출, 1년차
    #   한도 = 10,000 ÷ 10 × 1.2 = 1,200
    #   한꺼번에: 순서상 전부 퇴직금 — 한도 안 1,200 × 4% × 70% = 33.6 + 한도 밖 600 × 4% = 24 → 57만 6천 원
    #   나눠 받기: 올해 1,200 → 33.6 / 내년 600(2년차 한도 8,800 ÷ 9 × 1.2 = 1,173 안) × 4% × 70% = 16.8
    #             → 합계 50만 4천 원, 차이 7만 2천 원
    a = one('retiree_irp', 'over_limit_plan')
    check(a is not None, '[한도초과] 알림 없음')
    fig(a, '올해 연금수령한도', '1,200만 원')
    fig(a, '한도 초과', '600만 원')
    fig(a, '한꺼번에 받으면', '57만 6천 원')
    fig(a, '나눠 받기 올해분', '33만 6천 원')
    fig(a, '나눠 받기 내년분', '16만 8천 원')
    fig(a, '나눠 받으면(두 해 합계)', '50만 4천 원')
    fig(a, '차이', '7만 2천 원')
    check(a and a['severity'] == '높음', '[한도초과] 높음이 아님')
    check(a and '감면 없이 퇴직소득세' in a['message'], '[한도초과] 한도 넘긴 퇴직금 과세 설명 없음')
    # 같은 숫자가 상담(chat) 경로에서도 나오는지 — 알림의 '다음 행동' 질문을 taxcalc.compute 에 넣어 본다
    q = next(x['payload'] for x in a['next_actions'] if x['kind'] == 'chat')
    r = taxcalc.compute(q)
    check(r and '57만 6천 원' in r['figures'] and '50만 4천 원' in r['figures'],
          f'[한도초과] 상담 질문 계산이 알림과 다름: {r and r["figures"]} / {q}')
    # 실효세율이 없으면 금액 대신 '더 필요한 정보'
    p = json.loads(json.dumps(PROFILES['retiree_irp']))
    p['accounts'][0].pop('retire_eff')
    b = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'over_limit_plan'][0]
    check('한꺼번에 받으면' not in b['figures'] and b['needs'], '[한도초과] 실효세율 없는데 금액을 냄')
    # 한도 안이면 내지 않는다
    p = json.loads(json.dumps(PROFILES['retiree_irp']))
    p['accounts'][0]['plan_this_year'] = 1200
    check(not [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'over_limit_plan'], '[한도초과] 한도 안인데 알림')
    # 재원(funds)이 없으면 내지 않는다
    p['accounts'][0]['plan_this_year'] = 1800
    p['accounts'][0].pop('funds')
    check(not [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'over_limit_plan'], '[한도초과] 재원 없는데 알림')
    # 손계산(③ 재원만): 60세, 연금저축 평가액 5,000(공제받은 돈 전부), 2021-05-01 가입 → 가입 5년 2026-05-01 = 개시 첫해(1년차)
    #   한도 600, 계획 1,000 → 한꺼번에: 600 × 5.5% = 33 + 400 × 16.5% = 66 → 99만 원
    #   나눠: 올해 600 → 33 / 내년 400(한도 4,400 ÷ 9 × 1.2 = 586.7 안) × 5.5% = 22 → 55만 원
    p = {'id': 't', 'birth': '1966-01-10', 'accounts': [
        {'type': '연금저축', 'opened': '2021-05-01', 'balance': 5000, 'funds': {'credit': 5000},
         'plan_this_year': 1000, 'paid_years': 0}]}
    c = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'over_limit_plan'][0]
    fig(c, '올해 연금수령한도', '600만 원')
    fig(c, '한꺼번에 받으면', '99만 원')
    fig(c, '나눠 받으면(두 해 합계)', '55만 원')
    check('16.5% 기타소득세' in c['message'] and 'R-09' not in c['rule_ids'], f'[한도초과] ③ 재원 문구·규칙: {c["rule_ids"]}')
    # 같은 계좌를 2018년에 가입했으면 2023년(가입 5년)이 1년차 → 2026년은 4년차: 한도 5,000 ÷ 7 × 1.2 = 857만 1,429원
    p['accounts'][0]['opened'] = '2018-05-01'
    e = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'over_limit_plan'][0]
    fig(e, '올해 연금수령한도', '857만 1,429원')
    # DIY 로 넘기는 설계도 같은 연차(4년차)로 시작해야 첫해 한도가 알림과 같다(고치기 전: DIY 가 1년차 600만 원으로 계산)
    try:
        import diy
    except ImportError:
        diy = None
    if diy:
        plan = next(x['payload'] for x in e['next_actions'] if x['kind'] == 'diy')
        row0 = diy.simulate(plan)['payout']['rows'][0]
        check(row0['text']['limit'] == '857만 1,429원', f'[한도초과] DIY 첫해 한도 {row0["text"]["limit"]}')
        a_plan = next(x['payload'] for x in a['next_actions'] if x['kind'] == 'diy')
        row0 = diy.simulate(a_plan)['payout']['rows'][0]
        check(row0['text']['limit'] == '1,200만 원', f'[한도초과] 62세 DIY 첫해 한도 {row0["text"]["limit"]}')

    def ol(prof):
        return [x for x in scan(prof, TODAY, strict=True) if x['kind'] == 'over_limit_plan']

    base = {'type': '연금저축', 'opened': '2021-05-01', 'balance': 5000, 'plan_this_year': 1000, 'paid_years': 0}
    # 한도를 넘는 몫이 모두 ① 세액공제 안 받은 돈이면 세금이 없다(R-06) → '참고'
    #   (고치기 전: '높음'으로 '세금 0원 vs 0원(차이 0원)'을 냈다)
    f = ol({'id': 't', 'birth': '1966-01-10', 'accounts': [dict(base, funds={'free': 5000})]})
    check(f and f[0]['severity'] == '참고' and '세금이 붙지 않습니다' in f[0]['message'], f'[한도초과] 비과세 재원만: {f}')
    # 평가액 중 재원 목록에 없는 돈 = 운용수익(③). 손계산은 위 c 와 같다(③ 5,000) → 99만 / 55만 원
    #   (고치기 전: 인출 1,000 중 300 만 재원에서 빠져 세금 16만 5천 원으로 작게 냈다)
    g = ol({'id': 't', 'birth': '1966-01-10', 'accounts': [dict(base, funds={'credit': 300})]})
    check(g and g[0]['figures'].get('한꺼번에 받으면') == '99만 원' and any('운용수익' in x for x in g[0]['assumptions']),
          f'[한도초과] 재원 합 < 평가액: {g and (g[0]["figures"], g[0]["assumptions"])}')
    # 재원 합이 평가액보다 크면 차이를 운용손실로 보고 ③에서 뺀다(③ 5,500 − 500 = 5,000 → 같은 값)
    h = ol({'id': 't', 'birth': '1966-01-10', 'accounts': [dict(base, funds={'credit': 5500})]})
    check(h and h[0]['figures'].get('한꺼번에 받으면') == '99만 원' and any('운용손실' in x for x in h[0]['assumptions']),
          f'[한도초과] 재원 합 > 평가액: {h and h[0]["assumptions"]}')
    # ③에서 다 뺄 수 없을 만큼 크면 어느 재원이 줄었는지 몰라 계산하지 않는다
    check(not ol({'id': 't', 'birth': '1966-01-10', 'accounts': [dict(base, funds={'retire': 6000, 'credit': 100},
                                                                          retire_eff=4)]}), '[한도초과] 재원 합이 너무 큰데 알림')
    # 계획이 평가액보다 많으면 평가액까지만 계산하고 가정에 적는다. 손계산: 평가액 500, 1년차 한도 60
    #   한꺼번에 60 × 5.5% = 3.3 + 440 × 16.5% = 72.6 → 75만 9천 원
    k = ol({'id': 't', 'birth': '1966-01-10', 'accounts': [dict(base, balance=500, funds={'credit': 500})]})
    check(k and k[0]['figures'].get('올해 인출 계획') == '500만 원' and k[0]['figures'].get('한꺼번에 받으면') == '75만 9천 원'
          and any('평가액까지만' in x for x in k[0]['assumptions']), f'[한도초과] 계획 > 평가액: {k and k[0]["figures"]}')
    # 개시 가능일이 올해 뒤쪽이면(가입 5년 = 2026-11-01) 그 뒤 인출로 본다고 적는다
    m = ol({'id': 't', 'birth': '1966-01-10', 'accounts': [dict(base, opened='2021-11-01', funds={'credit': 5000})]})
    check(m and any('2026-11-01' in x and '뒤에 인출' in x for x in m[0]['assumptions']), f'[한도초과] 개시 전 {m}')
    # 다른 계좌 ③과 합쳐 1,500만 원을 넘으면 이 계좌만 본 세금이라는 점을 적는다(R-08)
    #   연금저축(1년차) ③ 한도 안 600 + 옛 IRP(2011 가입 → 2026년 11년차, 한도 없음) ③ 1,000 = 1,600
    two = {'id': 't', 'birth': '1966-01-10', 'accounts': [
        dict(base, funds={'credit': 5000}),
        {'type': 'IRP', 'opened': '2011-01-05', 'balance': 9000, 'funds': {'credit': 9000}, 'plan_this_year': 1000}]}
    n_ = ol(two)
    check(n_ and any('1,600만 원' in x and '이 계좌만' in x for x in n_[0]['assumptions']), f'[한도초과] 계좌 합산 1,500 {n_}')
    o = [x for x in scan(two, TODAY, strict=True) if x['kind'] == 'over_1500']
    check(o and o[0]['figures']['올해 ③ 재원 연금수령 합계'] == '1,600만 원', '[한도초과] 계좌 합산 1,500 알림')
    # 지금 54세라도 올해 55세 생일(12-15) 뒤에 받으면 세율은 그날 나이(5.5%)로 — 손계산은 위 c 와 같다(99만 / 55만 원)
    #   (고치기 전: 오늘 나이 54세로 세율을 못 정해 금액 없이 '퇴직소득세 실효세율이 필요'라고 엉뚱하게 되물었다)
    r_ = ol({'id': 't', 'birth': '1971-12-15', 'accounts': [dict(base, opened='2015-01-01', funds={'credit': 5000})]})
    check(r_ and r_[0]['figures'].get('한꺼번에 받으면') == '99만 원' and not r_[0]['needs']
          and any('2026-12-15' in x for x in r_[0]['assumptions']), f'[한도초과] 올해 55세 {r_ and (r_[0]["figures"], r_[0]["needs"])}')
    # 실제로 받은 해 수를 모르면 그 가정을 적는다(퇴직금 재원)
    p = json.loads(json.dumps(PROFILES['retiree_irp']))
    p['accounts'][0].pop('paid_years')
    q_ = ol(p)
    check(q_ and any('실제수령 1년차로 봄' in x for x in q_[0]['assumptions']), f'[한도초과] paid_years 없음 {q_}')


# ---------------------------------------------------------------------------
# 5. 연 1,500만 원 초과 (R-08, doc38)
# ---------------------------------------------------------------------------
def test_over_1500():
    # 손계산: 69세, 연금저축 1,800 인출 = ① 공제 안 받은 돈 200(비과세, 판단 제외) + ③ 1,600 → 1,500 초과
    #   전액 16.5%: 1,600 × 16.5% = 264만 원 / 1,500 이하면 5.5%: 1,500 × 5.5% = 82만 5천 원
    #   IRP 1,000 은 전부 퇴직금 → 판단 제외
    a = one('pensioner_69', 'over_1500')
    check(a is not None, '[1500] 알림 없음')
    fig(a, '올해 ③ 재원 연금수령 합계', '1,600만 원')
    fig(a, '16.5% 분리과세를 고르면', '264만 원')
    fig(a, '1,500만 원을 받을 때 세금', '82만 5천 원')
    check(a and '전액' in a['message'] and '초과분만' not in a['message'], '[1500] 전액 과세 문구')
    q = next(x['payload'] for x in a['next_actions'] if x['kind'] == 'chat')
    r = taxcalc.compute(q)
    check(r and '264만 원' in r['figures'], f'[1500] 상담 질문 계산이 알림과 다름: {r and r["figures"]}')
    # 퇴직금만 1,800 받는 62세는 1,500 판단에 들지 않는다
    check(one('retiree_irp', 'over_1500') is None, '[1500] 퇴직금 재원인데 알림')
    # 두 계좌 합산: 각각 900(③)이면 합 1,800 → 알림
    p = {'id': 't', 'birth': '1960-01-10', 'accounts': [
        {'type': '연금저축', 'opened': '2010-01-05', 'balance': 9000, 'funds': {'credit': 9000}, 'plan_this_year': 900},
        {'type': 'IRP', 'opened': '2011-01-05', 'balance': 9000, 'funds': {'credit': 9000}, 'plan_this_year': 900}]}
    b = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'over_1500']
    check(b and b[0]['figures']['올해 ③ 재원 연금수령 합계'] == '1,800만 원', '[1500] 두 계좌 합산 안 됨')


# ---------------------------------------------------------------------------
# 6. 70·80세 세율 변화 (R-07, doc38)
# ---------------------------------------------------------------------------
def test_rate_step():
    # 손계산: 1957-03-20생 → 2027-03-20 만 70세, 5.5% → 4.4%
    #   올해 ③ 계획 1,600 > 1,500 이라 예시는 1,500: 82만 5천 원 → 66만 원
    a = one('pensioner_69', 'rate_step')
    check(a is not None and a['when'] == '2027-03-20', f'[세율] 날짜 {a and a["when"]}')
    fig(a, '지금 세율', '5.5%')
    fig(a, '바뀐 뒤 세율', '4.4%')
    fig(a, '1,500만 원 받을 때 세금', '82만 5천 원 → 66만 원')
    # 손계산: 79세 → 80세, 4.4% → 3.3%, ③ 1,000 → 44만 → 33만 원
    p = {'id': 't', 'birth': '1947-02-01', 'accounts': [
        {'type': '연금저축', 'opened': '2005-01-05', 'balance': 9000, 'funds': {'credit': 9000}, 'plan_this_year': 1000,
         'paid_years': 20}]}
    b = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'rate_step']
    check(b and b[0]['figures'].get('1,000만 원 받을 때 세금') == '44만 원 → 33만 원', f'[세율] 80세 {b and b[0]["figures"]}')
    # 60세는 내년에도 구간이 그대로 → 없음, 퇴직금만 받는 계좌도 없음
    p['birth'] = '1966-01-01'
    check(not [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'rate_step'], '[세율] 60세인데 알림')
    check(one('retiree_irp', 'rate_step') is None, '[세율] 62세에게 알림')


# ---------------------------------------------------------------------------
# 7. 위험자산 70% (R-14, doc14)
# ---------------------------------------------------------------------------
def test_risk():
    # 손계산: DC 2억 × (72% − 70%) = 400만 원 한도 밖 → 높음
    a = one('pre_retiree', 'risk_limit')
    check(a and a['severity'] == '높음', '[위험자산] 72% 인데 높음이 아님')
    fig(a, '한도를 넘은 금액(대략)', '400만 원')
    # 손계산: IRP 1억 × (70% − 66%) = 400만 원 여유 → 참고
    b = one('retiree_irp', 'risk_limit')
    check(b and b['severity'] == '참고', '[위험자산] 66% 인데 참고가 아님')
    fig(b, '한도까지 여유(대략)', '400만 원')
    # 64% 는 내지 않는다, 연금저축은 대상 아님
    p = {'id': 't', 'accounts': [{'type': 'IRP', 'risky_pct': 64}, {'type': '연금저축', 'risky_pct': 90}]}
    check(not scan(p, TODAY, strict=True), '[위험자산] 64%·연금저축에 알림')
    check(alerts.RISK_CAP == 70 and alerts.RISK_NEAR == 65, '[위험자산] 기준값을 원문에서 못 읽음')
    # 경계: 65% 는 참고, 70% 는 아직 한도 안(참고), 70.5% 는 높음
    for x, want in ((65, '참고'), (70, '참고'), (70.5, '높음')):
        r = scan({'id': 't', 'accounts': [{'type': 'IRP', 'risky_pct': x}]}, TODAY, strict=True)
        check(r and r[0]['severity'] == want, f'[위험자산] {x}% → {r and r[0]["severity"]}')
    # DC 에도 같은 한도라는 근거·TDF 예외(doc53)를 함께 낸다
    check(a and 'doc53.txt' in a['docs'] and 'R-14' in a['rule_ids'], f'[위험자산] TDF 근거 {a and a["docs"]}')


# ---------------------------------------------------------------------------
# 8. 연금실제수령연차 11년차 (R-09, doc40)
# ---------------------------------------------------------------------------
def test_actual_year():
    # 손계산: IRP 퇴직금 재원, 올해 전 9년 수령 + 올해 수령 → 올해 10년차, 내년 11년차: 감면 30% → 40%
    #   1,000 × 5% × 70% = 35만 원 → 1,000 × 5% × 60% = 30만 원
    a = one('pensioner_69', 'actual_year_11')
    check(a is not None and a['when'] == '2027-01-01', '[실제수령연차] 알림 없음')
    fig(a, '퇴직소득세 감면', '30% → 40%')
    fig(a, '퇴직금 1,000만 원 받을 때 세금', '35만 원 → 30만 원')
    check(a and '35만 원에서 30만 원으로 줄어듭니다' in a['message'], f'[실제수령연차] 문구: {a and a["message"]}')
    # 8년 수령 → 내년 10년차는 감면이 그대로라 없음
    p = json.loads(json.dumps(PROFILES['pensioner_69']))
    p['accounts'][1]['paid_years'] = 8
    check(not [x for x in scan(p, TODAY, strict=True) if x['kind'].startswith('actual_year')], '[실제수령연차] 10년차에 알림')
    # 올해 받지 않으면 실제수령연차가 늘지 않는다 — 10년 받았고 올해 쉬면 내년이 11년차
    p['accounts'][1]['paid_years'] = 10
    p['accounts'][1]['plan_this_year'] = 0
    check([x for x in scan(p, TODAY, strict=True) if x['kind'] == 'actual_year_11'], '[실제수령연차] 쉬는 해 처리')
    # 올해 계획으로 퇴직금이 다 빠지면(퇴직금 800 < 계획 1,000) 내년 감면 확대는 쓸 데가 없다 → 알리지 않는다
    #   (고치기 전: 재원이 ③으로 바뀐 효과를 감면 효과처럼 '39만 원 → 44만 원'으로 냈다)
    p = json.loads(json.dumps(PROFILES['pensioner_69']))
    p['accounts'][1]['funds'] = {'free': 0, 'retire': 800, 'credit': 5200}
    check(not [x for x in scan(p, TODAY, strict=True) if x['kind'].startswith('actual_year')], '[실제수령연차] 퇴직금 소진인데 알림')
    # 손계산: 퇴직금 1,500 중 올해 1,000 인출 → 내년 남는 퇴직금 500 → 500 × 5% × 70% = 17.5 → × 60% = 15
    p['accounts'][1]['funds'] = {'free': 0, 'retire': 1500, 'credit': 4500}
    b = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'actual_year_11'][0]
    fig(b, '퇴직금 500만 원 받을 때 세금', '17만 5천 원 → 15만 원')
    # 실효세율이 없으면 금액 없이 '더 필요한 정보'
    p = json.loads(json.dumps(PROFILES['pensioner_69']))
    p['accounts'][1].pop('retire_eff')
    c = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'actual_year_11'][0]
    check(c['needs'] and not any('세금' in k for k in c['figures']), f'[실제수령연차] 실효세율 없음 {c["figures"]}')
    # 20년 수령 + 올해 수령 → 내년 22년차는 그대로, 19년 + 올해 → 내년 21년차: 감면 40% → 50%
    p = json.loads(json.dumps(PROFILES['pensioner_69']))
    p['accounts'][1]['paid_years'] = 19
    d = [x for x in scan(p, TODAY, strict=True) if x['kind'].startswith('actual_year')]
    check(d and d[0]['kind'] == 'actual_year_21' and d[0]['figures']['퇴직소득세 감면'] == '40% → 50%',
          f'[실제수령연차] 21년차 {d and d[0]["figures"]}')
    # 감면율은 taxcalc(withdraw_tax)에서 읽는다 — 1~10년차 0.7, 11~20년차 0.6, 21년차~ 0.5 (doc39·doc40 표)
    check([alerts._retire_ratio(m) for m in (1, 10, 11, 20, 21, 30)] == [0.7, 0.7, 0.6, 0.6, 0.5, 0.5],
          '[실제수령연차] 감면 비율')


# ---------------------------------------------------------------------------
# 공통 — 모양·근거·문구·리포트·outbox
# ---------------------------------------------------------------------------
def test_common():
    seen = set()
    days = ['2026-01-15', '2026-03-01', '2026-06-30', '2026-10-08', '2026-11-25', '2026-12-31', '2027-01-10']
    for pid, p in PROFILES.items():
        for d in days:
            al = scan(p, d, strict=True)
            ranks = [(alerts.SEVERITY[a['severity']], a['when']) for a in al]
            check(ranks == sorted(ranks), f'[정렬] {pid} {d}')
            check(len({a['id'] for a in al}) == len(al), f'[id 중복] {pid} {d}')
            for a in al:
                seen.add(a['kind'])
                k = a['kind']
                check(REQUIRED <= set(a), f'[모양] {k} 빠진 키 {REQUIRED - set(a)}')
                check(a['severity'] in ('높음', '중간', '참고'), f'[모양] {k} 중요도 {a["severity"]}')
                n_sent = len(re.findall(r'[.!?](?=\s|$)', a['message']))
                check(2 <= n_sent <= 3, f'[문장 수] {k} {n_sent}문장: {a["message"]}')
                text = ' '.join([a['title'], a['message']] + list(map(str, a['figures'].values()))
                                + [x['label'] for x in a['next_actions']])
                check(not BANNED.search(text), f'[판단 표현] {k}: {BANNED.search(text)}')
                check(all(r in taxcalc.RULE_NAME for r in a['rule_ids']), f'[규칙 번호] {k} {a["rule_ids"]}')
                files = {q['file'] for q in a['quotes']}
                check(set(a['docs']) == files, f'[문서] {k} docs 와 원문 파일이 다름')
                want_r, want_d = CONTRACT.get(k if not k.startswith('actual_year') else 'actual_year_11')
                check(want_r <= set(a['rule_ids']) and want_d <= set(a['docs']),
                      f'[계약 근거] {k} {a["rule_ids"]} {a["docs"]}')
                check(all(x['kind'] in ('chat', 'diy', 'handoff') and x['label'] for x in a['next_actions']),
                      f'[다음 행동] {k}')
                json.dumps(a, ensure_ascii=False)
    check(set(CONTRACT) <= seen, f'[알림 종류] 시연 프로필로 나오지 않는 종류: {set(CONTRACT) - seen}')
    # 원문 인용이 문서에 글자 그대로 있는지(RULES 밖에서 쓰는 ISA 60일 원문)
    for key, (rid, f, text) in alerts.EXTRA_QUOTES.items():
        check(re.sub(r'\s+', ' ', text) in doc_text(f), f'[원문] {key} 가 {f} 에 없음')
    # 원문에서 읽은 기준값
    check((alerts.SEP_CAP, alerts.SEP_RATE, alerts.PENSION_AGE, alerts.MIN_YEARS, alerts.OLD_START, alerts.ISA_DAYS)
          == (1500, 16.5, 55, 5, 6, 60) and str(alerts.OLD_CUT) == '2013-03-01', '[기준값] 원문 파싱')
    check(alerts.CAP_ALL == 900 and alerts.CAP_SAVINGS == 600, '[기준값] 공제 한도')
    # 정보가 없는 프로필은 조용히 빈 목록
    check(scan({}, TODAY) == [] and scan({'id': 'x', 'birth': '1960-02-29'}, TODAY, strict=True) == [], '[빈 프로필]')
    check(alerts._add_years(alerts._date('1960-02-29'), 55).isoformat() == '2015-02-28', '[윤일 생일]')
    # 기준일 없이 부르면 오늘
    scan(PROFILES['young_worker'])
    # 'DIY 설계' 다음 행동의 payload 는 diy.simulate 가 받는 plan 이어야 한다(diy 모듈이 있을 때만)
    try:
        import diy
    except ImportError:
        diy = None
    if diy:
        for p in PROFILES.values():
            for a in scan(p, TODAY, strict=True):
                for x in a['next_actions']:
                    if x['kind'] == 'diy':
                        try:
                            diy.simulate(x['payload'])
                            ok = True
                        except Exception as e:
                            ok = e
                        check(ok is True, f'[DIY payload] {a["kind"]}: {ok}')


def test_profiles():
    raw = json.load(open(alerts.PROFILES_PATH, encoding='utf-8'))
    doc = raw['_설명']
    check(len(raw['profiles']) >= 3, '[프로필] 3명 미만')
    for p in raw['profiles']:
        check({'id', 'name', 'summary'} <= set(p), f'[프로필] {p.get("id")} id·name·summary')
        for k in p:
            check(k in doc, f'[프로필] 문서화 안 된 필드 {k}')
        for a in p.get('accounts', []):
            for k in a:
                check(f'accounts[].{k}' in doc, f'[프로필] 문서화 안 된 계좌 필드 {k}')
            f = a.get('funds')
            if f and a.get('balance') is not None:
                check(abs(sum(f.values()) - a['balance']) < 1e-6, f'[프로필] {p["id"]} 재원 합 ≠ 평가액')
    p3 = PROFILES['retiree_irp']['accounts'][0]
    check((p3['balance'], p3['funds']['retire'], p3['funds']['credit'], p3['retire_eff'], p3['plan_this_year'])
          == (10000, 6000, 4000, 4, 1800), '[프로필] 62세 퇴직금 IRP 값이 계약과 다름')
    p1 = PROFILES['young_worker']
    check(p1['income'] == {'kind': '총급여', 'v': 5000} and p1['accounts'][0]['paid_this_year'] == 300,
          '[프로필] 30대 직장인 값이 계약과 다름')
    lst = alerts.profile_list()
    check(lst and all(set(x) == {'id', 'name', 'summary'} for x in lst), '[프로필] profile_list 모양')
    check(alerts.get_profile('retiree_irp')['id'] == 'retiree_irp' and alerts.get_profile('없음') is None, '[프로필] get_profile')


def test_report():
    for pid, p in PROFILES.items():
        md = report(p, TODAY)
        al = scan(p, TODAY)
        check(md.startswith('# 이번 달 연금 점검 리포트'), f'[리포트] {pid} 제목')
        check(not re.search(r'<\s*/?\s*[A-Za-z][^>]*>', md) and '<' not in md, f'[리포트] {pid} HTML 태그')
        check('| 항목 | 내용 |' in md, f'[리포트] {pid} 고객 요약 표')
        for a in al:
            check(a['title'] in md and all(r in md for r in a['rule_ids'])
                  and all(d.replace('.txt', '') in md for d in a['docs']), f'[리포트] {pid} {a["kind"]} 근거')
        check('## 다음 행동' in md, f'[리포트] {pid} 다음 행동')
        check(not BANNED.search(md), f'[리포트] {pid} 판단 표현 {BANNED.search(md)}')
    md = report(PROFILES['young_worker'], '2026-07-01')
    check('먼저 알려 드릴 일이 없습니다' in md, '[리포트] 알림 없는 달')


def test_outbox():
    pull('t-out')
    al = scan(PROFILES['pensioner_69'], TODAY)
    check(push('t-out', al) == len(al) and push('t-out', al) == 0, '[outbox] 같은 알림 중복으로 쌓임')
    check(alerts.pending('t-out') == len(al), '[outbox] pending')
    got = pull('t-out')
    check([a['id'] for a in got] == [a['id'] for a in al] and pull('t-out') == [], '[outbox] pull 후 비워지지 않음')
    # 여러 스레드가 동시에 넣어도 하나도 잃지 않는다
    def worker(t):
        for j in range(50):
            push('t-thr', [{'id': f'{t}-{j}'}])
    old_max, alerts.OUTBOX_MAX = alerts.OUTBOX_MAX, 1000      # 상한에 걸리지 않게 잠시 늘린다
    try:
        ts = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        [x.start() for x in ts]
        [x.join() for x in ts]
        got = pull('t-thr')
        check(len(got) == 400 and len({a['id'] for a in got}) == 400, f'[outbox] 동시 push {len(got)}/400')
    finally:
        alerts.OUTBOX_MAX = old_max
    # 고객별 상한을 넘으면 오래된 것부터 버린다
    push('t-cap', [{'id': str(j)} for j in range(alerts.OUTBOX_MAX + 5)])
    q = pull('t-cap')
    check(len(q) == alerts.OUTBOX_MAX and q[0]['id'] == '5', '[outbox] 상한')
    # push 한 뒤 원본을 바꿔도 큐 안의 알림은 그대로(복사본)
    a = {'id': 'copy', 'title': '원래'}
    push('t-copy', [a])
    a['title'] = '바뀜'
    check(pull('t-copy')[0]['title'] == '원래', '[outbox] 복사본이 아님')


def test_inputs():
    """이상 입력 — 서버는 화면이 보낸 profile 을 그대로 넘길 수 있다(POST /alerts 의 profile)."""
    yw = PROFILES['young_worker']
    base = [a['id'] for a in scan(yw, TODAY, strict=True)]
    # 기준일: datetime·공백 글자도 받는다(고치기 전: datetime 이면 strict 가 아닐 때 알림 0건, strict 면 TypeError)
    from datetime import date, datetime
    check([a['id'] for a in scan(yw, datetime(2026, 10, 8, 15, 30), strict=True)] == base, '[입력] datetime 기준일')
    check([a['id'] for a in scan(yw, date(2026, 10, 8), strict=True)] == base, '[입력] date 기준일')
    check([a['id'] for a in scan(yw, ' 2026-10-08 ', strict=True)] == base, '[입력] 공백 붙은 기준일')
    scan(yw, '')                                           # 빈 글자 = 오늘
    for bad in ('2026-13-01', '내일', '2026/10/08x'):
        try:
            scan(yw, bad)
            ok = False
        except ValueError as e:
            ok = 'YYYY-MM-DD' in str(e)
        check(ok, f'[입력] 잘못된 기준일 {bad!r} 이 ValueError 가 아님')
    # 숫자 글자는 숫자로 읽는다
    p = json.loads(json.dumps(yw))
    p['income']['v'] = '5,000'
    p['accounts'][0]['paid_this_year'] = '300'
    p['isa']['amount'] = '2000'
    check([a['id'] for a in scan(p, TODAY, strict=True)] == base, '[입력] 숫자 글자')
    a = [x for x in scan(p, TODAY, strict=True) if x['kind'] == 'year_end_credit'][0]
    fig(a, '더 돌려받을 금액', '99만 원')

    def kinds(prof):
        return [x['kind'] for x in scan(prof, TODAY, strict=True)]
    # 음수 납입은 버린다(고치기 전: 남은 공제 대상 1,400만 원 → '231만 원 더 환급')
    p = {'id': 'x', 'income': {'kind': '총급여', 'v': 5000}, 'accounts': [{'type': '연금저축', 'paid_this_year': -500}]}
    check('year_end_credit' not in kinds(p), '[입력] 음수 납입으로 알림')
    check('accounts[0].paid_this_year' in report(p, TODAY), '[입력] 뺀 필드가 리포트에 없음')
    # 소득 종류가 문서 기준(총급여·종합소득)이 아니면 공제율을 정하지 않는다(고치기 전: '연봉'을 종합소득 13.2%로 계산)
    for inc in ({'kind': '연봉', 'v': 5000}, {'v': 5000}, {'kind': '총급여', 'v': float('nan')},
                {'kind': '총급여', 'v': 0}, {'kind': '총급여', 'v': True}, '5000'):
        p = {'id': 'x', 'income': inc, 'accounts': [{'type': '연금저축', 'paid_this_year': 300}]}
        check('year_end_credit' not in kinds(p), f'[입력] 소득 {inc!r} 로 공제 알림')
    # 계좌 목록이 이상해도 깨지지 않는다
    for accts in (None, 'IRP', 5, ['IRP', None, 3], [{'type': None}], [{'risky_pct': 90}]):
        check(kinds({'id': 'x', 'accounts': accts}) == [], f'[입력] accounts={accts!r}')
    # 범위 밖 비율·세율은 버린다, 숫자 글자 비율은 읽는다
    check(kinds({'id': 'x', 'accounts': [{'type': 'IRP', 'risky_pct': 150}]}) == [], '[입력] 위험자산 150%')
    check(kinds({'id': 'x', 'accounts': [{'type': 'IRP', 'risky_pct': '72'}]}) == ['risk_limit'], '[입력] 위험자산 글자')
    # ISA 금액이 음수·없음·0 이면 알리지 않는다(고치기 전: '공제 대상 -1억 9,800만 원 늘어요')
    for amt in (-2000, None, 0, 'abc'):
        check('isa_maturity' not in kinds({'id': 'x', 'isa': {'maturity': '2026-11-01', 'amount': amt}}), f'[입력] ISA {amt!r}')
    check('isa_maturity' not in kinds({'id': 'x', 'isa': {'maturity': '11월 20일', 'amount': 2000}}), '[입력] ISA 날짜 글자')
    # 재원 하나라도 이상하면 인출 알림을 내지 않는다(인출 순서를 정할 수 없다)
    p = json.loads(json.dumps(PROFILES['retiree_irp']))
    p['accounts'][0]['funds']['retire'] = -1
    check('over_limit_plan' not in kinds(p), '[입력] 음수 재원으로 인출 알림')
    # 날짜가 틀린 생일은 빼고 나머지 알림은 낸다
    p = json.loads(json.dumps(PROFILES['pre_retiree']))
    p['birth'] = '1971-02-30'
    ks = kinds(p)
    check('age55' not in ks and 'risk_limit' in ks and 'year_end_credit' in ks, f'[입력] 틀린 생일 {ks}')
    # 리포트는 프로필이 무엇이든 깨지지 않고, 태그도 만들지 않는다
    for prof in ('abc', None, [], {'id': 'x', 'birth': 'abc'}, {'id': 'x', 'isa': {'maturity': '2026-11-01'}},
                 {'id': 'x', 'name': '<b>홍길동</b><script>alert(1)</script>',
                  'accounts': [{'type': '<i>IRP</i>', 'risky_pct': 90}]}):
        try:
            md = report(prof, TODAY)
            ok = md.startswith('# 이번 달 연금 점검 리포트') and '<' not in md and '>' not in md
        except Exception as e:
            ok = e
        check(ok is True, f'[입력] 리포트 {prof!r}: {ok}')
    # push 는 알림 dict 만 받는다
    pull('t-bad')
    check(push('t-bad', [1, None, 'x', {'id': 'ok'}]) == 1 and push('t-bad', 5) == 0 and push('t-bad', {'id': 'one'}) == 1,
          '[입력] push 이상 입력')
    pull('t-bad')
    # 입력 프로필을 바꾸지 않는다
    p = json.loads(json.dumps(PROFILES['retiree_irp']))
    before = json.dumps(p, sort_keys=True)
    scan(p, TODAY, strict=True)
    report(p, TODAY)
    check(json.dumps(p, sort_keys=True) == before, '[입력] scan·report 가 프로필을 바꿈')


def test_sweep():
    """2026~2027년 매일 — 오류 없이, 모양·문장 수·판단 표현 규칙을 지키는지."""
    from datetime import date, timedelta
    d, end = date(2026, 1, 1), date(2027, 12, 31)
    bad = []
    n = 0
    while d <= end:
        for pid, p in PROFILES.items():
            try:
                for a in scan(p, d, strict=True):
                    n += 1
                    s = len(re.findall(r'[.!?](?=\s|$)', a['message']))
                    if not (2 <= s <= 3) or BANNED.search(a['title'] + a['message']) or '<' in json.dumps(a, ensure_ascii=False):
                        bad.append((pid, d.isoformat(), a['kind']))
            except Exception as e:
                bad.append((pid, d.isoformat(), repr(e)))
        d += timedelta(days=1)
    check(not bad and n > 0, f'[매일] {len(bad)}건 실패, 예: {bad[:3]}')


def test_fuzz():
    """무작위 프로필(이상 값 섞음) 2,000개 — strict 로도 예외 없이, 숫자 필드는 음수·NaN 이 나오지 않는다."""
    import random
    rnd = random.Random(20261008)
    nums = [0, 1, 50, 300, 600, 900, 1499.9, 1500, 1500.1, 1800, 6000, 10000, -1, float('nan'), '300', 'x', None, True]
    dates = ['1957-03-20', '1964-04-02', '1971-12-15', '1992-05-14', '1960-02-29', '2005-04-01', '2012-12-31',
             '2013-03-01', '2021-05-01', '2026-07-01', '2026-11-20', '2027-01-05', 'abc', None, 20261008]
    bad = []
    for k in range(2000):
        accts = []
        for _ in range(rnd.randint(0, 3)):
            a = {'type': rnd.choice(['연금저축', 'IRP', 'DC', 'ISA', None]), 'opened': rnd.choice(dates)}
            for f in ('paid_this_year', 'balance', 'plan_this_year', 'paid_years', 'retire_eff'):
                if rnd.random() < 0.6:
                    a[f] = rnd.choice(nums)
            if rnd.random() < 0.5:
                a['risky_pct'] = rnd.choice([30, 64.9, 65, 70, 70.1, 99, 101, -5, 'x'])
            if rnd.random() < 0.6:
                a['funds'] = {f: rnd.choice(nums) for f in ('free', 'retire', 'credit') if rnd.random() < 0.8}
            accts.append(a)
        p = {'id': f'f{k}', 'birth': rnd.choice(dates), 'accounts': accts}
        if rnd.random() < 0.6:
            p['income'] = {'kind': rnd.choice(['총급여', '종합소득', '연봉']), 'v': rnd.choice(nums)}
        if rnd.random() < 0.4:
            p['isa'] = {'maturity': rnd.choice(dates), 'amount': rnd.choice(nums)}
        if rnd.random() < 0.3:
            p['retire_plan'] = {'date': rnd.choice(dates), 'amount': rnd.choice(nums)}
        day = rnd.choice(['2026-01-02', '2026-10-08', '2026-12-31', '2027-03-19', '2027-11-15'])
        try:
            al = scan(p, day, strict=True)
            md = report(p, day, al)
            text = json.dumps(al, ensure_ascii=False) + md
            shown = ' '.join(str(v) for a in al for v in a['figures'].values()) + ' ' + \
                ' '.join(a['title'] + a['message'] for a in al)
            # 음수 금액('-1억'), NaN·None 이 고객 문구에 나오면 안 된다('D-84'·'2026-11-01' 은 제외)
            if re.search(r'(?<![A-Za-z\d])-\d|nan|inf|None', shown) or '<' in text:
                bad.append((k, 'value', [a['kind'] for a in al]))
        except Exception as e:
            bad.append((k, repr(e)))
    check(not bad, f'[무작위] {len(bad)}건, 예: {bad[:3]}')


def test_source():
    """계약 — taxcalc 비공개 함수·세율 상수를 쓰지 않는다(식은 taxcalc 한 곳에서만 고치게)."""
    import ast
    src = open(os.path.join(ROOT, 'src', 'alerts.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    # taxcalc 에서 가져오는 이름은 공개 함수·표만
    imported = {n.name for x in ast.walk(tree) if isinstance(x, ast.ImportFrom) and x.module == 'taxcalc' for n in x.names}
    attrs = {x.attr for x in ast.walk(tree) if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name)
             and x.value.id in ('tc', 'taxcalc')}
    check(not [n for n in imported | attrs if n.startswith('_')], f'[계약] taxcalc 비공개 이름 사용 {imported | attrs}')
    check(not any(isinstance(x, ast.Import) and any(n.name == 'taxcalc' for n in x.names) for x in ast.walk(tree)),
          '[계약] taxcalc 모듈 통째 import(비공개 함수 접근 가능)')
    # 세율·한도 숫자를 계산식에 직접 쓰지 않는다(숫자 상수만 검사 — 안내 문장 속 글자는 제외)
    rule_nums = {0.165, 16.5, 0.132, 13.2, 0.055, 5.5, 4.4, 3.3, 1.2, 120, 0.7, 0.6, 0.5, 1500, 900, 600, 300, 1800,
                 5500, 4500, 70, 80, 55, 11}
    consts = {x.value for x in ast.walk(tree) if isinstance(x, ast.Constant) and type(x.value) in (int, float)}
    check(not (consts & rule_nums), f'[계약] 세율·한도 숫자를 코드에 직접 씀: {sorted(consts & rule_nums)}')


def main():
    for t in (test_year_end, test_isa, test_age55, test_over_limit, test_over_1500, test_rate_step, test_risk,
              test_actual_year, test_common, test_profiles, test_report, test_outbox, test_inputs, test_sweep,
              test_fuzz, test_source):
        try:
            t()
        except Exception as e:                 # 한 묶음이 터져도 나머지는 돈다
            check(False, f'[예외] {t.__name__}: {type(e).__name__}: {e}')
    print(f'{total - len(fail)}/{total} 통과')
    for f in fail:
        print('  ' + f)
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
