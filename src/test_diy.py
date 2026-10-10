# -*- coding: utf-8 -*-
"""
DIY 연금 설계(diy.py) 회귀 테스트. API 호출 없음.

    python -X utf8 src/test_diy.py

기대값은 문서 원문 규칙(taxcalc.RULES · diy.CAP_QUOTES)으로 손계산한 값이다. 평가셋 정답에서 가져오지 않는다.
금액 단위는 만 원.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import taxcalc
import diy
from taxcalc import won

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'extracted', 'docs')

_n = 0
_fail = []


def check(name, cond, detail=''):
    global _n
    _n += 1
    if not cond:
        _fail.append(f'[{name}] {detail}')


def near(a, b, tol=1e-6):
    # 결과 금액은 1원(0.0001만 원) 단위로 반올림돼 나온다 — 나눗셈이 끼면 tol=1e-3 으로 본다
    return a is not None and b is not None and abs(a - b) <= tol


def _nows(s):
    return re.sub(r'\s+', '', s)


def _row(res, year):
    return next(x for x in res['payout']['rows'] if x['year'] == year)


def _src(row, kind):
    return next((s for s in row['by_source'] if s['kind'] == kind), None)


INCOME = {'kind': '총급여', 'v': 5000}


# ---------------------------------------------------------------------------
# 1. 근거 원문 — 납입한도 1,800만 원 문장이 문서에 글자 그대로 있는가(공백 무시)
# ---------------------------------------------------------------------------
def t_quotes():
    for fname, quote in diy.CAP_QUOTES:
        with open(os.path.join(DOCS, fname), encoding='utf-8') as fh:
            check('납입한도 원문', _nows(quote) in _nows(fh.read()), f'{fname}: {quote[:30]}…')
        check('납입한도 숫자', '1,800만원' in quote and diy.CONTRIB_CAP == 1800, quote)


# ---------------------------------------------------------------------------
# 2. 납입 조합 — 계약서 확인용 예시와 손계산
# ---------------------------------------------------------------------------
def t_combos():
    res = diy.simulate({'income': INCOME, 'combos': [{'name': 'A', 'savings': 600, 'irp': 300},
                                                       {'name': 'B', 'savings': 900, 'irp': 0}]})
    a, b = res['combos']
    # A: 공제 대상 min(min(600,600)+300, 900) = 900, 총급여 5,000 ≤ 5,500 → 16.5% → 900 × 16.5% = 148.5
    check('A 공제 대상', near(a['eligible'], 900), a['eligible'])
    check('A 환급', near(a['refund'], 148.5) and a['text']['refund'] == '148만 5천 원', a['text'])
    check('A 과세제외 없음', near(a['excluded'], 0), a['excluded'])
    # B: 연금저축 900 → 공제 대상 600 → 600 × 16.5% = 99, 못 받은 300은 나중에 비과세(R-06)
    check('B 환급', near(b['refund'], 99) and b['text']['refund'] == '99만 원', b['text'])
    check('B 공제 못 받는 금액', near(b['excluded'], 300) and b['text']['excluded'] == '300만 원', b['text'])
    check('B 비과세 문장', any('비과세' in x and '300만 원' in x for x in b['lines']), b['lines'])
    check('B 근거 R-06', 'R-06' in b['rule_ids'] and 'doc39.txt' in b['docs'], b['rule_ids'])
    check('A 근거', a['rule_ids'] == ['R-01', 'R-02'] and a['docs'] == ['doc41.txt'], (a['rule_ids'], a['docs']))
    check('요약 결론', res['summary'][0].startswith('조합 A') and '148만 5천 원' in res['summary'][0]
          and 'R-01' in res['summary'][0] and 'doc41' in res['summary'][0], res['summary'])
    check('순서 그대로', [c['name'] for c in res['combos']] == ['A', 'B'])
    check('순위 표시 없음', not any(k in c for c in res['combos'] for k in ('rank', 'best', 'recommended')))

    # 총급여 6,000 > 5,500 → 13.2%: 900 × 13.2% = 118.8
    r = diy.simulate({'income': {'kind': '총급여', 'v': 6000}, 'combos': [{'savings': 600, 'irp': 300}]})
    check('13.2% 환급', r['combos'][0]['text']['refund'] == '118만 8천 원', r['combos'][0]['text'])
    check('이름 기본값', r['combos'][0]['name'] == 'A')
    # 종합소득 기준은 4,500 — 4,500 이하 16.5%, 넘으면 13.2%
    r = diy.simulate({'income': {'kind': '종합소득', 'v': 4500}, 'combos': [{'savings': 600}]})
    check('종합소득 4,500 → 16.5%', r['combos'][0]['rate'] == 16.5 and near(r['combos'][0]['refund'], 99))
    r = diy.simulate({'income': {'kind': '종합소득', 'v': 4600}, 'combos': [{'savings': 600}]})
    check('종합소득 4,600 → 13.2%', r['combos'][0]['rate'] == 13.2 and near(r['combos'][0]['refund'], 79.2))

    # 30대 직장인 연금저축만 300 → 300 × 16.5% = 49.5
    r = diy.simulate({'income': INCOME, 'combos': [{'savings': 300}]})
    check('연금저축 300', r['combos'][0]['text']['refund'] == '49만 5천 원', r['combos'][0]['text'])
    # IRP만 900 → 공제 대상 900
    r = diy.simulate({'income': INCOME, 'combos': [{'irp': 900}]})
    check('IRP만 900', near(r['combos'][0]['eligible'], 900) and near(r['combos'][0]['refund'], 148.5))

    # 1,800 초과: 연금저축 1,200 + IRP 800 = 2,000 → 200 초과(넣을 수 없음), 넣는 1,800 중 공제 900, 과세제외 900
    r = diy.simulate({'income': INCOME, 'combos': [{'name': 'C', 'savings': 1200, 'irp': 800}]})
    c = r['combos'][0]
    check('한도 초과액', near(c['over_cap'], 200) and near(c['deposit'], 1800), c)
    check('한도 초과 과세제외', near(c['excluded'], 900) and near(c['eligible'], 900), c)
    check('한도 초과 경고', any('1,800만 원' in w and '200만 원' in w for w in r['warnings']), r['warnings'])
    check('한도 근거 문서', 'doc41.txt' in c['docs'] and 'doc1.txt' in c['docs']
          and any(e['key'] == 'contrib_cap' for e in r['evidence']), c['docs'])
    # 넘는 200은 연금저축에서 덜 넣는다고 본다 → 연금저축 1,000 + IRP 800, 그 가정이 경고에 적힌다
    check('한도 초과 → 연금저축에서 뺌', near(c['deposit_savings'], 1000) and near(c['deposit_irp'], 800)
          and any('연금저축에서 덜 넣는다' in w for w in r['warnings']), (c['deposit_savings'], c['deposit_irp']))
    # 연금저축 1,800 + IRP 900 = 2,700 → 900 초과, 연금저축 900 + IRP 900 → 공제 대상 900, 과세제외 900
    r = diy.simulate({'income': INCOME, 'combos': [{'savings': 1800, 'irp': 900}]})
    c = r['combos'][0]
    check('한도 초과 2', near(c['over_cap'], 900) and near(c['eligible'], 900) and near(c['excluded'], 900)
          and near(c['refund'], 148.5), c)
    # 1,800 정확히는 경고 없음
    r = diy.simulate({'income': INCOME, 'combos': [{'savings': 900, 'irp': 900}]})
    check('1,800 은 경고 없음', not r['warnings'] and near(r['combos'][0]['excluded'], 900), r['warnings'])

    # 소득 0 → 공제 없음(R-02 '종합소득이 있어야') — 공제를 못 받으니 넣은 600 전부가 과세제외금액(나중에 비과세, R-06)
    r = diy.simulate({'income': {'kind': '총급여', 'v': 0}, 'combos': [{'savings': 600}]})
    c = r['combos'][0]
    check('소득 0 환급 0', near(c['refund'], 0) and any('소득이 없' in w for w in r['warnings']))
    check('소득 0 → 전부 과세제외', near(c['excluded'], 600) and near(c['eligible'], 0)
          and '600만 원' in r['summary'][0] and '비과세' in r['summary'][0], (c['eligible'], c['excluded'], r['summary']))
    check('소득 0 → 결정세액 가정 없음', not any('결정세액' in x for x in r['assumptions']), r['assumptions'])

    # 경계: 총급여 5,500 이하 16.5%(148.5), 5,500 초과 13.2%(118.8) — '이하'·'초과'(doc41)
    r = diy.simulate({'income': {'kind': '총급여', 'v': 5500}, 'combos': [{'savings': 600, 'irp': 300}]})
    check('총급여 5,500 → 16.5%', r['combos'][0]['rate'] == 16.5 and near(r['combos'][0]['refund'], 148.5))
    r = diy.simulate({'income': {'kind': '총급여', 'v': 5500.01}, 'combos': [{'savings': 600, 'irp': 300}]})
    check('총급여 5,500.01 → 13.2%', r['combos'][0]['rate'] == 13.2 and near(r['combos'][0]['refund'], 118.8))
    # 연금저축 601 → 공제 대상 600(99만 원), 1만 원은 과세제외
    r = diy.simulate({'income': INCOME, 'combos': [{'savings': 601}]})
    check('연금저축 601', near(r['combos'][0]['refund'], 99) and near(r['combos'][0]['excluded'], 1), r['combos'][0])
    # 넣는 돈 = 공제 대상 + 과세제외금액 (어느 조합이든)
    for s, i in ((0, 0), (300, 0), (600, 300), (900, 0), (700, 300), (0, 1800), (1800, 900), (100, 2000), (5000, 5000)):
        for inc in (INCOME, {'kind': '총급여', 'v': 0}):
            c = diy.simulate({'income': inc, 'combos': [{'savings': s, 'irp': i}]})['combos'][0]
            check(f'넣는 돈 = 공제 대상 + 과세제외 ({s},{i},{inc["v"]})', near(c['deposit'], c['eligible'] + c['excluded'])
                  and c['deposit'] <= 1800 + 1e-9, c)
    # 소득이 없으면(입력 없음) 환급은 비우고 되묻는다
    r = diy.simulate({'combos': [{'savings': 600, 'irp': 300}]})
    check('소득 없음 → needs', r['combos'][0]['refund'] is None and any('총급여' in x for x in r['needs']), r['needs'])
    check('소득 없음 → 두 공제율 보여줌', '148만 5천 원' in r['combos'][0]['lines'][0]
          and '118만 8천 원' in r['combos'][0]['lines'][0], r['combos'][0]['lines'])

    # 지금 40세, 60세 수령 → 20년 납입 참고값: B 과세제외 300 × 20 = 6,000
    r = diy.simulate({'income': INCOME, 'age': 40, 'combos': [{'name': 'B', 'savings': 900}],
                      'payout': {'start_age': 60, 'years': 10, 'balance': 10000}})
    c = r['combos'][0]
    check('수령 전 남은 해', c['years_to_start'] == 20, c.get('years_to_start'))
    check('누적 과세제외', c['text']['excluded_total'] == '6,000만 원' and near(c['refund_total'], 1980), c['text'])


# ---------------------------------------------------------------------------
# 3. 수령 설계 — 해마다 withdraw_tax, 재원 ① → ② → ③
# ---------------------------------------------------------------------------
def t_payout_basic():
    # 평가액 1억 전부 ③, 60세부터 10년, 수익 0 → 해마다 1,000
    # k년차 한도 = 잔액(1,000 × (11−k)) ÷ (11−k) × 120% = 1,200 → 늘 한도 안, 세금 1,000 × 5.5% = 55, 합계 550
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 10000, 'credit': 10000}})
    p = r['payout']
    check('연 수령액', near(p['annual'], 1000), p['annual'])
    check('연도 수', len(p['rows']) == 10)
    check('1년차 한도', near(_row(r, 1)['limit'], 1200) and _row(r, 1)['text']['limit'] == '1,200만 원')
    check('5년차 한도', near(_row(r, 5)['limit'], 1200), _row(r, 5)['limit'])
    check('해마다 55', all(near(x['tax'], 55) for x in p['rows']), [x['tax'] for x in p['rows']])
    check('누적 세금', near(p['total_tax'], 550) and p['text']['total_tax'] == '550만 원', p['total_tax'])
    check('누적 열', near(_row(r, 4)['cum_tax'], 220), _row(r, 4)['cum_tax'])
    check('평균 세율', p['avg_rate'] == 5.5, p['avg_rate'])
    check('한도 초과 없음', not p['over_limit_years'] and not p['over_1500_years'])
    check('마지막 잔액 0', near(_row(r, 10)['balance_end'], 0), _row(r, 10)['balance_end'])
    check('연금수령연차', [x['year_n'] for x in p['rows']] == list(range(1, 11)))
    check('1년차 가정', any('1년차' in x and '2013년 3월 1일' in x for x in r['assumptions']), r['assumptions'])
    check('요약', '550만 원' in r['summary'][-1] and 'R-05' in r['summary'][-1], r['summary'])
    check('근거 번호', {'R-04', 'R-05', 'R-06', 'R-07'} <= set(p['rule_ids']), p['rule_ids'])

    # 65세 시작 → 65~69세 5.5%(5년 × 55) + 70~74세 4.4%(5년 × 44) = 275 + 220 = 495
    r = diy.simulate({'payout': {'start_age': 65, 'years': 10, 'balance': 10000}})
    check('70세 세율 4.4', _row(r, 6)['rate'] == 4.4 and near(_row(r, 6)['tax'], 44), _row(r, 6)['tax'])
    check('65세 시작 합계', near(r['payout']['total_tax'], 495), r['payout']['total_tax'])
    check('재원 구성 없음 가정', any('전부를 ③' in x for x in r['assumptions']), r['assumptions'])


def t_payout_sources():
    # 1억 = ① 1,500 + ② 퇴직금 3,000(실효세율 4%) + ③ 5,500, 60세부터 10년 → 해마다 1,000, 한도 늘 1,200
    #  1년: ① 1,000                       → 0          (① 500 남음)
    #  2년: ① 500 + ② 500 × 4% × 70%      → 14         (② 2,500)
    #  3년: ② 1,000 × 4% × 70%            → 28         (② 1,500)
    #  4년: ② 1,000                        → 28         (② 500)
    #  5년: ② 500 → 14, ③ 500 × 5.5% → 27.5 = 41.5   (③ 5,000)
    #  6~10년: ③ 1,000 × 5.5% = 55 × 5    → 275
    #  합계 0 + 14 + 28 + 28 + 41.5 + 275 = 386.5
    plan = {'payout': {'start_age': 60, 'years': 10, 'balance': 10000, 'free': 1500, 'retire': 3000,
                       'credit': 5500, 'eff': 4}}
    r = diy.simulate(plan)
    p = r['payout']
    want = [0, 14, 28, 28, 41.5, 55, 55, 55, 55, 55]
    check('재원별 연 세금', len(p['rows']) == len(want) and all(near(x['tax'], w) for x, w in zip(p['rows'], want)),
          [x['tax'] for x in p['rows']])
    check('재원별 합계', near(p['total_tax'], 386.5) and p['text']['total_tax'] == '386만 5천 원', p['total_tax'])
    y1, y2, y5 = _row(r, 1), _row(r, 2), _row(r, 5)
    check('1년차 ①만', [s['kind'] for s in y1['by_source']] == ['free'] and near(y1['remain']['free'], 500))
    check('2년차 ①→②', [s['kind'] for s in y2['by_source']] == ['free', 'retire']
          and near(_src(y2, 'free')['take'], 500) and near(_src(y2, 'retire')['take'], 500), y2['by_source'])
    check('2년차 잔액', near(y2['remain']['free'], 0) and near(y2['remain']['retire'], 2500), y2['remain'])
    check('5년차 ②→③', near(_src(y5, 'retire')['tax'], 14) and near(_src(y5, 'credit')['tax'], 27.5), y5['by_source'])
    check('5년차 잔액', near(y5['remain']['retire'], 0) and near(y5['remain']['credit'], 5000), y5['remain'])
    # 재원별 세금의 합 = withdraw_tax 합계(해마다)
    check('재원별 합 = 연 합계', all(near(sum(s['tax'] for s in x['by_source']), x['tax']) for x in p['rows']))
    check('설명 줄 짝', all(len(x['lines']) == len(x['by_source']) for x in p['rows']))
    check('퇴직금 근거 R-09', 'R-09' in p['rule_ids'] and 'doc40.txt' in p['docs'], p['rule_ids'])
    check('실제수령연차 가정', any('실제수령연차' in x for x in r['assumptions']), r['assumptions'])

    # 재원 일부만 주면 나머지는 ③(운용수익)으로 채운다: 1억 중 ① 1,000 → ③ 9,000
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 10000, 'free': 1000}})
    check('나머지 ③', near(r['payout']['fund']['credit'], 9000) and any('차이' in x for x in r['assumptions']))
    # 재원 합계가 평가액보다 크면 재원 합계로
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 5000, 'credit': 10000}})
    check('재원 합계 우선', near(r['payout']['balance'], 10000) and r['warnings'], r['warnings'])
    # 평가액 없이 재원만
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'retire': 6000, 'credit': 4000, 'eff': 4}})
    check('평가액 = 재원 합계', near(r['payout']['balance'], 10000))
    # 평가액도 재원도 없으면 되묻기
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10}})
    check('평가액 없음 → needs', r['payout'] is None and any('평가액' in x for x in r['needs']), r['needs'])


def t_payout_limits():
    # 5년에 나눠 받기: 해마다 2,000. 1년차 한도 10,000 ÷ 10 × 120% = 1,200
    #  → 한도 안 1,200 × 5.5% = 66, 넘는 800 × 16.5% = 132, 합계 198
    # 2년차: 잔액 8,000, 한도 8,000 ÷ 9 × 120% = 1,066.67 → 58.667 + 933.33 × 16.5% = 154 → 212.667
    r = diy.simulate({'payout': {'start_age': 60, 'years': 5, 'balance': 10000}})
    y1, y2 = _row(r, 1), _row(r, 2)
    check('한도 초과 1년차', near(y1['over_limit'], 800) and near(y1['tax'], 198), (y1['over_limit'], y1['tax']))
    check('한도 초과 2년차', near(y2['limit'], 8000 / 9 * 1.2, 1e-3)
          and near(y2['tax'], 8000 / 9 * 1.2 * 0.055 + (2000 - 8000 / 9 * 1.2) * 0.165, 1e-3), (y2['limit'], y2['tax']))
    check('한도 초과 경고', r['payout']['over_limit_years'][:2] == [1, 2]
          and any('연금수령한도' in w and '연금외수령' in w for w in r['warnings']), r['warnings'])
    check('한도 초과 근거 R-10', 'R-10' in r['payout']['rule_ids'], r['payout']['rule_ids'])

    # 2억 전부 ③, 10년 → 해마다 2,000, 한도 2,400 안. ③ 연금 2,000 > 1,500 → 전액 16.5% = 330
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 20000}})
    y1 = _row(r, 1)
    check('1,500 초과 판정', y1['over_1500'] and near(y1['pension_credit'], 2000), y1)
    check('1,500 초과 전액 16.5%', near(y1['tax'], 330), y1['tax'])
    check('1,500 경고·근거', any('1,500만 원' in w and '전액' in w for w in r['warnings'])
          and 'R-08' in r['payout']['rule_ids'], r['warnings'])
    check('1,500 가정(분리과세로 계산)', any('16.5% 분리과세로 계산' in x for x in r['assumptions']))

    # 퇴직금은 1,500 판단에 안 들어간다: 퇴직금 2억(실효 5%), 60세부터 12년 → 해마다 1,666.67
    #  1년차 한도 2,400 안 → 1,666.67 × 5% × 70% = 58.333, 1,500 초과 아님
    #  11년차: 연금수령연차 11 → 한도 없음, 실제수령 11년차 → 40% 감면: 1,666.67 × 5% × 60% = 50
    r = diy.simulate({'payout': {'start_age': 60, 'years': 12, 'balance': 20000, 'retire': 20000, 'eff': 5}})
    y1, y11 = _row(r, 1), _row(r, 11)
    check('퇴직금 1,500 판단 제외', not y1['over_1500'] and not r['payout']['over_1500_years'])
    check('퇴직금 30% 감면', near(y1['tax'], 20000 / 12 * 0.05 * 0.7, 1e-3), y1['tax'])
    check('11년차 한도 없음', y11['limit'] is None and y11['text']['limit'].startswith('한도 없음'), y11['limit'])
    check('실제수령 11년차 40% 감면', near(y11['tax'], 20000 / 12 * 0.05 * 0.6, 1e-3), y11['tax'])

    # 퇴직소득세율이 없으면 퇴직금 세금은 비우고 되묻는다(③ 세금은 그대로 계산)
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 10000, 'retire': 500, 'credit': 9500}})
    y1 = _row(r, 1)
    check('실효세율 없음 → 비움', y1['tax'] is None and r['payout']['total_tax'] is None)
    check('실효세율 없음 → needs', any('퇴직소득세' in x for x in r['needs']), r['needs'])
    check('실효세율 없음 → ③ 세금은 계산', near(_src(y1, 'credit')['tax'], 500 * 0.055)
          and _src(y1, 'retire')['tax'] is None, y1['by_source'])

    # 2013.3.1 전 가입(6년차부터): 1년차 한도 10,000 ÷ (11−6) × 120% = 2,400
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 10000, 'year_n_start': 6}})
    check('6년차 기산 한도', near(_row(r, 1)['limit'], 2400) and _row(r, 6)['limit'] is None)

    # 55세 전 시작: 53·54세는 연금외수령(③ 16.5%), 55세가 1년차
    #  53세 1,000 × 16.5% = 165 / 55세: 잔액 2,000, 한도 2,000 ÷ 10 × 120% = 240 → 240 × 5.5% + 760 × 16.5% = 138.6
    r = diy.simulate({'payout': {'start_age': 53, 'years': 4, 'balance': 4000}})
    y1, y3 = _row(r, 1), _row(r, 3)
    check('55세 전 연금외수령', y1['early'] and y1['year_n'] is None and near(y1['tax'], 165), (y1['early'], y1['tax']))
    check('55세 1년차', y3['year_n'] == 1 and near(y3['limit'], 240) and near(y3['tax'], 138.6), (y3['limit'], y3['tax']))
    check('55세 전 경고', any('55세 전' in w for w in r['warnings']) and 'R-10' in r['payout']['rule_ids'])
    # 경고는 실제로 빠진 재원만 말한다 — 이 계획은 ③ 뿐이라 퇴직금 문구가 없어야 한다
    check('55세 전 경고 ③만', any('55세 전' in w and '③' in w and '퇴직금' not in w for w in r['warnings']), r['warnings'])

    # 1,500 경계: 평가액 1억 5천, 10년 → 해마다 1,500(한도 1,800 안). '초과'가 아니므로 5.5% → 82.5
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 15000}})
    y1 = _row(r, 1)
    check('1,500 정확히는 초과 아님', not y1['over_1500'] and near(y1['tax'], 82.5) and not r['payout']['over_1500_years'],
          (y1['over_1500'], y1['tax']))
    # 1억 5,010, 10년 → 1,501 → 전액 16.5% = 247.665
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 15010}})
    y1 = _row(r, 1)
    check('1,501 은 전액 16.5%', y1['over_1500'] and near(y1['tax'], 1501 * 0.165), (y1['over_1500'], y1['tax']))

    # 한도 초과 + 1,500 초과가 같은 해: 3억, 5년 → 해마다 6,000, 한도 30,000 ÷ 10 × 120% = 3,600
    #  한도 안 3,600(> 1,500) × 16.5% = 594, 넘는 2,400 × 16.5% = 396 → 990
    r = diy.simulate({'payout': {'start_age': 60, 'years': 5, 'balance': 30000}})
    y1 = _row(r, 1)
    check('한도·1,500 동시', near(y1['limit'], 3600) and near(y1['over_limit'], 2400) and y1['over_1500']
          and near(y1['pension_credit'], 3600) and near(y1['tax'], 990), (y1['limit'], y1['over_limit'], y1['tax']))

    # 80세 경계(R-07): 6년차 기산을 넘어 11년차부터 시작(한도 없음), 78세부터 4년, 해마다 1,000
    #  78·79세 4.4% → 44 × 2, 80·81세 3.3% → 33 × 2 = 154
    r = diy.simulate({'payout': {'start_age': 78, 'years': 4, 'balance': 4000, 'year_n_start': 11}})
    check('80세 3.3%', all(near(x['tax'], w) for x, w in zip(r['payout']['rows'], [44, 44, 33, 33]))
          and near(r['payout']['total_tax'], 154) and all(x['limit'] is None for x in r['payout']['rows']),
          [x['tax'] for x in r['payout']['rows']])

    # 실제수령 21년차 50% 감면(R-09): 퇴직금 2억 2천(실효 5%), 22년, 11년차부터(한도 없음) → 해마다 1,000
    #  1~10년차 1,000 × 5% × 70% = 35, 11~20년차 × 60% = 30, 21·22년차 × 50% = 25 → 350 + 300 + 50 = 700
    r = diy.simulate({'payout': {'start_age': 60, 'years': 22, 'balance': 22000, 'retire': 22000, 'eff': 5,
                                 'year_n_start': 11}})
    taxes = [x['tax'] for x in r['payout']['rows']]
    check('실제수령 감면 단계', len(taxes) == 22 and near(taxes[9], 35) and near(taxes[10], 30) and near(taxes[19], 30)
          and near(taxes[20], 25)
          and near(r['payout']['total_tax'], 700), taxes)

    # 이미 받아 온 계좌: 실제수령 10년차에서 시작(actual_n_start=10), 퇴직금 2,000(실효 5%), 2년, 한도 없음(11년차)
    #  10년차 1,000 × 5% × 70% = 35, 11년차 × 60% = 30 → 65
    r = diy.simulate({'payout': {'start_age': 62, 'years': 2, 'balance': 2000, 'retire': 2000, 'eff': 5,
                                 'year_n_start': 11, 'actual_n_start': 10}})
    check('실제수령연차 이어받기', near(_row(r, 1)['tax'], 35) and near(_row(r, 2)['tax'], 30)
          and [x['actual_n'] for x in r['payout']['rows']] == [10, 11]
          and any('10년차' in x and '실제수령연차' in x for x in r['assumptions']), [x['tax'] for x in r['payout']['rows']])

    # ① 공제 안 받은 돈은 한도를 넘어도 비과세: 3,000 전부 ①, 1년에 다 받음(한도 360)
    r = diy.simulate({'payout': {'start_age': 60, 'years': 1, 'balance': 3000, 'free': 3000}})
    y1 = _row(r, 1)
    check('① 한도 넘어도 0', near(y1['tax'], 0) and near(y1['over_limit'], 2640), (y1['tax'], y1['over_limit']))
    check('① 한도 경고 문구', any('연금수령한도' in w and '① 공제 안 받은 돈은 비과세' in w and '퇴직금' not in w
                              and '③' not in w for w in r['warnings']), r['warnings'])

    # 55세 전 퇴직금: 54세부터 2년, 퇴직금 2,000(실효 4%) → 54세 1,000 × 4% = 40(감면 없음)
    #  55세 잔액 1,000, 한도 120: 120 × 4% × 70% = 3.36 + 880 × 4% = 35.2 → 38.56, 합계 78.56
    r = diy.simulate({'payout': {'start_age': 54, 'years': 2, 'balance': 2000, 'retire': 2000, 'eff': 4}})
    check('55세 전 퇴직금 100%', near(_row(r, 1)['tax'], 40) and near(_row(r, 2)['tax'], 38.56)
          and near(r['payout']['total_tax'], 78.56), [x['tax'] for x in r['payout']['rows']])
    check('55세 전 경고 ②만', any('55세 전' in w and '② 퇴직금' in w and '③' not in w for w in r['warnings']), r['warnings'])

    # 전부 55세 전이면 연금수령연차·한도·세율 가정과 R-05·R-07 근거를 달지 않는다
    r = diy.simulate({'payout': {'start_age': 50, 'years': 3, 'balance': 3000}})
    check('전부 55세 전', not any('연금수령연차' in x for x in r['assumptions'])
          and not {'R-05', 'R-07'} & set(r['payout']['rule_ids']) and near(r['payout']['total_tax'], 495),
          (r['assumptions'], r['payout']['rule_ids']))

    # 지금 나이를 주면, 수령 시작 전에 요건을 갖출 수 있다는 점을 가정에 함께 적는다(40세 → 55세부터 연차, 60세는 6년차)
    r = diy.simulate({'age': 40, 'payout': {'start_age': 60, 'years': 10, 'balance': 10000}})
    check('연차 가정 보충', any('1년차' in x and '6년차일 수 있' in x for x in r['assumptions']), r['assumptions'])


def t_payout_return():
    # 수익률 3%: 기초 지급 연금식 → 1억 × 0.03 ÷ ((1 − 1.03^−10) × 1.03) = 1,138.16…
    w = 10000 * 0.03 / ((1 - 1.03 ** -10) * 1.03)
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 10000, 'free': 10000, 'return_rate': 0.03}})
    p = r['payout']
    check('수익률 반영 연 수령액', near(p['annual'], w, 1e-3), p['annual'])
    check('같은 금액', all(near(x['withdraw'], w, 1e-3) for x in p['rows']), [x['withdraw'] for x in p['rows']])
    check('마지막 잔액 0', near(_row(r, 10)['balance_end'], 0, 1e-3), _row(r, 10)['balance_end'])
    # 1년차: ① 에서 1,138.16 인출, 남은 8,861.84 × 3% = 265.86 이 ③(운용수익)으로
    y1 = _row(r, 1)
    check('수익은 ③으로', near(y1['remain']['credit'], (10000 - w) * 0.03, 1e-3)
          and near(y1['remain']['free'], 10000 - w, 1e-3), y1['remain'])
    check('①이 다 빠진 뒤 ③ 과세', any(x['tax'] and x['tax'] > 0 for x in p['rows'])
          and near(y1['tax'], 0), [x['tax'] for x in p['rows']])
    check('2년차 평가액', near(_row(r, 2)['balance_start'], (10000 - w) * 1.03, 1e-3))


def t_withdraw_tax_used():
    # 해마다 taxcalc.withdraw_tax 를 부르는가(식 복사 금지)
    calls = []
    orig = taxcalc.withdraw_tax

    def spy(*a, **k):
        calls.append(k.get('age'))
        return orig(*a, **k)
    taxcalc.withdraw_tax = spy
    try:
        diy.simulate({'payout': {'start_age': 60, 'years': 7, 'balance': 7000}})
    finally:
        taxcalc.withdraw_tax = orig
    check('withdraw_tax 해마다 호출', set(range(60, 67)) <= set(calls), calls)


def t_sep_limit_matches_taxcalc():
    # diy 의 '1,500만 원 초과' 표시(SEP_LIMIT)와 taxcalc 가 실제로 매긴 ③ 세금이 같은 기준인지 —
    # 표시가 켜진 해는 ③ 한도 안 금액에 16.5%, 꺼진 해는 나이별 세율이어야 한다(넘는 부분은 둘 다 16.5%)
    plans = [{'start_age': s, 'years': y, 'balance': b, 'free': f}
             for s in (58, 68, 78) for y in (3, 5, 10) for b in (9000, 15000, 15010, 20000, 30000) for f in (0, 2000)]
    bad = []
    for po in plans:
        r = diy.simulate({'payout': po})
        for x in r['payout']['rows']:
            cr = _src(x, 'credit')
            if not cr:
                continue
            rate = 16.5 if x['over_1500'] else x['rate']
            want = cr['inside'] * rate / 100 + cr['over'] * 0.165
            if not near(cr['tax'], want, 1e-3) or x['over_1500'] != (cr['inside'] > diy.SEP_LIMIT):
                bad.append((po, x['year'], cr, x['over_1500']))
    check('1,500 표시 = taxcalc 세금', not bad, bad[:3])


# ---------------------------------------------------------------------------
# 4. 문구·근거·다음 행동·입력 검사
# ---------------------------------------------------------------------------
PLANS = [
    {'income': INCOME, 'age': 40, 'combos': [{'name': 'A', 'savings': 600, 'irp': 300},
                                              {'name': 'B', 'savings': 900}, {'name': 'C', 'savings': 1200, 'irp': 800}],
     'payout': {'start_age': 60, 'years': 10, 'balance': 10000, 'credit': 10000}},
    {'income': INCOME, 'combos': [{'savings': 300}], 'payout': {'start_age': 60, 'years': 5, 'balance': 20000}},
    {'payout': {'start_age': 53, 'years': 15, 'balance': 30000, 'free': 2000, 'retire': 8000, 'credit': 20000, 'eff': 4.5,
                'return_rate': 0.04}},
]


def t_text_and_evidence():
    ids = {rid for rid, *_ in taxcalc.RULEBOOK}
    for i, plan in enumerate(PLANS):
        r = diy.simulate(plan)
        check(f'판단 표현 없음 #{i}', not diy.banned_words(r), diy.banned_words(r))
        check(f'HTML 태그 없음 #{i}', not re.search(r'<[A-Za-z/!]', json.dumps(r, ensure_ascii=False)))
        check(f'상품명 없음 #{i}', not re.search(r'TDF|TIGER|KODEX|ACE |펀드명|ETF\s*\w+', json.dumps(
            {k: v for k, v in r.items() if k != 'evidence'}, ensure_ascii=False)))
        try:
            json.dumps(r, ensure_ascii=False, allow_nan=False)
            ok = True
        except (TypeError, ValueError) as e:
            ok = False
            print(e)
        check(f'JSON #{i}', ok)
        check(f'규칙 번호 #{i}', r['rule_ids'] and set(r['rule_ids']) <= ids, r['rule_ids'])
        check(f'문서 파일 #{i}', r['docs'] and all(os.path.exists(os.path.join(DOCS, f)) for f in r['docs']), r['docs'])
        ev_ok = True
        for e in r['evidence']:
            with open(os.path.join(DOCS, e['file']), encoding='utf-8') as fh:
                ev_ok &= _nows(e['text']) in _nows(fh.read())
        check(f'근거 원문 #{i}', ev_ok)
        acts = r['next_actions']
        check(f'다음 행동 모양 #{i}', acts and all(set(a) == {'label', 'kind', 'payload'} and a['kind'] in
                                                  ('chat', 'diy', 'handoff') for a in acts), acts)
        check(f'상담 연결 #{i}', any(a['kind'] == 'handoff' for a in acts))
        # 숫자가 나오는 요약 줄에는 근거 번호와 문서가 붙는다
        check(f'요약 근거 표기 #{i}', all(re.search(r'R-\d\d', s) and re.search(r'doc\d+', s) for s in r['summary']),
              r['summary'])


def t_chat_payloads():
    # 다음 행동의 chat 문장을 상담 경로 계산기(taxcalc.compute, API 없음)가 같은 숫자로 읽는가
    r = diy.simulate({'payout': {'start_age': 62, 'years': 5, 'balance': 10000}})
    a = next(x for x in r['next_actions'] if x['label'].startswith('한도'))
    c = taxcalc.compute(a['payload'])
    text = '\n'.join(b['text'] for b in (c or {}).get('blocks', []))
    check('한도 질문 → 같은 한도', c is not None and '1,200만 원' in text and '800만 원' in text, (a['payload'], text[:300]))
    r = diy.simulate({'payout': {'start_age': 62, 'years': 10, 'balance': 20000}})
    a = next(x for x in r['next_actions'] if '1,500' in x['label'])
    c = taxcalc.compute(a['payload'])
    check('1,500 질문 → 같은 세금', c is not None and '330만 원' in c['figures'], (a['payload'], c and c['figures']))
    # 재원이 섞인 계획: 1억 = ① 1,000 + ② 3,000(실효 4%) + ③ 6,000, 5년 → 해마다 2,000, 1년차 한도 1,200
    #  ① 1,000 한도 안 비과세 / ② 1,000 중 한도 안 200 × 4% × 70% = 5.6, 넘는 800 × 4% = 32 → 37.6
    r = diy.simulate({'payout': {'start_age': 60, 'years': 5, 'balance': 10000, 'free': 1000, 'retire': 3000,
                                 'credit': 6000, 'eff': 4}})
    check('재원 섞인 1년차', near(_row(r, 1)['tax'], 37.6), _row(r, 1)['tax'])
    a = next(x for x in r['next_actions'] if x['label'].startswith('한도'))
    c = taxcalc.compute(a['payload'])
    check('재원 섞인 질문 → 같은 세금', c is not None and '37만 6천 원' in c['figures'], (a['payload'], c and c['figures']))
    # 인출이 ①+② 를 넘어 ③ 까지 가는 해: 1억 = ① 500 + ② 3,000(실효 5%) + ③ 6,500, 2년 → 해마다 5,000, 한도 1,200
    #  ① 500 한도 안 0 / ② 3,000 중 한도 안 700 × 5% × 70% = 24.5, 넘는 2,300 × 5% = 115 / ③ 1,500 넘음 × 16.5% = 247.5
    #  → 387. 질문 문장에 ③ 금액이 없으면 상담 계산기가 순서를 끝까지 못 나눠 규칙만 낸다 — ③ 금액도 넣어야 한다
    r = diy.simulate({'payout': {'start_age': 60, 'years': 2, 'balance': 10000, 'free': 500, 'retire': 3000,
                                 'credit': 6500, 'eff': 5}})
    check('①②③ 1년차', near(_row(r, 1)['tax'], 387), _row(r, 1)['tax'])
    a = next(x for x in r['next_actions'] if x['label'].startswith('한도'))
    c = taxcalc.compute(a['payload'])
    check('①②③ 질문 → 같은 세금', c is not None and '387만 원' in c['figures'] and '세액공제 받은 돈과 수익' in a['payload'],
          (a['payload'], c and c['figures']))
    r = diy.simulate({'income': INCOME, 'combos': [{'savings': 900}]})
    check('과세제외 질문', any(x['kind'] == 'chat' and '세액공제 못 받은' in x['payload'] for x in r['next_actions']))


def t_invalid():
    bad = [
        {'combos': [{'savings': -1}]},
        {'combos': [{'savings': 'abc'}]},
        {'income': {'kind': '연봉', 'v': 5000}},
        {'combos': [{}] * 6},
        {'payout': {'start_age': 60, 'years': 0, 'balance': 1000}},
        {'payout': {'start_age': 60, 'years': 10, 'balance': 1000, 'return_rate': -0.05}},
        {'payout': {'years': 10, 'balance': 1000}},
        {'payout': {'start_age': 60, 'years': 10, 'balance': float('nan')}},
        'plan',
        {'income': {'kind': '총급여', 'v': 10 ** 400}},                 # float 로 못 바꾸는 큰 정수(OverflowError)
        {'combos': [{'savings': 10 ** 400}]},
        {'payout': {'start_age': 100, 'years': 30, 'balance': 1000}},    # 끝 나이 129세
        {'combos': [None]},
        {'combos': 'A'},
        {'payout': 'x'},
    ]
    for i, plan in enumerate(bad):
        try:
            diy.simulate(plan)
            ok = False
        except ValueError:
            ok = True
        check(f'잘못된 입력 #{i}', ok, plan)
    # 문자열 숫자('5,000')는 받는다
    r = diy.simulate({'income': {'kind': '총급여', 'v': '5,000'}, 'combos': [{'savings': '600', 'irp': '300'}]})
    check('문자열 숫자', near(r['combos'][0]['refund'], 148.5))
    check('빈 plan', diy.simulate({})['combos'] == [] and diy.simulate(None)['payout'] is None)
    check('빈 plan → needs', any('납입 조합' in x for x in diy.simulate({})['needs']), diy.simulate({})['needs'])
    # 조합 이름은 요약 문장(마크다운)에 들어간다 — 태그·마크다운 기호를 뺀다, 비면 A·B…
    r = diy.simulate({'income': INCOME, 'combos': [{'name': '<img src=x onerror=alert(1)>', 'savings': 600},
                                                    {'name': '**굵게**', 'savings': 300}, {'name': '  ', 'irp': 100}]})
    s = json.dumps(r, ensure_ascii=False)
    check('이름 태그 제거', '<' not in s and '>' not in s and r['combos'][1]['name'] == '굵게'
          and r['combos'][2]['name'] == 'C', [c['name'] for c in r['combos']])
    # 조사: '4,000만 원은'(받침 ㄴ) — '원는'·'원가' 같은 꼴이 나오지 않는다
    r = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 10000, 'retire': 6000, 'eff': 4}})
    r2 = diy.simulate({'payout': {'start_age': 60, 'years': 10, 'balance': 5000, 'credit': 10000}})
    s = json.dumps([r, r2], ensure_ascii=False)
    check('조사', '원는' not in s and '원가 ' not in s and '4,000만 원은' in s and '1억 원이' in s,
          [x for x in r['assumptions'] + r2['warnings'] if '원' in x][:3])


def main():
    for t in (t_quotes, t_combos, t_payout_basic, t_payout_sources, t_payout_limits, t_payout_return,
              t_withdraw_tax_used, t_sep_limit_matches_taxcalc, t_text_and_evidence, t_chat_payloads, t_invalid):
        try:
            t()
        except Exception as e:                       # 한 묶음이 죽어도 나머지는 돈다
            import traceback
            traceback.print_exc()
            check(t.__name__, False, f'예외 {type(e).__name__}: {e}')
    print(f'{_n - len(_fail)}/{_n} 통과')
    for f in _fail:
        print('  ' + f)
    return 1 if _fail else 0


if __name__ == '__main__':
    sys.exit(main())
