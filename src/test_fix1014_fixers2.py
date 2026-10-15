# -*- coding: utf-8 -*-
"""
10-14 2차 검토 — fixers2 묶음(verify.fix_over1500 의 '초과분' 규칙 가르기) 회귀 시험. API 호출 없음.

    python src/test_fix1014_fixers2.py

1차 수정(결함 28)이 '초과분'이 연금수령한도 규칙(doc39, 초과분만 연금외수령)인지 1,500만 원 규칙(doc38, 넘으면 전액)인지를
'한도'·'기타소득'이라는 낱말로 갈랐다. 그래서 '분리과세 한도·연금소득 한도 1,500만 원'(110·111·114), 같은 문장 앞의
'연금수령한도'(112), 뒤의 '기타소득'(113·115)만 보고 틀린 1,500만 원 문장을 건너뛰었다. 이제 초과분을 나누는 기준이
무엇을 부른 값인지로 가른다. 1차 시험(test_fix1014_fixers.py)의 한도 규칙 문장은 계속 그대로여야 한다.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import verify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
passed, total = 0, 0


def ok(cond, msg):
    global passed, total
    total += 1
    if cond:
        passed += 1
    else:
        print('실패:', msg)


def n_over(a):
    return verify.fix_over1500(a)[1]


def site_is_limit(a, frag):
    """답 a 에서 frag 바로 뒤 '초과분' 자리를 한도 규칙으로 보는가(답의 다른 줄이 걸려도 그 자리만 따로 본다)."""
    i = a.index(frag) + len(frag)
    m = verify._PART_ONLY.search(a, i)
    assert m and m.start() - i < 5, frag
    return verify._limit_rule_excess(a, m)


# ── 재현 입력(110~113) — 고치기 전엔 n=0, 바로잡기 줄이 붙어야 한다
REPRO = {
    110: '연금소득 분리과세 한도 1,500만 원을 넘으면 초과분은 16.5%예요.',
    111: '연간 연금소득 한도 1,500만 원을 넘으면 초과분만 16.5%로 과세돼요.',
    112: '연금수령한도 안에서 받아도 연금소득이 1,500만 원을 넘으면 초과분만 16.5%예요.',
    113: '1,500만 원을 넘으면 초과분은 기타소득처럼 16.5%가 붙어요.',
}
for idx, s in REPRO.items():
    a, k = verify.fix_over1500(s)
    ok(k == 1 and a.endswith(verify.OVER1500_NOTE), f'{idx}: 틀린 1,500만 원 문장인데 건너뜀 — {s}')

# ── 114 — 저장된 답의 '연금소득 분리과세 한도는 연 1,500만 원이므로, 초과분 300만 원 … 별도 과세' 자리
S114 = '- 연금소득 분리과세 한도는 **연 1,500만 원**이므로, 초과분 **300만 원**(1,800만 원 - 1,500만 원)에 대해 별도 과세됩니다.'
ok(n_over(S114) == 1, '114: 그 문장만 있는 답을 놓침')
p = os.path.join(ROOT, 'eval', '_a1_ab_v2.json')
if os.path.exists(p):
    rows = [r['answer'] for r in json.load(open(p, encoding='utf-8'))['rows'] if '분리과세 한도는 **연 1,500만 원**' in (r.get('answer') or '')]
    ok(rows and all(not site_is_limit(a, '이므로, ') for a in rows), f'114: 저장된 답 {len(rows)}개의 그 자리를 한도 규칙으로 봄')
    ok(rows and all(n_over(a) == 1 for a in rows), '114: 저장된 답 전체 바로잡기')

# ── 115 — 저장된 답의 '연금 수령 한도 초과 시: 초과분(2,000만원 - 1,500만원)은 16.5% 기타소득세' 자리(1,500만 원 규칙을 섞어 씀)
S115 = ('| **연금 한도 내** | 1,500만원 | 4.4% (67세) | 1,500만원 × 4.4% = **66만원** |\n\n### 중요 포인트:\n'
        '1. **연금 수령 한도 초과 시**:  \n   - 초과분(**2,000만원 - 1,500만원**)은 **16.5% 기타소득세** 부과 대상입니다.  \n')
ok(n_over(S115) == 1, '115: 표 첫 줄과 그 문장만 있는 답을 놓침')
p = os.path.join(ROOT, 'eval', '_dialog_calc_a.json')
if os.path.exists(p):
    found = []

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and '초과분(**2,000만원 - 1,500만원**)' in x:
            found.append(x)
    walk(json.load(open(p, encoding='utf-8')))
    ok(found and all(not site_is_limit(a, '한도 초과 시**:  \n   - ') for a in found), '115: 저장된 답의 그 자리를 한도 규칙으로 봄')
    ok(found and all(n_over(a) == 1 for a in found), '115: 저장된 답 전체 바로잡기')

# ── 1,500만 원 기준을 '한도'·'기준'으로 부른 말 — 낱말이 아니라 무엇을 부른 값인지로 1,500만 원 규칙
for s in ('분리과세 한도(1,500만 원)를 넘는 초과분은 16.5%예요.',
          '연금소득 종합과세 한도 1,500만 원 초과분은 16.5%예요.',
          '종합과세 기준 1,500만 원을 넘으면 초과분만 과세돼요.',
          '사적연금소득 1,500만 원 초과분은 16.5%예요.',
          '1,500만 원 한도 초과분은 16.5%예요.',                     # 한도 바로 뒤라도 1,500만 원 한도면 건너뛰지 않는다
          '연간 1,500만 원까지는 5.5%, 초과분은 16.5%예요.',
          '연금소득 한도: 1,500만 원\n초과분: 16.5%',                  # 초과분 줄에 기준이 없으면 바로 앞 1,500만이 무엇인지 본다
          '연금소득이 연 1,500만 원을 넘으면 초과분은 기타소득세 16.5%예요.',
          '연금수령한도는 1,500만 원이고 한도를 넘는 초과분은 연금외수령이에요. 연금소득 1,500만 원 초과분은 16.5%예요.',
          '- **1,500만원 이내 수령**: 연금소득세 5.5%~3.3% 적용.\n- **1,500만원 초과 수령**: 초과분에 16.5% 기타소득세 부과.',
          '세액공제 받은 금액+운용수익 1,800만원 중 **1,500만원 한도 초과분 300만원**에 대해 종합과세 또는 16.5% 선택 가능'):
    a, k = verify.fix_over1500(s)
    ok(k == 1 and a.endswith(verify.OVER1500_NOTE), f'1,500만 원 규칙 문장 바로잡기: {s[:60]}')

# ── 한도 규칙 — 1,500만 원이 연금수령한도 값이거나 '한도를 넘는' 초과분은 그대로(1차 결함 28이 다시 나면 안 된다)
for s in ('연금수령한도는 1억 원 ÷ 8 × 120% = 1,500만 원이에요.\n- 초과분(2,000만 원 − 1,500만 원)은 16.5% 기타소득세예요.',
          '한도 안 1,500만 원은 5.5%, 초과분 500만 원은 16.5% 기타소득세예요.',
          '연금수령분 1,500만 원은 5.5%, 초과분 500만 원은 16.5%예요.',
          '연금수령한도는 1,500만 원이에요. 2,000만 원 받으면 초과분 500만 원은 16.5% 기타소득세예요.',
          '연금수령한도 1,500만 원을 넘는 초과분 500만 원은 16.5% 기타소득세예요.',
          '1,500만 원은 연금수령한도예요. 연금수령한도 초과분은 기타소득세 16.5%예요.',
          '올해 연금수령한도: **1,500만 원**\n- 한도 초과분 500만 원: 16.5% 기타소득세',
          '연금수령한도(1,500만 원)를 넘는 초과분 500만 원은 16.5%예요.',
          '올해 한도 1,500만 원을 초과한 금액만 기타소득세 16.5%로 과세돼요.',
          '연금 1,500만 원 중 초과분 500만 원은 연금외수령이라 16.5% 기타소득세예요.',
          '퇴직금 재원은 연금수령한도 1,500만 원 안이면 퇴직소득세 30% 감면, 한도를 넘는 초과분은 기존 퇴직소득세 그대로예요.'):
    ok(n_over(s) == 0, f'한도 규칙 초과분은 그대로: {s[:60]}')

# 1차 결함 28 재현 답(규칙 계산과 같은 한도 답)도 그대로
A28 = ('올해 연금수령한도는 1억 원 ÷ (11 − 3) × 120% = **1,500만 원**이에요. 2,000만 원 중 한도를 넘는 초과분 500만 원은 '
       '연금외수령이라 16.5% 기타소득세 82만 5천 원, 한도 안 1,500만 원은 5.5% 연금소득세 82만 5천 원으로 합계 **165만 원**이에요.')
ok(n_over(A28) == 0, '결함 28 한도 답에 바로잡기 줄이 붙음')

# ── 1차에서 _SENT_END 를 같은 이름으로 다시 정의해 숫자 대조의 _sentence 가 HEAD 와 다르게 문장을 잘랐다 — 원래대로
ok(verify._SENT_END.pattern == r'[\n!?]|(?<!\d)\.|\.(?!\d)', f'_SENT_END 가 바뀜: {verify._SENT_END.pattern}')
ok(verify._sentence('세금이 붙습니다.2025년부터 바뀝니다', 12) == '2025년부터 바뀝니다', '_sentence 문장 자르기가 HEAD 와 다름')
# 다른 고치기(fix_income_basis)의 _BASIS 와 이름이 겹치면 한쪽이 덮인다
ok(verify._BASIS.pattern.startswith('(총급여') and 'lim' in verify._EXCESS_BASIS.pattern, '정규식 이름 겹침')
ok(verify.fix_income_basis('총급여가 7,000만 원이라면 이는 종합소득금액 4,500만 원을 초과하므로, 13.2%')[1] == 1, 'fix_income_basis 그대로')

print(f'{passed}/{total} 통과')
sys.exit(0 if passed == total else 1)
