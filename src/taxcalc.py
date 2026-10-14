# -*- coding: utf-8 -*-
"""
세금 규칙 계산(A1) — 연금 세금 숫자를 모델에 맡기지 않고 코드로 계산해 근거로 넣는다.

왜 — 시연 질문 "62살, IRP에서 연 1,800만 원 받으면 세금?"에 모델이 "1,500만 원까지만 5.5%"라고
답했다. 문서(doc38)는 1,500만 원을 넘으면 **초과분이 아니라 전액**이 종합과세 또는 16.5% 분리과세라고
적고 있다. 저장된 답변 2,249개 중 16개가 같은 오독이었다(Q-013 13개, Q-025·035·050). 그 문장이 든 조각은
검색 11위라 근거 10개 안에 못 들어오는 경우도 있었다.

규칙과 숫자는 모두 문서 원문에서 가져온다(RULES — 파일과 원문 문장, test_rules 가 원문 일치를 검사).
평가셋 정답(answer_checklist)에서 규칙을 가져오지 않는다.

원칙 — **틀린 숫자를 자신 있게 내는 것이 가장 나쁘다.** 그래서 세 단계로 나눈다.
  계산   입력이 하나로 분명할 때만 숫자를 낸다
  규칙만 조건이 갈리거나(퇴직금 재원·종신·55세 전·해지·여러 소득 섞임) 기간이 불분명하면 숫자 없이 해당 규칙만
  침묵   계좌 종류가 없거나(국민연금일 수 있다) 체계가 다르면((구)개인연금·보험·주택연금) 아무것도 넣지 않는다
처음 구현을 3개 관점으로 검토해 20여 개 오계산을 찾았고(월 납입을 연으로, 국민연금 금액을 연금저축으로,
가입 N년차를 수령연차로 …) 그 사례를 test_rules.py 에 고정했다.

계산하는 것:
  payout     연금 수령 세금 — 나이별 5.5/4.4/3.3%, 연 1,500만 원 초과 시 전액 종합과세 또는 16.5%
  deduction  세액공제 — 총급여 5,500만/종합소득 4,500만 기준 16.5/13.2%, 연금저축 600·합산 900만,
             ISA 만기 전환 10%(최대 300만) 추가, 실제 납입액 상한
  limit      연금수령한도 — 평가액 ÷ (11 − 연금수령연차) × 120%, 초과 인출분 16.5% 기타소득세

이 모듈은 clova·answer 를 부르지 않는다 — test_rules.py 에서 API 없이 검사한다.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 근거 원문. 계산 블록에 그대로 실린다. test_rules.py 가 파일 안에 글자 그대로 있는지 검사한다.
# ---------------------------------------------------------------------------
RULES = {
    'rate_table': ('doc38.txt',
                   '연금 수령 시기 (연금수령일 현재) | 연금소득세율 | 종신연금 수령 시\n'
                   '55세 이상 70세 미만 | 5.5% | 3.3%\n'
                   '70세 이상 80세 미만 | 4.4% | 3.3%\n'
                   '80세 이상 | 3.3% | 3.3%'),
    'over_1500': ('doc38.txt', '*연간 1,500만원 초과 수령 시 전액 다른 소득과 합산 종합과세 또는 16.5% 세율로 분리과세'),
    'whole_amount': ('doc38.txt', '16.5% 세율을 적용할 때 1,500만원을 초과하는 금액에 대해 16.5%가 아니라 '
                                  '전체금액에 대해 16.5% 세율이 적용된다는 점은 기억해두자.'),
    'progressive': ('doc38.txt', '종합과세에 해당되는 경우 최소 6.6%에서 최대 49.5% (지방소득세포함)의 세율이 적용된다.'),
    'excluded': ('doc38.txt', '이 1,500만원을 판단할 때 세액공제 안받은 돈과 퇴직금은 포함되지 않는다.'),
    'all_accounts': ('doc38.txt', '사적연금소득을 1년에 1,500만원 초과 수령 시 (전 금융기관의 연금계좌 합산) 종합과세 되지만'),
    'not_always': ('doc38.txt', '종합과세에 해당되었더라도 타소득과 사적연금소득을 합쳐서 계산한 세율이 16.5%를 넘지 않는다면 '
                                '16.5% 세율을 적용하는 것이 오히려 불리할 수 있다.'),
    'retire_rate': ('doc38.txt', '실제 연금 수령 연차가 10년 이하일 때는 퇴직소득 세율의 70%에 해당하는 세율로 연금소득세가 '
                                 '부과되며 11년 초과 20년 이하일 때는 퇴직소득 세율의 60%에 해당하는 세율, 20년을 초과하면 '
                                 '퇴직소득 세율의 50%에 해당하는 세율로 부과된다.'),

    'ded_limits': ('doc41.txt', '연금저축은 연600만원, IRP는 연금저축 납입액을 포함해서 연900만원이다.'),
    'ded_rates': ('doc41.txt', '직장인은 총급여 5,500만원, 종합소득이 있으면 종합소득금액 4,500만원이 기준이 되는데 '
                               '그 금액 이하면 16.5%, 초과하면 13.2%가 적용된다.'),
    'ded_cap_tax': ('doc41.txt', '차감할 세금이 있으면 최대 금액까지 세액공제되지만 애초에 납부할 세금이 작으면 '
                                 '세액공제액도 그 금액만큼 줄어든다.'),
    'ded_need_income': ('doc41.txt', '종합소득이 있어야 세액공제 혜택을 볼 수 있다.'),
    'early_out': ('doc41.txt', '물론 인출금액이 과세재원이면 16.5% 기타소득세가 적용된다.'),
    'isa_to_savings': ('doc4.txt', 'ISA→연금저축계좌 전환 연도의 세액공제 대상 한도액은 600 만원+300 만원 = 900 만원'),
    'isa_to_irp': ('doc4.txt', '만약, IRP 계좌로 전환한 경우는 900 만원+300 만원 = 1,200 만원'),
    'isa_formula': ('doc4.txt', 'MIN { (만기 ISA 순납입액) X 10% , 300 만원 }'),
    'isa_extra': ('doc23.txt', '계좌에 입금한 금액의 10%를 300만원 한도로 추가 세액공제 혜택을 준다.'),
    'isa_separate': ('doc23.txt', '연금계좌의 납입한도는 연 1,800만원이지만 그와 별도로 ISA만기금액은 입금할 수 있다.'),

    'pension_req': ('doc39.txt', '연금수령이란 먼저, 연금계좌 가입기간 5년 이상이면서 둘째, 만55세 이후에 인출해야 하고, '
                                 '마지막으로 연간 연금수령한도 이내에서 인출하여야 한다.'),
    'limit_formula': ('doc39.txt', '연금수령한도 = 연금계좌 평가액(매년 1월 1일 또는 연금개시일 기준 평가액) ÷ '
                                   '(11 − 연금수령연차) × 120%'),
    'limit_year1': ('doc39.txt', '처음 연금개시가 가능한 해를 연금수령연차 1년차로 본다.'),
    'year_count': ('doc39.txt', '연금수령연차는 실제 연금 수령 여부와 무관하게 연금개시가 가능한 순간부터 매년 누적된다.'),
    'year6': ('doc39.txt', '2013년 3월 1일 이후 가입한 연금계좌는 1년차부터 시작하지만, '
                           '2013년 3월 1일 이전 가입한 연금계좌는 6년차로 기산한다.'),
    'over_limit': ('doc39.txt', '세액공제 납입금과 운용수익에서 한도 초과하는 수령하는 금액은 연금외수령으로 분류된다. '
                                '전액 16.5% 기타소득세가 부과된다.'),
    'limit_rates': ('doc39.txt', '만55 ~69세는 5.5%, 만70세 ~ 79세는 4.4%, 만80세 이상은 3.3%다.'),

    # 재원별 인출(퇴직금이 섞인 계좌·55세 전 해지)
    'no_period_req': ('doc39.txt', '연금계좌에 퇴직금이 있을 경우 가입기간 요건은 사라진다.'),
    'order': ('doc39.txt', '① 세액공제를 받지 않은 돈이 먼저 나가고, ② 퇴직금이 그 다음, ③ 세금혜택을 받은 돈과 수익이 '
                           '가장 나중에 나가는 구조다.'),
    'free': ('doc39.txt', '세액공제를 받지 않은 납입금(ISA전환금 포함)이 있을 경우 인출할 때에도 전액 비과세이며, '
                          '연금수령한도 초과분에 대해서도 과세되지 않는다.'),
    'retire_in': ('doc39.txt', '연금계좌로 이전한 퇴직금을 연금수령한도 내에서 받으면 원래 납부해야 하는 퇴직소득세의 '
                               '50% ~ 70%만 납부하면 된다.'),
    'retire_years': ('doc39.txt', '1~10년차에는 30% 감면, 11~ 20년차에는 40% 감면, 21년차부터는 50% 감면이 적용된다.'),
    'retire_over': ('doc39.txt', '다만 연금수령한도를 초과한다면 초과분에 대해서는 기존 퇴직소득세 그대로 납부해야 한다.'),
    'actual_year': ('doc40.txt', '연금수령연차는 ‘연금수령한도’를 결정하고, 연금실제수령연차는 ‘퇴직소득세 감면율’을 결정한다.'),
    'early_retire': ('doc1.txt', '이연퇴직소득 | 퇴직소득세 | 이연퇴직소득세의 100%'),
    'early_credit': ('doc1.txt', '소득·세액공제 받은 납입금액 또는 운용수익 | 기타소득세 | 16.5% ※ 지방소득세 포함'),
    'unavoidable': ('doc20.txt', '부득이한 사유에 해당하면 중도 인출 시에도 전액 5.5% ~ 3.3% 저율의 연금소득세를 적용 받는다.'),
    # 부득이한 사유의 범위·절차(doc20). 요양은 세법상 3개월 이상 — 근퇴법 중도인출(R-13)의 6개월과 다르다(doc20 표 주석)
    'unavoidable_care': ('doc20.txt', '*3개월 이상 요양 시 적용 (단, 200만원 + 의료실비 + 휴직월수 x 150만원 한도)'),
    'unavoidable_sep': ('doc20.txt', '부득이한 사유로 인출 시 연금개시 전·후 불문하고 연금수령으로 보아 전액 분리과세된다. '
                                     '연금소득 인출액이 연간 1,500만원을 초과하더라도 다른 소득과 합산되지 않는다.'),
    'unavoidable_doc': ('doc20.txt', '저율과세 혜택을 받기 위해서는 해당 사유가 확인된 날부터 6개월 이내에 그 사유를 확인할 수 있는 '
                                     '서류를 갖추어 금융회사에 제출하여야 한다.'),
    'unavoidable_emigrate': ('doc20.txt', '사망·해외이주는 중도인출 사유가 아니다. IRP에서 인출을 원한다면 부분 인출이 아닌 '
                                          '전액 해지만 가능하다. 그런데 세법상 부득이한 사유에는 해당한다.'),
    'emigrate_proof': ('doc20.txt', '단순 장기체류나 영주권 취득만으로는 부득이한 사유로 인정되지 않는다.'),
    'emigrate_retire': ('doc20.txt', '퇴직금 입금일로부터 3년이 경과한 경우에 퇴직금에 한해 연금수령으로 보아 퇴직소득세의 30% 감면이 가능하다.'),
    'house_not_unavoidable': ('doc20.txt', '주택 구입과 전세보증금은 IRP 중요 인출 사유에 해당하지만, 소득세법상 부득이한 사유는 아니다. '
                                           '따라서 이 경우에는 기타소득세 16.5%가 부과된다.'),
    'irp_reason_req': ('doc20.txt', '중도인출 사유를 법으로 열거하고 있어서, 사유에 해당하지 않으면 만 55세 이전에는 중도인출 자체가 불가능하다.'),
    # 해지 신청 전 확인 — 연금저축은 해지 신청 때 과세재원이 확정된다(doc5 업무 문서), IRP 는 재원확정(doc19)
    'cancel_fix': ('doc5.txt', '해지신청 시 과세재원 확정, 국세청 통보(해지신청 후 과세재원 변경 및 그에 따른 환급 불가)'),
    'cancel_fix_exc': ('doc5.txt', '단, 해지 이후 전년도 납입금액에 대해 소득공제를 받지 않음을 증빙 시 과세재원 변경 및 환급가능'),
    'irp_fix': ('doc19.txt', '금융기관의 연금계좌 과세대상금액과 국세청 소득·세액공제합계액을 확인하여 국세청 합계액을 '
                             '초과하는 과세대상금액을 과세제외함으로써 과세대상금액을 일치시켜주는 업무입니다.'),
    'irp_fix_doc': ('doc19.txt', '재원확정 필요서류 -연금보험료등 소득·세액공제확인서(국세청자료)'),
    # 퇴직연금(DC·IRP) 중도인출·담보 — 10-13 외부 시험: DC 가입자의 '집 사려고 중도인출'에 '16.5%'로 단정했고(퇴직금은 퇴직소득세),
    # '퇴직연금 담보대출 돼?'에 '문서에 없다'고 답했다(doc16·doc20 에 있음)
    'dc_withdraw_ok': ('doc49.txt', '제도별로는 DC(확정기여형)제도, IRP제도의 경우 중도인출이 가능하며 DB(확정급여형)제도의 경우는 중도인출이 허용되지 않습니다.'),
    'house_req': ('doc49.txt', '신청시기 : 주택매매계약 체결일로부터 소유권 이전 등기후 1개월 이내'),
    'dc_submit': ('doc49.txt', '서류 구비후, DC 중도인출은 재직 중인 회사에 제출, IRP 중도인출은 가까운 지점에 내점하여 신청가능'),
    'dc_tax': ('doc55.txt', '과세 | 퇴직금 : 퇴직소득세 / 가입자부담금 : 기타소득세'),
    'loan_no': ('doc16.txt', '퇴직연금제도(중소기업퇴직연금기금제도를 포함한다. 이하 이 조에서 같다)의 급여를 받을 권리는 양도 또는 압류하거나 '
                             '담보로 제공할 수 없다.'),
    'loan_ok': ('doc16.txt', '가입자는 주택구입 등 대통령령으로 정하는 사유와 요건을 갖춘 경우에는 대통령령으로 정하는 '
                             '한도에서 퇴직연금제도의 급여를 받을 권리를 담보로 제공할 수 있다.'),
    'loan_repay': ('doc20.txt', '퇴직연금제도를 받을 권리를 담보로 제공하고 대출받은 가입자가 그 대출 원리금을 상환하기 위한 경우 | O | X | 16.5%  기타소득세'),
    # DC 적립금을 연금으로 받을 때 — 10-13 외부 시험 22번: 'DC 연금으로 받으면 비과세야?'에 적립금 전체에 연금소득세 5.5%를 붙였다.
    # DC 운용수익도 퇴직 때 퇴직소득(doc42), 감면은 퇴직소득세에만·세액공제 받은 돈과 수익은 연금소득세(doc51)
    'dc_profit': ('doc42.txt', 'DC계좌에서 운용하면서 발생한 운용수익도 마찬가지로 인출 전 까지 과세이연되고 이후 중도인출 혹은 퇴직 시 '
                               '퇴직소득으로 과세된다.'),
    'dc_irp_route': ('doc51.txt', 'DC법정퇴직금 | IRP 의무이전 | IRP·일반계좌 선택가능 (IRP 수령 시 연금저축으로 계약이전 가능)'),
    'retire_only': ('doc51.txt', '이 감면은 퇴직소득에서 발생한 퇴직소득세에만 적용된다. 연금계좌 내에서 세액공제를 받은 납입금과 운용수익에는 '
                                 '연금소득세(3.3% ~ 5.5%)가 별도로 적용된다.'),

    # 중도인출·운용 규칙(R-12~R-15) — 금액 계산은 없고 규칙만
    'savings_withdraw': ('doc1.txt', '중도인출 | 일부 or 전액 인출 가능'),
    'irp_withdraw_ok': ('doc14.txt', '일정 조건을 충족하면 중도인출이 가능합니다.'),
    'irp_reasons': ('doc14.txt', '1. 무주택자인 가입자가 본인 명의로 주택을 구입하는 경우'),
    'risk_limit': ('doc14.txt', '위험자산(ETF, 주식형 펀드 등) 투자 비중은 전체 적립금의 70% 이내로 제한이 됩니다.'),
    'old_pension': ('doc1.txt', '가. 개인연금저축: 2000.12.31 이전에 개설된 계좌'),
    'old_no_transfer': ('doc22.txt', '개인연금저축은 소득세법에 따른 이체 적용 대상에서 제외. 연금저축계좌만 가능'),
}


def rule(key):
    return RULES[key][1]


# ---------------------------------------------------------------------------
# 규칙표 R-01 ~ R-15 (발표·온톨로지 07_연금온톨로지.md 의 번호). 원문 키를 규칙 번호로 묶는다.
# 계산 블록·답변 근거·시연 화면이 같은 번호를 쓴다. 'how' 는 이 규칙을 코드가 어떻게 쓰는지.
# ---------------------------------------------------------------------------
RULEBOOK = [
    ('R-01', '세액공제 한도', ['ded_limits'], '연금저축 600만 원, 연금저축+IRP 합산 900만 원까지 공제 대상', '계산'),
    ('R-02', '세액공제율', ['ded_rates', 'ded_cap_tax', 'ded_need_income'],
     '총급여 5,500만 원(종합소득 4,500만 원) 이하 16.5%, 초과 13.2%', '계산'),
    ('R-03', 'ISA 만기 전환 추가 공제', ['isa_extra', 'isa_formula', 'isa_to_savings', 'isa_to_irp', 'isa_separate'],
     '전환액의 10%, 최대 300만 원 추가 공제 대상', '계산'),
    ('R-04', '연금수령 요건', ['pension_req', 'no_period_req'],
     '만 55세 이상 + 가입 5년 + 한도 이내. 퇴직금이 있으면 5년 요건 없음', '판정'),
    ('R-05', '연금수령한도', ['limit_formula', 'limit_year1', 'year_count', 'year6'],
     '평가액 ÷ (11 − 연금수령연차) × 120%. 2013.3.1 전 가입은 6년차부터', '계산'),
    ('R-06', '인출 순서', ['order', 'free'], '① 공제 안 받은 돈 → ② 퇴직금 → ③ 공제받은 돈·수익', '계산'),
    ('R-07', '연금소득세율', ['rate_table', 'limit_rates'], '55~69세 5.5%, 70~79세 4.4%, 80세 이상 3.3%', '계산'),
    ('R-08', '1,500만 원 분리과세 선택', ['over_1500', 'whole_amount', 'progressive', 'excluded', 'all_accounts', 'not_always'],
     '③ 재원 연금이 연 1,500만 원을 넘으면 전액 16.5% 분리과세 또는 종합과세', '계산'),
    ('R-09', '퇴직소득세 감면', ['retire_rate', 'retire_in', 'retire_years', 'actual_year', 'dc_profit', 'dc_irp_route', 'retire_only'],
     '퇴직금을 한도 안에서 연금으로 받으면 실제수령 1~10년차 30%, 11~20년차 40%, 21년차~ 50% 감면', '계산'),
    ('R-10', '연금외수령 세금', ['over_limit', 'early_out', 'retire_over', 'early_retire', 'early_credit',
                                'cancel_fix', 'cancel_fix_exc', 'irp_fix', 'irp_fix_doc'],
     '③ 재원 16.5% 기타소득세, ② 퇴직금 퇴직소득세 100%, ① 비과세', '계산'),
    ('R-11', '부득이한 사유 인출', ['unavoidable', 'unavoidable_care', 'unavoidable_sep', 'unavoidable_doc',
                                   'unavoidable_emigrate', 'emigrate_proof', 'emigrate_retire', 'house_not_unavoidable'],
     '부득이한 사유면 중도 인출도 연금소득세 5.5~3.3%', '규칙만'),
    ('R-12', '중도인출 가능 여부', ['savings_withdraw', 'irp_withdraw_ok', 'irp_reason_req', 'dc_withdraw_ok', 'dc_submit', 'dc_tax',
                                   'loan_no', 'loan_ok', 'loan_repay'],
     '연금저축은 사유 없이, IRP는 법정 사유가 있어야', '규칙만'),
    ('R-13', '법정 중도인출 사유', ['irp_reasons', 'house_req'], '무주택 주택 구입·전월세 보증금, 6개월 이상 요양, 재난, 파산·회생 등', '규칙만'),
    ('R-14', '위험자산 한도', ['risk_limit'], 'DC·IRP 위험자산 70% 이내', '규칙만'),
    ('R-15', '(구)개인연금저축', ['old_pension', 'old_no_transfer'], '2000.12.31 이전 개설, 연금저축계좌로 이체 불가', '규칙만'),
]
RULE_ID = {k: rid for rid, _, keys, _, _ in RULEBOOK for k in keys}
RULE_NAME = {rid: name for rid, name, _, _, _ in RULEBOOK}


def rule_ids(keys):
    """원문 키들 → 규칙 번호 목록(순서 유지, 중복 제거)."""
    return sorted(dict.fromkeys(RULE_ID[k] for k in keys if k in RULE_ID))


# ---------------------------------------------------------------------------
# 금액 읽기 — 값, 쓰임(role), 기간(period), 출처(src)
# ---------------------------------------------------------------------------
_NUM = r'\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?'
# 1억 2천만 원 · 1억2천5백만 · 3억 · 1,800만 원 · 2천만원 · 1천8백만 · 18,000,000원
_MONEY = re.compile(
    rf'(?P<eok>{_NUM})\s*억(?:\s*(?P<eok_cheon>\d)\s*천)?(?:\s*(?P<eok_baek>\d)\s*백)?(?:\s*(?P<eok_man>{_NUM}))?\s*만?\s*원?'
    rf'|(?<![\d.])(?P<cheon>\d(?:\.\d)?)\s*천(?:\s*(?P<baek>\d)\s*백)?(?:\s*(?P<cman>{_NUM}))?\s*만\s*원?'
    # '천만원이면요?' — 숫자 없는 천만(1,000만). '추천만'처럼 낱말 안의 천은 아니다(C10 대화: 재작성이 '천만 원'을 남기면 금액을 못 읽었다)
    rf'|(?<![\d가-힣])(?P<bcheon>천)(?:\s*(?P<bbaek>\d)\s*백)?\s*만\s*원?'
    # won() 이 쓰는 '33만 6천 원', '1,173만 3,333원' — 만 뒤의 천·원 단위까지 한 금액으로 읽는다
    rf'|(?P<mc>{_NUM})\s*만\s*(?P<mc_cheon>\d)\s*천(?!\s*만)(?:\s*원)?'
    rf'|(?P<mw>{_NUM})\s*만\s*(?P<mw_won>\d{{1,3}}(?:,\d{{3}})?)\s*원'
    rf'|(?P<man>{_NUM})\s*만\s*원?'
    rf'|(?P<won>\d{{1,3}}(?:,\d{{3}})+)\s*원')
# '연봉 6천' '총급여 5천5백' — 소득 자리에서만 '만'이 빠진 표기를 받는다.
# \s*(?:이|가)?\s*(?:약)?\s* 처럼 선택 항목 사이에 \s* 를 겹쳐 두면 공백이 길 때 세제곱으로 느려진다
# (검토: 공백 800개에 2.2초, 그동안 서버 전체가 멈춤 — 정규식 엔진이 GIL 을 놓지 않는다). 선형 꼴로 쓴다.
_INCOME_BARE = re.compile(r'(?:연봉|총\s*급여|급여)\s*(?:(?:이|가|은|는)\s*)?(?:(?:약|대략)\s*)?(\d)\s*천(?:\s*(\d)\s*백)?(?!\s*만|\s*\d)')


def _f(s):
    return float(s.replace(',', '')) if s else 0.0


def money_spans(q):
    """[(만원 단위 값, 시작, 끝)]."""
    out = []
    for m in _MONEY.finditer(q):
        g = m.groupdict()
        if g['eok']:
            v = _f(g['eok']) * 10000 + _f(g['eok_cheon']) * 1000 + _f(g['eok_baek']) * 100 + _f(g['eok_man'])
        elif g['cheon']:
            v = _f(g['cheon']) * 1000 + _f(g['baek']) * 100 + _f(g['cman'])
        elif g['bcheon']:
            v = 1000 + _f(g['bbaek']) * 100
        elif g['mc']:
            v = _f(g['mc']) + _f(g['mc_cheon']) * 0.1
        elif g['mw']:
            v = _f(g['mw']) + _f(g['mw_won']) / 10000
        elif g['man']:
            v = _f(g['man'])
        else:
            v = _f(g['won']) / 10000
        if v > 0:
            out.append((v, m.start(), m.end()))
    return out


# 금액 앞(같은 문장)에서 가장 가까운 출처 말. 국민연금·월급·남편 금액이 연금저축 금액을 대신하지 않게 한다.
_SRC = [
    ('public', r'국민연금|공무원연금|사학연금|군인연금|기초연금|공적연금'),
    ('person', r'남편|아내|배우자|와이프|부인|부모님?|아버지|어머니|아버님|어머님|자녀|아들|딸'),
    ('earned', r'근로소득|월급|연봉|급여|사업소득|임대소득|이자|배당'),
    ('insure', r'(?<!연금저축)보험|주택연금|농지연금'),
    ('retire', r'퇴직금|퇴직급여|퇴직연금|DC|DB'),
    ('private', r'연금저축|IRP|개인형|연금계좌|사적연금'),
]


def _source(q, s):
    pre = re.split(r'[.?!\n]', q[max(0, s - 24):s])[-1]
    # 같은 마디('…받고 남편이 IRP에서 연 2,000만원')에 사람 말이 있으면 계좌 말보다 앞선다 — 남의 돈이다
    clause = re.split(r',|하고\s|받고\s|이고\s|그리고|이랑|랑\s|[와과]\s', pre)[-1]
    if re.search(_SRC[1][1], clause):
        return 'person'
    best, pos = None, -1
    for name, rx in _SRC:
        for m in re.finditer(rx, pre):
            if m.start() > pos:
                best, pos = name, m.start()
    return best


# 규칙을 말하는 나이(55세 이후·70세 미만…)는 고객 나이가 아니다.
# '150 세금은요'의 '50 세'처럼 금액 끝자리와 세금·세액의 '세'를 나이로 읽지 않게 앞뒤를 막는다.
_AGE = re.compile(r'(?:만\s*)?(?<![\d,.])(\d{2})\s*(?:살|세(?![금액율제법]))'
                  r'(?!\s*(?:이후|이상|이하|미만|이전|전|부터|까지|넘|되|가\s*되|도\s*안|도\s*아직|안\s*됐|안\s*되|에\s*(?:연금|개시|받)))')
_DECADE = re.compile(r'([5-9])0\s*대')


_FUTURE_AGE = re.compile(r'(?<![\d,.])(\d{2})\s*(?:살|세)\s*(?:부터|이\s*되면|가\s*되면|되면|되어서|넘어서|넘으면|이후에)')


def rate_age(p):
    """세율을 정할 나이 — 받는 나이를 따로 말했으면 그 나이, 아니면 고객 나이."""
    return p.get('rate_age') or p.get('age')


def customer_age(q):
    """질문에 나이가 하나로 분명하면 그 나이, 아니면 None. 60대처럼 연대면 ('대', 60)."""
    ages = {int(a) for a in _AGE.findall(q) if 19 <= int(a) <= 99}
    if len(ages) == 1:
        return ages.pop()
    if ages:
        return None                     # 나이가 둘 이상 — 누구 나이인지 모른다
    ds = {int(d) * 10 for d in _DECADE.findall(q)}
    if len(ds) == 1:
        return ('대', ds.pop())
    return None


def rate_for_age(age):
    """(세율 %, 구간 설명) 또는 None. 문서 표(doc38) 구간 그대로."""
    if isinstance(age, tuple):              # 연대: 60대 → 55~69 구간에 온전히 든다
        d = age[1]
        if d == 60:
            return 5.5, '55세 이상 70세 미만'
        if d == 70:
            return 4.4, '70세 이상 80세 미만'
        if d >= 80:
            return 3.3, '80세 이상'
        return None                     # 50대는 55세 전후가 섞인다
    if age is None or age < 55:
        return None
    if age < 70:
        return 5.5, '55세 이상 70세 미만'
    if age < 80:
        return 4.4, '70세 이상 80세 미만'
    return 3.3, '80세 이상'


_YEARLY = re.compile(r'(?:1년|일년|연간?|매년|한\s*해|해마다)\s*(?:에|에만|동안)?\s*(?:약|대략|한)?\s*$')
_MONTHLY = re.compile(r'(?:(?<![\d개])월|매달|매월|한\s*달(?:에)?|달마다)\s*(?:에|에만)?\s*(?:약|대략|한)?\s*$')
_INCOME_PRE = re.compile(r'(총\s*급여|연봉|급여|근로소득|종합소득(?:금액)?|사업소득|소득)\s*(?:이|가|은|는|이요|가요)?\s*(?:약|대략|한)?\s*$')
_OTHER_PERSON = re.compile(r'남편|아내|배우자|와이프|부인|부모|아버지|어머니|자녀|아들|딸')
_THRESHOLD_POST = re.compile(r'\s*(?:이하|이상|초과|미만|넘|기준|까지|한도|을\s*넘|를\s*넘)')
_LUMP_CTX = re.compile(r'지금까지|그동안|누적|모은|모았|쌓인|쌓였|평가|총\s*\d|전부|목돈|퇴직금')


# 재원 이름 바로 뒤(조사·'약'까지)에 오는 금액이 그 재원의 금액이다. 끝($)에 붙여 쓴다.
_FUND_TAIL = r'(?:\s*(?:재원|부분|분))?\s*(?:이|가|은|는|이요|만|도)?\s*(?:약|대략|한)?\s*$'
_FUND_PRE = [
    ('free', re.compile(r'(?:(?:(?:세액\s*)?공제\s*(?:를\s*)?(?:안|못)\s*받은|공제\s*받지\s*않은)\s*(?:돈|납입금|납입액|원금|금액)?'
                        r'|비과세\s*(?:원금|재원|납입금)|과세\s*제외\s*(?:금액)?)' + _FUND_TAIL)),
    ('credit', re.compile(r'(?:(?:세액\s*)?공제\s*(?:를\s*)?받은\s*(?:돈|납입금|납입액|원금|금액)'
                          r'(?:\s*(?:과|와|이랑|하고|및|·|,)\s*(?:운용\s*)?수익(?:금)?)?'
                          r'|(?<![가-힣])(?:운용\s*)?수익(?:금)?)' + _FUND_TAIL)),
    ('retire', re.compile(r'(?:퇴직금|퇴직\s*급여|이연\s*퇴직\s*소득)' + _FUND_TAIL)),
]
_RTAX_PRE = re.compile(r'퇴직\s*소득\s*세(?:액)?\s*(?:는|은|이|가)?\s*(?:약|대략)?\s*$')
_EFF = re.compile(r'(?:퇴직\s*소득\s*세\s*)?실효\s*세율\s*(?:은|는|이|가)?\s*(?:약\s*)?(\d+(?:\.\d+)?)\s*%'
                  r'|퇴직\s*소득\s*세율\s*(?:은|는|이|가)?\s*(?:약\s*)?(\d+(?:\.\d+)?)\s*%')
_ACTUAL = re.compile(r'실제\s*(?:연금\s*)?수령\s*(?:연차\s*)?(?:가|는|이)?\s*(\d{1,2})\s*년\s*차?|연금\s*(?:을\s*)?받은\s*지\s*(\d{1,2})\s*년')
# '연금저축 600, IRP 300 넣으면' — 계좌 이름 바로 뒤 단위 없는 숫자(만 원). 납입·공제 질문에서만 읽는다.
_BARE_ACCT = re.compile(r'(?:연금저축(?:펀드|계좌)?|IRP|개인형\s*IRP)\s*(?:에|에만|에는|은|는|만|로|으로)?\s*'
                        r'(\d{2,4})(?!\d|[.,]\d)(?!\s*(?:만|원|%|세|살|년|개|억|천|백|대|호|좌|주|배|건|명|회|차|월|일|시|분))',
                        re.I)
_MENTION = [('retire', r'퇴직금|퇴직\s*급여|이연\s*퇴직'),
            ('credit', r'(?:세액\s*)?공제\s*(?:를\s*)?받은|공제분|세액공제\s*받았'),
            ('free', r'공제\s*(?:를\s*)?(?:안|못)\s*받|공제\s*받지\s*않|비과세\s*원금|과세\s*제외')]


def _income_kind(word):
    if re.search(r'종합소득|사업소득', word):
        return '종합소득'
    if re.search(r'총\s*급여|연봉|급여|근로소득', word):
        return '총급여'
    return None                     # '소득'만으로는 총급여(5,500만)인지 종합소득금액(4,500만)인지 모른다


# 금액과 동사 사이에 끼는 부사 — 'IRP에 300만원 이미 넣었어요'를 납입으로 못 읽었다
_ADV = r'(?:(?:이미|벌써|미리|다|모두|전부|꽉|각각|같이|함께|동시에|둘\s*다)\s*){0,2}'
# '중도인출하면'·'중도 해지하면' — 인출 동사 앞의 '중도'(C10 대화: 금액이 있는데 '인출하려는 금액'을 되물었다)
_OUT_VERB = r'(?:중도\s*)?(?:빼|인출|찾|꺼내|출금|해지)'


def _role(q, s, e, no_isa=False):
    """금액의 쓰임을 앞뒤 말로 가른다 → (role, period, kind). no_isa=True 면 ISA 전환액으로 보지 않는다."""
    pre = q[max(0, s - 14):s]
    post = q[e:e + 14]
    period = 'monthly' if _MONTHLY.search(pre) else 'yearly' if _YEARLY.search(pre) else None
    m = _INCOME_PRE.search(pre)
    if m and '연금' not in pre[-6:]:
        if _THRESHOLD_POST.match(post) or _OTHER_PERSON.search(q[max(0, s - 16):s]):
            return 'skip', None, None   # '총급여 5,500만 원 이하면'은 기준 인용, '남편 연봉'은 남의 소득
        return 'income', None, _income_kind(m.group(1))
    m = re.match(r'\s*(?:정도|쯤)?\s*(?:의|인)?\s*(연봉|총\s*급여|급여|종합소득(?:금액)?|사업소득)', post)
    if m:
        return 'income', None, _income_kind(m.group(1))
    # ISA 전환액 — 금액과 같은 문장 안에 ISA 가 있어야 한다('…900만원 채웠어요. ISA 만기 3천만원…'의 900은 아니다)
    sent_a = max(q.rfind(c, 0, s) for c in '.?!\n') + 1
    sent_b = min([i for i in (q.find(c, e) for c in '.?!\n') if i >= 0] or [len(q)])
    sent = q[sent_a:sent_b]
    around = q[max(sent_a, s - 25):min(sent_b, e + 45)]
    after_move = re.search(r'(?:옮기|옮겨|이체하|전환하)\w{0,2}(?:고|서|뒤|후|면서)\s', q[sent_a:s]) and \
        re.match(r'\s*(?:을|를|씩)?\s*(?:씩)?\s*' + _ADV + r'(?:더\s*)?(?:넣|납입|입금|불입)', post)
    if not no_isa and not after_move and re.search(r'ISA', sent, re.I) and \
            re.search(r'만기|해지|전환|옮|이체|넣|입금|납입', around):
        if re.match(r'\s*(?:의|이|가)?\s*(?:수익|이익|이자|한도|비과세|추가)', post) or \
                re.search(r'(?:매년|연간|매달|매월)\s*$', pre):
            return 'skip', None, None   # ISA 수익·비과세 한도·연간 납입은 전환액이 아니다
        return 'isa', None, None
    # 'IRP 1억 원(퇴직금 6,000만 …)' — 계좌 이름 바로 뒤 금액에 괄호로 구성이 붙으면 평가액이다
    if re.search(r'(?:IRP|연금저축|연금계좌|계좌)\s*(?:에|에는|은|는|이|가)?\s*$', pre, re.I) and re.match(r'\s*\(', post):
        return 'balance', None, None
    # 재원 구성 — '퇴직금 6,000만 원', '공제받은 돈과 수익 4,000만', '퇴직소득세 240만 원'
    wide = q[max(0, s - 26):s]
    if _RTAX_PRE.search(wide):
        return 'rtax', None, None
    if not re.match(r'\s*(?:을|를|씩|만)?\s*(?:씩)?\s*' + _ADV + r'(?:받|수령|' + _OUT_VERB + r'|넣|납입|입금|불입)', post):
        for kind, rx in _FUND_PRE:
            if rx.search(wide):
                return 'fund', None, kind
    if re.match(r'\s*(?:이|가|정도|쯤)?\s*(?:들어\s*있|들어있|있어|있는데|있습니다|모였|모았|쌓였)', post) or \
            re.search(r'(?:평가액|잔액|적립금|계좌에)\s*(?:이|은|는|가)?\s*(?:약|대략)?\s*$', pre):
        return 'balance', None, None
    if re.match(r'\s*(?:을|를|만|정도|쯤)?\s*' + _ADV + r'(?:한\s*번에|한꺼번에|일시에|목돈으로)?\s*' + _OUT_VERB, post):
        return 'withdraw', None, None
    # '내'는 납입('매달 30만 원씩 내고 있어요')일 때만 — '330만 원 내는 거죠'는 세금을 내는 말이다
    if re.match(r'\s*(?:을|를|씩|정도|쯤|으로|로)?\s*(?:씩)?\s*' + _ADV
                + r'(?:더\s*)?(?:넣|납입|입금|저축|불입|채우|채워|채운|채웠|내(?:고|요|서|며|왔|온|는\s*중))', post):
        return 'contrib', period, None
    # '연금저축에 600만원, IRP에 300만원 넣어서' · '연금저축에서 연 1,000만원, IRP에서 연 800만원 받으면'
    # — 나열 뒤에 동사가 온다. 나열을 끝내는 동사가 쓰임을 정한다.
    if re.search(r'연금저축|IRP|개인형', pre, re.I) and not re.match(r'\s*(?:까지|한도|이\s*한도|을\s*넘)', post):
        ahead = re.split(r'[.?!\n]', q[e:e + 45])[0]
        if re.match(r'\s*(?:씩)?\s*(?:,|와|과|하고|이랑|랑|\+|그리고|(?=(?:IRP|연금저축|개인형)))', ahead, re.I):
            verb = re.search(r'(넣|납입|입금|불입|채웠)|(받|수령|나오|나와|나올)', ahead)
            if verb and verb.group(1):
                return 'contrib', period, None
            if verb and verb.group(2):
                return 'payout', period, None
    if re.match(r'\s*(?:을|를|씩|정도|쯤|이|가)?\s*(?:씩)?\s*' + _ADV + r'(?:받|수령|나오|나와|나올|나온|나옵|연금으로\s*받)', post):
        return 'payout', period, None
    return None, None, None


# 부득이한 사유(doc20·doc1). '이민 갑니다'·'해외로 이사'도 해외이주로 읽는다 — 못 읽으면 블록이 16.5% 로 틀을 짠다(Q-087 같은 질문).
# 영주권은 그 자체로는 사유가 아니지만(doc20: 단순 장기체류·영주권 취득만으로는 인정 안 됨) 그 말을 블록에 실어야 하므로 받는다.
_EMIGRATE = re.compile(r'해외\s*이주|이민|(?:해외|외국|국외)(?:로|으로)?\s*(?:이주|이사)|영주권')
_UNAVOIDABLE = re.compile(r'부득이|요양|사망|돌아가(?:시|셨|신)|별세|파산|개인\s*회생|회생\s*절차|천재지변|재난')
_EARLY_HARD = re.compile(r'중도\s*인출|중도\s*해지|55\s*세\s*(?:되기\s*)?전|55\s*세도\s*안')
# '해지'·'깨'는 연금계좌에서 돈을 빼는 말일 때만 55세 전·해지 인출로 본다.
# '주식형으로 바꾸고 싶으면 이거 해지하고 다시 사야' — 계좌 안에서 상품만 바꾸는 말이다(Q-021 같은 질문)
_SWITCH_PRE = re.compile(r'바꾸|바꿔|바꿀|갈아\s*타|교체|리밸런싱')
_SWITCH_POST = re.compile(r'\s*(?:하고|한\s*(?:뒤|후|다음)(?:에)?|해서)?\s*'
                          r'(?:다시\s*(?:사|매수|가입|담)|재매수|갈아\s*타|다른\s*(?:거|걸|것|펀드|상품))')
# 해지하는 대상 — 맨 뒤(가장 가까운) 말이 연금계좌가 아니면(ISA·예금·펀드…) 연금계좌 인출이 아니다
_CANCEL_OBJ = re.compile(r'연금저축(?:펀드|계좌|보험)?|IRP|개인형|연금\s*계좌|계좌|ISA|예금|적금|(?<!연금저축)보험|펀드|ETF|TDF|상품',
                         re.I)


def _early(q):
    if _EARLY_HARD.search(q):
        return True
    for m in re.finditer(r'해지|깨(?:면|서|고)', q):
        clause = re.split(r'[.?!\n,]', q[max(0, m.start() - 25):m.start()])[-1]
        if _SWITCH_PRE.search(clause) or _SWITCH_POST.match(q, m.end()):
            continue
        objs = _CANCEL_OBJ.findall(clause)
        if objs and not re.match(r'연금저축|IRP|개인형|연금\s*계좌|계좌', objs[-1], re.I):
            continue
        return True
    return False


# 단위 없는 금액 — 연금 질문에서는 만 원으로 읽는다. 대화 21개를 끝까지 돌려 보니 '1년에 2,000 받으려는데'·'연 1,800 받을 건데'를
# 못 읽어 계산 블록이 없었고, 모델이 67·68세에 4.4%를 적용했다(C02·C13). '어디에 600 넣는'·'연봉 4,500'도 같은 빈틈.
_BARE_N = r'(\d{1,3}(?:,\d{3})+|\d{2,5})'
_BARE_STOP = r'(?![\d,.]|\s*(?:만|원|%|세|살|년|개|억|천|백|대|호|좌|주|배|건|명|회|차|월|일|시|분|등급|기|위|번|p|P))'
_BARE_PERIOD = re.compile(r'(?:1년에|일년에|한\s*해에?|연간?|매년|매달|매월|한\s*달에?|(?<![\d개])월)\s*(?:약\s*|대략\s*)?' + _BARE_N + _BARE_STOP)
_BARE_VERB = re.compile(r'(?<![\w.,])' + _BARE_N + r'(?=\s*(?:을|를|씩|정도|쯤|이|가)?\s*(?:씩)?\s*' + _ADV
                        + r'(?:넣|납입|입금|불입|채우|채워|채운|채웠|받|수령|빼|인출|찾|꺼내|해지|옮기|옮겨|넘기|넘겨))')
_BARE_INCOME = re.compile(r'(?:연봉|총\s*급여|급여)\s*(?:(?:이|가|은|는)\s*)?(?:(?:약|대략)\s*)?(\d{1,2},\d{3}|\d{4})(?![\d,]|\s*(?:만|원|억|천|년))')
# '매년 2천씩'·'만기금 2천 연금계좌로' — 만 없는 N천(연봉 N천은 _INCOME_BARE 가 소득으로 읽는다)
_BARE_CHEON = re.compile(r'(?<![\d가-힣.])(\d(?:\.\d)?)\s*천(?:\s*(\d)\s*백)?(?!\s*(?:만|원|억|\d|백))'
                         r'(?=\s*(?:을|를|이|가|은|는|씩|정도|쯤)?\s*(?:씩)?\s*(?:연금|IRP|넘기|넘겨|옮기|옮겨|넣|받|빼|인출))')
_BARE_CTX = re.compile(r'연금|IRP|ISA|공제|세금|세율|떼|납입|수령', re.I)


def _product_words():
    """상품 이름에서 숫자 바로 앞에 오는 말 — '라이프사이클 7090'·'거북이 90'·'코스닥 150'."""
    words = {'라이프사이클', '거북이', '롱숏', 'KRX', 'KOSPI', '코스피', '코스닥', 'KOSDAQ', '나스닥', 'NASDAQ', 'S&P', '다우',
             '인덱스', '밸류', '국채', 'TDF', 'KODEX', 'TIGER', 'ACE', 'KBSTAR', 'SOL', 'ARIRANG', 'HANARO', 'KOSEF', '빈티지',
             '타겟데이트', '타깃데이트'}
    try:
        cards = json.load(open(os.path.join(ROOT, 'data', 'fund_cards.json'), encoding='utf-8'))
        for f in cards:
            for m in re.finditer(r'([가-힣A-Za-z&]{2,})\s*\d{2,4}', f.get('fund_name') or ''):
                w = m.group(1)
                words.add(w[-4:] if re.match(r'[가-힣]', w[-1]) else w[-5:])
    except Exception:
        pass
    return words


_PRODUCT_NUM = re.compile(r'(?:' + '|'.join(sorted((re.escape(w) for w in _product_words()), key=len, reverse=True))
                          + r')\s*\d{2,4}', re.I)


def product_spans(q):
    """상품 이름 속 숫자 자리 [(시작, 끝)] — 이 자리의 숫자는 금액이 아니다."""
    return [(m.start(), m.end()) for m in _PRODUCT_NUM.finditer(q or '')]


# 금액이 아니라 확인하려는 결과(세후 실수령·공제 결과)나 빼는 몫 — '세금 떼고 1,503 받는 거죠', '퇴직금 500 빼고 받으면',
# '세액공제 300 받을 수 있나요', '공제 한도 300 넘기나요'(검토: 이런 숫자를 입력으로 읽어 계산 블록을 깎거나 바꿨다)
_NOT_INPUT_PRE = re.compile(r'(?:떼고|세후|실수령(?:액)?|빼고|공제|한도)\s*(?:은|는|이|가|로|으로)?\s*(?:약|대략)?\s*$')
_NOT_INPUT_POST = re.compile(r'\s*(?:을|를|만)?\s*(?:빼고|제외하고)\s*(?:받|나머지|남은)')


def _bare_amounts(q, taken, notes=None):
    """단위 없는 금액 → [(만원 값, 시작, 끝)]. 이미 읽은 금액과 겹치면 뺀다. 펀드 이름의 숫자(TDF 2045)·연도는 금액이 아니다.
    notes 를 주면 어떻게 읽었는지 적는다 — 답의 '가정'에 밝히고, 결과가 크게 갈리는 것은 시연 채널에서 되묻는다(사용자 결정 10-10)."""
    if not _BARE_CTX.search(q):
        return []
    out = []
    prod = product_spans(q)
    for rx in (_BARE_PERIOD, _BARE_VERB, _BARE_INCOME):
        for m in rx.finditer(q):
            raw = m.group(1)
            v = float(raw.replace(',', ''))
            s, e = m.start(1), m.end(1)
            if not 10 <= v <= 99999 or any(a <= s < b or s <= a < e for a, b in taken + prod):
                continue
            # 'TDF 2045'·'TDF2040'처럼 이름 바로 뒤 숫자만 막는다('IRP에서 연 1,800'의 IRP 는 이름이 아니다)
            if ',' not in raw and 2020 <= v <= 2079 or re.search(r'(?:[A-Za-z]|펀드|빈티지)\s*$', q[max(0, s - 8):s]):
                continue
            if _NOT_INPUT_PRE.search(q[max(0, s - 10):s]) or _NOT_INPUT_POST.match(q, e):
                continue
            if v >= 10000:
                # '월 50,000'·'80,000' — 다섯 자리는 원 단위로 쓴 금액이다(만 원으로 읽으면 5억·8억이 된다)
                if notes is not None:
                    notes.append({'raw': raw, 'text': f"'{raw}'을 {won(v / 10000)}(원 단위)으로 봄",
                                  'ask': f"말씀하신 '{raw}'은 {won(v / 10000)}인가요, {won(v)}인가요?"})
                v = v / 10000
            elif notes is not None:
                notes.append({'raw': raw, 'text': f"'{raw}'을 {won(v)}으로 봄", 'ask': ''})
            out.append((v, s, e))
            taken.append((s, e))
    for m in _BARE_CHEON.finditer(q):
        s, e = m.start(), m.end()
        if any(a <= s < b or s <= a < e for a, b in taken + prod) or re.search(r'연봉|급여|소득', q[max(0, s - 8):s]):
            continue
        v = float(m.group(1)) * 1000 + (int(m.group(2)) * 100 if m.group(2) else 0)
        if notes is not None:
            notes.append({'raw': m.group(0).strip(), 'text': f"'{m.group(0).strip()}'을 {won(v)}으로 봄", 'ask': ''})
        out.append((v, s, e))
        taken.append((s, e))
    return out


# 금액이 그 계좌의 금액임을 보이는 앞말 — '연금저축 600', 'IRP에 300'. '연금저축으로'·'IRP에서'는 옮기는 쪽·나오는 쪽이라 뺀다
_ACCT_OWN = re.compile(r'(?:연금저축(?:펀드|계좌)?|IRP|개인형\s*IRP|연금\s*계좌)\s*(?:에|에는|은|는|이|가|도|만)?\s*(?:약|대략)?\s*$',
                       re.I)


def norm_ws(q):
    """공백 덩어리를 하나로 — 줄바꿈 덩어리는 줄바꿈 하나, 나머지 공백(NBSP·EM SPACE 포함)은 띄어쓰기 하나.
    정규식들이 \\s* 를 겹쳐 쓰므로 뜻은 같고, 긴 공백에서 역추적이 폭주하는 일을 막는다
    (검토: 줄바꿈 1,985개짜리 질문에 66초 — 정규식이 GIL 을 놓지 않아 그동안 서버 전체가 멈춘다)."""
    q = re.sub(r'[^\S\n]+', ' ', (q or '')[:2000])
    return re.sub(r'\s*\n\s*', '\n', q)


def parse(q):
    """질문에서 계산에 쓸 입력을 뽑는다. 금액마다 쓰임·기간·출처를 단다."""
    q = norm_ws(q)
    spans = []
    found = money_spans(q)
    taken = [(s, e) for _, s, e in found]
    if re.search(r'넣|납입|입금|불입|공제|환급|돌려\s*받', q):
        for m in _BARE_ACCT.finditer(q):
            v = float(m.group(1))
            if 10 <= v <= 1800 and not any(s <= m.start(1) < e for s, e in taken):
                found.append((v, m.start(1), m.end(1)))
                taken.append((m.start(1), m.end(1)))
    notes = []
    found += _bare_amounts(q, taken, notes)
    found.sort(key=lambda x: x[1])
    for v, s, e in found:
        role, period, kind = _role(q, s, e)
        spans.append({'v': v, 's': s, 'e': e, 'role': role, 'period': period, 'kind': kind, 'src': _source(q, s)})
        mm = re.search(r'(\d{1,2})\s*월\s*(?:에|에만)?\s*$', q[max(0, s - 7):s])
        if mm and role in ('contrib', 'payout', 'withdraw'):
            notes.append({'raw': q[s:e], 'text': f'{mm.group(1)}월에 {won(v)}은 그 달 한 번의 금액으로 봄(매달 아님)', 'ask': ''})
    # ISA 가 든 문장의 금액은 모두 ISA 전환액으로 읽힌다 — '연금저축 600, IRP 300 넣었고 ISA 만기 2천만원 옮기면'의
    # 600·300은 계좌별 납입이다. 전환액 후보가 둘 이상일 때만, 계좌 이름에 바로 붙은 금액(옮기는 말이 뒤따르지 않는)을 다시 읽는다
    isa_sp = [x for x in spans if x['role'] == 'isa']
    if len(isa_sp) >= 2:
        for x in isa_sp:
            sent_a = max(q.rfind(c, 0, x['s']) for c in '.?!\n') + 1
            ahead = re.split(r'[.?!\n,]', q[x['e']:x['e'] + 20])[0]
            own_verb = re.match(r'\s*(?:을|를|씩|은|는|도)?\s*' + _ADV + r'(?:더\s*)?(?:넣|납입|입금|불입|채웠|채우)', ahead)
            if _ACCT_OWN.search(q[sent_a:x['s']]) and (own_verb or not re.search(r'옮|이체|전환', ahead)):
                role, period, kind = _role(q, x['s'], x['e'], no_isa=True)
                if role:                    # 다시 읽어도 쓰임을 모르면('1,500만 원씩 나눠 넣으면') 그대로 둔다
                    x['role'], x['period'], x['kind'] = role, period, kind
    for m in _INCOME_BARE.finditer(q):
        v = int(m.group(1)) * 1000 + (int(m.group(2)) * 100 if m.group(2) else 0)
        kind = _income_kind(m.group(0))
        if not _OTHER_PERSON.search(q[max(0, m.start() - 10):m.start()]):
            spans.append({'v': v, 's': m.start(), 'e': m.end(), 'role': 'income', 'period': None,
                          'kind': kind, 'src': 'earned'})
    p = {'spans': spans, 'age': customer_age(q), 'amount_notes': notes}
    # 55세는 보통 규칙 나이('55세부터 받을 수 있죠?')다. 지금 나이 이하도 받는 나이가 아니다
    fut = {int(a) for a in _FUTURE_AGE.findall(q)
           if 56 <= int(a) <= 99 and (not isinstance(p['age'], int) or int(a) > p['age'])}
    p['rate_age'] = fut.pop() if len(fut) == 1 and fut != {p['age']} else None
    p['payout'] = [x for x in spans if x['role'] == 'payout']
    incomes = [x for x in spans if x['role'] == 'income']
    p['income'] = (incomes[0]['kind'], incomes[0]['v']) if len(incomes) == 1 and incomes[0]['kind'] else None
    p['income_unknown_kind'] = len(incomes) == 1 and not incomes[0]['kind']
    isas = [x for x in spans if x['role'] == 'isa']
    p['isa'] = isas[0]['v'] if len(isas) == 1 else None
    # 'ISA 3천만 원을 연금저축과 IRP에 반반(1,500만 원씩) 나눠 넣으면' — 나눠 옮기는 경우도 계산한다(C07 대화: 블록이 IRP 경우만 보여
    # 모델이 '연금저축 750 + IRP 1,050 → 296.99만 원'을 지어냈다)
    p['isa_split'] = bool(re.search(r'반반|반씩|절반|나눠|나누어', q) and '연금저축' in q and re.search(r'IRP|개인형', q, re.I))
    if p['isa'] is None and p['isa_split'] and len(isas) == 2:
        big, small = sorted((x['v'] for x in isas), reverse=True)
        if abs(big - 2 * small) < 1:
            p['isa'] = big                  # '3,000만 원을 1,500만 원씩' — 큰 쪽이 전환액
    bals = [x for x in spans if x['role'] == 'balance']
    p['balance'] = bals[0]['v'] if len(bals) == 1 else None
    wds = [x for x in spans if x['role'] == 'withdraw']
    p['withdraw'] = wds[0]['v'] if len(wds) == 1 else None
    p['contrib'] = []
    for x in spans:
        if x['role'] != 'contrib':
            continue
        clause = re.split(r'[.?!\n,]', q[max(0, x['s'] - 20):x['s']])[-1]
        if _LUMP_CTX.search(clause) or x['src'] == 'retire' and '퇴직금' in clause:
            continue                        # 쌓인 돈·퇴직금 입금은 올해 납입액이 아니다
        v = x['v'] * 12 if x['period'] == 'monthly' else x['v']
        if v > 1800 and x['period'] is None:
            continue                        # 연 납입한도(1,800만)를 넘는 금액은 한 해 납입으로 보지 않는다
        # 금액 바로 앞에서 가장 가까운 계좌 이름 — 'IRP 300이랑 연금저축 900만'의 900은 연금저축이다
        names = re.findall(r'IRP|개인형|DC|퇴직연금|연금저축', clause, re.I)
        acct = 'savings' if not names or names[-1] == '연금저축' else 'irp'
        p['contrib'].append({'v': v, 'acct': acct, 'monthly': x['period'] == 'monthly', 'raw': x['v']})

    # 재원 구성(재원별 인출 계산용). 같은 재원 금액이 둘이면 어느 것인지 모르므로 쓰지 않는다
    p['fund'] = {}
    for kind in ('free', 'retire', 'credit'):
        vs = [x['v'] for x in spans if x['role'] == 'fund' and x['kind'] == kind]
        if len(vs) == 1:
            p['fund'][kind] = vs[0]
    p['fund_mentioned'] = {k for k, rx in _MENTION if re.search(rx, q)} | set(p['fund'])
    if 'retire' not in p['fund'] and _RETIRE_NONE.search(q):
        p['fund_mentioned'].discard('retire')
    # 평가액과 재원 구성을 밝힌 계좌에서 기간 없이 '1,800만 원을 수령한다면'은 올해 꺼내는 금액이다(해마다 받는 연금액이 아니다).
    # 대화 기억이 '올해 1,800만 원 빼면'을 이렇게 바꿔 쓰면 재원별 인출(57만 6천 원) 대신 규칙과 되묻기로 빠졌다(10-12 시연 점검)
    if p['withdraw'] is None and p['balance'] is not None and p['fund'] and len(p['payout']) == 1:
        x = p['payout'][0]
        if x['period'] is None and x['v'] <= p['balance'] and not re.match(r'\s*씩', q[x['e']:]):
            x['role'] = 'withdraw'
            p['withdraw'], p['payout'] = x['v'], []
    # '이직하면서 퇴직금 3,000만 원이 IRP로 들어왔어요. 이거 해지하면 세금은?' — 꺼낼 금액 없이 '해지'만 말하면 계좌 전부를 꺼내는
    # 것이다(12 문서 H·결정 D-6, 발표 8장 예시: 금액을 알려 줘도 '해지하려는 금액'을 다시 물었다). 평가액이 있으면 평가액,
    # 없으면 밝힌 재원 금액의 합을 인출액으로 본다. '일부'·'중도인출'·'만 빼면'은 전부 해지가 아니다
    p['cancel_all'] = False
    if p['withdraw'] is None and not p['payout'] and re.search(r'해지|해약|깨(?!끗)', q) and \
            not re.search(r'일부|부분|중도\s*인출|만\s*(?:빼|꺼내|찾|인출)', q):
        total = p['balance'] if p['balance'] is not None else (sum(p['fund'].values()) if p['fund'] else None)
        if total:
            p['withdraw'], p['cancel_all'] = total, True
    rt = [x['v'] for x in spans if x['role'] == 'rtax']
    p['rtax'] = rt[0] if len(rt) == 1 else None
    m = _EFF.search(q)
    p['retire_eff'] = float(m.group(1) or m.group(2)) if m else None
    m = _ACTUAL.search(q)
    p['actual_n'] = int(m.group(1) or m.group(2)) if m else None
    q_y = _ACTUAL.sub(' ', q)               # '실제 수령 12년차'는 연금수령연차가 아니다(doc40)

    # 연금수령연차 — '가입 5년차'·'근무 3년차'는 수령연차가 아니다
    m = re.search(r'(?:연금\s*)?(?:수령|개시)\s*(\d{1,2})\s*년\s*차|연금수령연차\s*(?:가|는|이)?\s*(\d{1,2})', q_y)
    if m:
        p['year_n'] = int(m.group(1) or m.group(2))
    else:
        m = re.search(r'(\d{1,2})\s*년\s*차', q_y)
        if m and not re.search(r'가입|근무|입사|재직|납입|직장|회사|일한', q_y[max(0, m.start() - 8):m.start()]):
            p['year_n'] = int(m.group(1))
        else:
            p['year_n'] = None
    p['year_assumed'] = False
    old_acct = re.search(r'(?:19\d\d|200\d|201[0-2])\s*년|2013년\s*3월\s*(?:1일\s*)?이전', q)
    if p['year_n'] is None and re.search(r'(?:올해|이번\s*해|금년)\s*(?:에\s*)?처음|처음으로\s*(?:연금|개시|받)|첫\s*해', q):
        a = p['age']
        if not old_acct and (a is None or a == 55):
            p['year_n'], p['year_assumed'] = 1, True      # 전제로 밝힌다(올해가 처음 개시 가능한 해라면)
    if p['year_n'] is None and not old_acct and \
            re.search(r'(?:올해|이번에|이번\s*달|지난\s*달|최근)\s*퇴직|퇴직(?:하면서|하고서?|해서)\s*(?:받은\s*)?퇴직금', q):
        # 올해 퇴직금을 새 IRP로 옮긴 경우 — 퇴직금이 있으면 가입기간 요건이 없어(doc39) 55세 이상이면 올해가 1년차
        p['year_n'], p['year_assumed'] = 1, True
    p['retire_payout'] = bool(re.search(
        r'퇴직금\s*(?:만|을|를|으로|이)?\s*(?:넣어?\s*둔|받아\s*둔|들어\s*있|들어간|이체|옮긴|넣고|넣었|입금)'
        r'|퇴직금(?:을|를)?\s*연금으로|퇴직연금(?:에서|으로|을)|(?<![A-Za-z])D[CB]\s*(?:형)?\s*(?:에서|으로)', q)) or \
        any(x['src'] == 'retire' for x in p['payout'])
    p['lifetime'] = '종신' in q
    p['emigrate'] = bool(_EMIGRATE.search(q))
    p['unavoidable'] = bool(_UNAVOIDABLE.search(q)) or p['emigrate']
    p['early'] = _early(q)
    p['no_credit'] = bool(re.search(r'세액\s*공제\s*(?:안|못)\s*받|공제\s*받지\s*않은|공제\s*안\s*받', q))
    p['future'] = bool(re.search(r'나중에|은퇴\s*(?:후|하면)|되면|부터|이후에', q))
    p['public'] = bool(re.search(r'국민연금|공무원연금|사학연금|군인연금|기초연금|공적연금', q))
    p['insure'] = bool(re.search(r'(?<!연금저축)보험|주택연금|농지연금', q))
    p['old'] = bool(re.search(r'\(구\)\s*개인연금|구\s*개인연금', q)) or \
        bool(re.search(r'개인연금', q) and re.search(r'(?:19\d\d|2000)\s*년', q))
    p['private'] = bool(re.search(r'연금저축|IRP|개인형|연금계좌|사적연금', q, re.I))
    m = re.search(r'ISA[^.?!]{0,40}?(연금저축(?:펀드|계좌)?|IRP|개인형\s*IRP|연금계좌)\s*(?:으로|로|에)', q, re.I)
    p['isa_dest'] = None if not m else ('irp' if re.search(r'IRP|개인형', m.group(1), re.I)
                                        else 'savings' if '연금저축' in m.group(1) else None)
    p['no_irp'] = bool(re.search(r'IRP\s*(?:는|은)?\s*(?:따로\s*)?(?:없|안\s*했|가입\s*안)', q, re.I))
    if p['isa_dest'] is None:
        # 문장이 나뉘어도('…받았어요. 그 돈을 연금저축펀드에 넣었는데요') 계좌가 하나만 나오면 그쪽으로 본다
        has_s = '연금저축' in q
        has_i = bool(re.search(r'IRP|개인형', q, re.I)) and not p['no_irp']
        p['isa_dest'] = 'savings' if has_s and not has_i else 'irp' if has_i and not has_s else None
    return p


_TAX_Q = re.compile(r'세금|세율|떼|원천징수|과세|세액(?!\s*공제)|종합과세|분리과세|내야|낼|나가')
# '과학기술인공제회'·'공제회'의 공제는 세액공제가 아니다(Q-099 오발동)
_DED_Q = re.compile(r'세액\s*공제|연말\s*정산|돌려\s*받|환급|(?<!인)공제(?!회)')


# ---------------------------------------------------------------------------
# 표기
# ---------------------------------------------------------------------------
def won(man):
    """만원 단위 값 → '297만 원', '82만 5천 원', '1,234만 5,600원'."""
    w = int(round(man * 10000))
    eok, rest = divmod(w, 100_000_000)
    m, r = divmod(rest, 10_000)
    parts = []
    if eok:
        parts.append(f'{eok:,}억')
    if m:
        parts.append(f'{m:,}만')
    if r:
        if r % 1000 == 0:
            parts.append(f'{r // 1000}천')
        else:
            return (' '.join(parts) + f' {r:,}원').strip()
    return (' '.join(parts) + ' 원').strip() if parts else '0원'


def pct(x):
    return f'{x:g}%'


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------
def _block(file, title, lines, keys, numbers=True, key_figure=None, yearly=None, needs=None, assume=None,
           figures=None, alts=None):
    """계산 블록. needs = 계산에 빠진 정보(되묻기용), assume = 대신 채운 값(답변의 '가정'),
    figures = 답에 나와야 할 계산값들(숫자 대조용), alts = 참고로 함께 실은 '고객 조건이 아닌' 값(다른 나이 구간 세액 —
    핵심 금액 없이 이 값만 쓴 답은 숫자 대조가 어긋남으로 본다)."""
    quotes = [rule(k) for k in keys]
    rids = rule_ids(keys)
    tag = f' {"·".join(rids)}' if rids else ''
    head = (f'[세금 계산(규칙{tag}) — 아래 문서 원문의 규칙으로 계산한 값]' if numbers
            else f'[세금 규칙{tag} — 조건이 갈려 금액은 계산하지 않음. 아래 문서 원문의 규칙]')
    body = list(lines)
    if needs:
        body.append('계산에 필요한데 질문에 없는 정보: ' + ' · '.join(needs))
    if assume:
        body.append('가정(질문에 없어 대신 채운 값): ' + ' · '.join(assume))
    text = head + '\n' + '\n'.join(f'- {x}' for x in body) + '\n[원문]\n' + '\n'.join(quotes)
    return {'file': file, 'topic': title, 'text': text, 'rules': keys, 'rids': rids, 'numbers': numbers,
            'key_figure': key_figure, 'yearly': yearly, 'needs': list(needs or []), 'assume': list(assume or []),
            'figures': list(figures or ([key_figure] if key_figure else [])), 'alt_figures': list(alts or [])}


# '16.5%가 무조건 이득이냐'처럼 분리과세·종합과세 선택을 묻는 말 — 이때는 '늘 유리하지 않다'(doc38) 규칙도 싣는다
_CHOICE_Q = re.compile(r'이득|유리|불리|무조건|나아|나을|낫|손해|선택|고르')
_NOT_ALWAYS = ('16.5% 분리과세가 늘 유리한 것은 아니다 — 다른 소득과 합쳐 계산한 종합과세 세율이 16.5%를 넘지 않으면 '
               '16.5% 분리과세가 오히려 불리할 수 있다')
_OVER_1500 = ('세액공제 받은 납입금·운용수익을 연 1,500만 원 넘게 연금수령하면 초과분이 아니라 전액이 종합과세'
              '(다른 소득과 합산해 6.6~49.5% 누진세율) 또는 16.5% 분리과세 대상')


def _payout_rule_only(p, why, extra_keys=(), q=''):
    lines = [why, '연금소득세(나이별, 지방소득세 포함): 55세 이상 70세 미만 5.5%, 70세 이상 80세 미만 4.4%, 80세 이상 3.3%',
             _OVER_1500,
             '1,500만 원 판단은 전 금융기관 연금계좌 합산이고, 세액공제 안 받은 돈과 퇴직금은 들어가지 않는다']
    keys = ['rate_table', 'over_1500', 'whole_amount', 'progressive', 'excluded', 'all_accounts'] + list(extra_keys)
    if _CHOICE_Q.search(q):
        lines.append(_NOT_ALWAYS)
        keys.append('not_always')
    return _block('doc38.txt', '연금 수령 세금(규칙)', lines, keys, numbers=False)


# 퇴직금을 '그 1,500만 원에 들어가나요?'처럼 따로 묻는 말 — 수령액이 퇴직금에서 나온다는 말이 아니다(Q-013 같은 질문)
_RETIRE_ASK = re.compile(r'퇴직\s*(?:금|급여)[^.?!\n]{0,20}?(?:도|은|는|까지)\s*[^.?!\n]{0,25}?'
                         r'(?:1[,.]?500|포함|들어가|들어\s*가|합산|해당|빠지|빠져|제외)')
# 퇴직금이 계좌에 있다는 말 — '퇴직금도 IRP에 있는데', '퇴직금 1억 IRP로 받았고'
_RETIRE_HELD = re.compile(r'퇴직\s*(?:금|급여)[^.?!\n]{0,15}?(?:IRP|개인형|연금\s*계좌|계좌|연금저축)\s*(?:에|로|으로|에서|에는)', re.I)
_RETIRE_NONE = re.compile(r'퇴직금\s*(?:은|는)?\s*(?:없|안\s*받|일시금으로\s*받았|다\s*썼)')


def _payout_retire(p, q):
    """퇴직금이 수령액의 재원일 수 있는 연금 수령 — 금액 없이 퇴직금 규칙과 ③ 재원(1,500만 원) 규칙을 함께."""
    lines = ['퇴직금 재원을 한도 안에서 연금으로 받으면 원래 퇴직소득세의 70%(실제 수령 10년차 이하)·60%(11~20년차)·'
             '50%(21년차~)만 낸다. 한도를 넘긴 퇴직금은 퇴직소득세 100%',
             '퇴직금 재원은 1,500만 원 판단에 들어가지 않는다 — 세액이 퇴직소득세액에 달려 있어 여기서 금액을 계산하지 않음',
             '계좌에 세액공제 받은 돈·수익도 있으면 퇴직금이 먼저 빠지고(인출 순서) 그 뒤 ③ 재원은 나이별 연금소득세 '
             '5.5%(55세 이상 70세 미만)·4.4%(70세 이상 80세 미만)·3.3%(80세 이상)',
             '③ 재원(세액공제 받은 납입금·운용수익) 수령액이 연 1,500만 원을 넘으면 초과분이 아니라 전액이 종합과세'
             '(6.6~49.5% 누진세율) 또는 16.5% 분리과세 대상 — 1,500만 원은 전 금융기관 연금계좌를 합산해 판단']
    keys = ['retire_rate', 'excluded', 'rate_table', 'order', 'over_1500', 'whole_amount', 'progressive', 'all_accounts']
    if _CHOICE_Q.search(q):
        lines.append(_NOT_ALWAYS)
        keys.append('not_always')
    return _block('doc38.txt', '퇴직금 재원 연금 세금(규칙)', lines, keys, numbers=False,
                  needs=['퇴직소득세(원천징수영수증의 퇴직소득세 또는 실효세율)',
                         '연금계좌 평가액과 그중 퇴직금 금액', '연금수령연차'])


_BANDS = [(5.5, '55세 이상 70세 미만'), (4.4, '70세 이상 80세 미만'), (3.3, '80세 이상')]
# 수령액 여럿이 합산이 아니라 고를 방식들이라는 말
_ALT_Q = re.compile(r'거랑|것과|이랑\s*.{0,12}(?:중|차이|비교)|아니면|vs|VS|비교|차이|중\s*(?:뭐|어느|어떤)|어느\s*(?:쪽|게|것)')
# 1,500만 원 판단은 전 금융기관 연금계좌 합산이다(doc38) — '합쳐서'·'같이 받으면'은 비교가 아니라 합산
_SUM_Q = re.compile(r'합쳐|합치|합산|같이|함께|둘\s*다|동시에|더하면|각각\s*받')
# 계좌가 둘(연금저축·IRP)이면 둘 다 받는 게 보통이다 — '거랑'·'차이'만으로는 비교로 보지 않는다
_ALT_STRONG = re.compile(r'아니면|vs|VS|중\s*(?:에\s*)?(?:뭐|어느|어떤)|어느\s*(?:쪽|게|것)|비교')


def _yearly(x):
    return x['v'] * 12 if x['period'] == 'monthly' else x['v']


def _payout_alternatives(p, q, mine):
    """고를 수령액 여럿을 각각 계산 — 합산하지 않는다. 연 표시는 하나만 있어도 모두 연으로 본다('연 1,500씩 … 2,000씩')."""
    band = rate_for_age(rate_age(p))
    lines, figs, keys = [], [], ['rate_table', 'over_1500', 'whole_amount', 'progressive', 'excluded', 'all_accounts']
    if band:
        age_txt = f'{p["age"][1]}대' if isinstance(p['age'], tuple) else f'{p["age"]}세'
        lines.append(f'나이 {age_txt} → 연금소득세율 {pct(band[0])} ({band[1]}, 지방소득세 포함)')
    else:
        lines.append('나이가 질문에 분명하지 않아 세율 구간을 정하지 않음 (55~69세 5.5%, 70~79세 4.4%, 80세 이상 3.3%)')
    for x, lab in dict.fromkeys((_yearly(x), (f'월 {won(x["v"])}(연 {won(_yearly(x))})' if x['period'] == 'monthly'
                                             else f'연 {won(x["v"])}')) for x in mine):
        if x > 1500:
            t = won(x * 0.165)
            figs.append(t)
            lines.append(f'{lab} 받으면: 1,500만 원 초과 → 초과분이 아니라 전액({won(x)})이 대상 — 16.5% 분리과세를 고르면 '
                         f'{won(x)} × 16.5% = {t}, 종합과세를 고르면 다른 소득과 합산해 6.6~49.5%')
        elif band:
            t = won(x * band[0] / 100)
            figs.append(t)
            lines.append(f'{lab} 받으면: 1,500만 원 이하 → 전액 {pct(band[0])} 분리과세 {won(x)} × {pct(band[0])} = {t}')
        else:
            alt = [won(x * r / 100) for r, _ in _BANDS]
            figs += alt
            lines.append(f'{lab} 받으면: 1,500만 원 이하 → 나이별 분리과세 '
                         + ' · '.join(f'{name} {pct(r)} → {a}' for (r, name), a in zip(_BANDS, alt)))
    if any(_yearly(x) > 1500 for x in mine):
        lines.append(_NOT_ALWAYS)
        keys.append('not_always')
    lines.append('비교만 보여 준다 — 1,500만 원 판단은 전 금융기관 연금계좌 합산이고, 세액공제 안 받은 돈과 퇴직금은 들어가지 않는다. '
                 '전제: 세액공제 받은 납입금·운용수익 재원을 55세 이후 연금수령한도 안에서 받는 경우')
    return _block('doc38.txt', '연금 수령 세금(방식 비교)', lines, keys, figures=list(dict.fromkeys(figs)))


def payout(p, q):
    """연금 수령 세금. 입력이 하나로 분명할 때만 숫자, 아니면 규칙만, 계좌 종류가 없으면 None."""
    pays = p['payout']
    if not pays or not p['private'] or p['insure']:
        return None                                  # 계좌 종류가 없으면 국민연금일 수도 있다
    if p['early'] or (isinstance(p['age'], int) and p['age'] < 55 and not p['future']):
        lines = ['55세 전 인출이나 해지는 연금수령이 아니라 연금외수령이다 — 세액공제 받은 납입금·운용수익은 16.5% 기타소득세'
                 '(1,500만 원 판단·종합과세 선택이 없다), 세액공제 안 받은 납입금은 비과세',
                 '부득이한 사유(사망·해외이주·요양 등)면 예외가 있다']
        return _block('doc39.txt', '55세 전·해지 인출 세금(규칙)', lines,
                      ['pension_req', 'over_limit'], numbers=False)
    retire_word = bool(re.search(r'퇴직금|퇴직\s*급여', q)) and not _RETIRE_NONE.search(q)
    # 퇴직금을 따로 묻기만 하는 말(수령액이 퇴직금에서 나온다는 말이 아님) — 수령 세금은 계산하고 퇴직금 규칙을 함께 싣는다
    retire_side = retire_word and not p['retire_payout'] and bool(_RETIRE_ASK.search(q)) and not _RETIRE_HELD.search(q)
    if p['retire_payout'] or (retire_word and not retire_side):
        # '퇴직금을 IRP에 넣어 뒀는데 연 1,800만 원' — 퇴직금이 나오면 그 수령액을 ③ 재원으로 계산하면 안 된다
        # (검토: 297만·330만 원을 세액공제 재원 기준으로 잘못 냈다). 퇴직소득세를 알아야 금액이 나온다.
        return _payout_retire(p, q)
    if p['lifetime']:
        # 종신형 세율은 문서(doc38 표: 모든 나이 3.3%)와 다른 안내(4.4%, 80세 이상 3.3%)가 엇갈린다 — 숫자를 정하지 않는다
        return _payout_rule_only(p, '종신연금형 세율은 확인이 필요하다 — 받은 문서(doc38) 표는 종신연금 수령 시 3.3%로 적지만 '
                                    '다른 안내와 엇갈려 여기서 정하지 않는다. 가입한 상품의 약관·상담으로 확인할 것', q=q)
    if p['no_credit']:
        return _payout_rule_only(p, '세액공제를 받지 않은 납입금은 인출할 때 과세되지 않고 1,500만 원 판단에도 들어가지 않는다', q=q)
    others = [x for x in pays if x['src'] in ('public', 'earned', 'insure', 'retire')]
    person = [x for x in pays if x['src'] == 'person']
    mine = [x for x in pays if x not in others and x not in person]
    if person and not mine and not others:
        mine, person = person, []       # '81세 아버지가 연금저축에서 연 1,000만원' — 질문 전체가 그 사람 얘기다
    if others or person or p['public']:
        return _payout_rule_only(p, '질문에 연금저축·IRP 외의 연금이나 소득이 함께 있어 금액을 계산하지 않음 '
                                    '(공적연금은 1,500만 원 판단에서 빠진다)', q=q)
    periods = [x['period'] for x in mine]
    two_accts = bool(re.search(r'연금저축', q)) and bool(re.search(r'IRP|개인형', q, re.I))
    alt = False if _SUM_Q.search(q) else bool(_ALT_STRONG.search(q)) if two_accts else bool(_ALT_Q.search(q))
    if len(mine) >= 2 and alt and any(periods) and not ('monthly' in periods and None in periods):
        if None in periods:              # '연 1,500씩 … 2,000씩' — 연 표시가 하나만 있어도 모두 연으로 본다
            mine = [dict(x, period=x['period'] or 'yearly') for x in mine]
        # '연 1,500씩 받는 거랑 2,000씩 받는 거' — 합산이 아니라 두 방식의 비교다(C16 대화). 각각 따로 계산한다
        return _payout_alternatives(p, q, mine)
    if any(x['period'] is None for x in mine):
        return _payout_rule_only(p, '질문에 연/월 표시가 없어 연간 수령액인지 알 수 없음 — 한 번에 인출하는 경우라면 '
                                    '연금수령한도를 넘는 부분은 연금외수령(16.5% 기타소득세)', q=q)
    if len(mine) > 1 and not all(x['src'] == 'private' for x in mine):
        return _payout_rule_only(p, '수령액이 여럿이라 어느 계좌 것인지 분명하지 않아 금액을 계산하지 않음', q=q)
    yearly = sum(x['v'] * (12 if x['period'] == 'monthly' else 1) for x in mine)
    lines = []
    if len(mine) > 1:
        lines.append('입력: ' + ' + '.join((f'월 {won(x["v"])} × 12' if x['period'] == 'monthly' else f'연 {won(x["v"])}')
                                          for x in mine) + f' = 연 {won(yearly)} (연금계좌 합산)')
    elif mine[0]['period'] == 'monthly':
        lines.append(f'입력: 월 {won(mine[0]["v"])} × 12 = 연 {won(yearly)}')
    else:
        lines.append(f'입력: 연금 수령액 연 {won(yearly)}')
    band = rate_for_age(rate_age(p))
    r = band[0] if band else None
    others_b = [(x, name) for x, name in _BANDS if not band or name != band[1]]
    if band:
        ra = rate_age(p)
        age_txt = f'{ra[1]}대' if isinstance(ra, tuple) else f'{ra}세'
        lines.append(f'나이 {age_txt} → 연금소득세율 {pct(r)} ({band[1]}, 지방소득세 포함)'
                     + (f' — 지금 {p["age"]}세지만 받는 나이({ra}세) 기준(연금소득세율은 연금수령일 현재 나이로 정함)'
                        if p.get('rate_age') and p.get('age') and p['age'] != ra else ''))
        # 고객 구간 하나만 보이면 답이 좁아진다 — 다른 나이 구간도 싣는다(세율은 연금수령일 현재 나이 기준)
        # '고객은 해당 없음'을 밝힌다 — 67세 질문에 답이 '70세 이상 80세 미만(고객 해당) 4.4%'로 옮겨 적은 일이 있다(C02 대화)
        lines.append(f'다른 나이 구간(고객 {age_txt}는 해당 없음): ' + ' · '.join(f'{name} {pct(x)}' for x, name in others_b)
                     + ' (연금수령일 현재 나이 기준 — 나이가 들수록 세율이 낮아진다)')
    elif yearly > 1500:
        lines.append('나이가 질문에 분명하지 않아 세율 구간을 정하지 않음 (55~69세 5.5%, 70~79세 4.4%, 80세 이상 3.3%)')
    key = None
    figs, alts = [], []
    if yearly > 1500:
        key = won(yearly * 0.165)
        figs.append(key)
        lines.append(f'연 {won(yearly)}은 1,500만 원 초과 → **초과분이 아니라 전액({won(yearly)})**이 대상이다')
        lines.append(f'  ① 16.5% 분리과세를 고르면 {won(yearly)} × 16.5% = {key}')
        lines.append('  ② 종합과세를 고르면 다른 종합소득과 합산해 6.6~49.5% 누진세율 (세액은 다른 소득에 따라 달라 여기서 계산하지 않음)')
        lines.append('  ' + _NOT_ALWAYS)
        lines.append('  틀린 계산 예: "1,500만 원까지 저율, 초과분만 16.5%" — 문서는 전체금액에 16.5%라고 적고 있다')
        if r:
            lines.append(f'  참고: 연 1,500만 원 이하로 받으면 전액 {pct(r)} 분리과세 (1,500만 원이면 {won(1500 * r / 100)})')
    elif r:
        key = won(yearly * r / 100)
        figs.append(key)
        lines.append(f'연 {won(yearly)}은 1,500만 원 이하 → 전액 {pct(r)} 분리과세(다른 소득과 합산하지 않음): '
                     f'{won(yearly)} × {pct(r)} = {key}')
        alt = [won(yearly * x / 100) for x, _ in others_b]
        alts += alt
        lines.append(f'같은 연 {won(yearly)}을 다른 나이 구간에 받으면: '
                     + ' · '.join(f'{name} {pct(x)} → {a}' for (x, name), a in zip(others_b, alt)))
    else:
        alt = [won(yearly * x / 100) for x, _ in _BANDS]
        figs += alt
        lines.append(f'연 {won(yearly)}은 1,500만 원 이하 → 나이별 연금소득세율로 분리과세(다른 소득과 합산하지 않음). '
                     '나이가 질문에 분명하지 않아 구간별로: '
                     + ' · '.join(f'{name} {pct(x)} → {a}' for (x, name), a in zip(_BANDS, alt)))
    lines.append('전제: 세액공제 받은 납입금·운용수익 재원을 55세 이후 연금수령한도 안에서 받는 경우, 전 금융기관 연금계좌 합산 기준. '
                 '세액공제 안 받은 납입금과 퇴직금 재원은 1,500만 원 판단에 들어가지 않는다(재원이 다르면 결과가 달라짐)')
    keys = ['rate_table', 'over_1500', 'whole_amount', 'progressive', 'excluded', 'all_accounts']
    if retire_side:
        # '그리고 퇴직금 받은 것도 그 1,500만 원에 들어가나요?' — 두 번째 하위 질문의 규칙도 함께 싣는다
        lines.append(f'퇴직금(이연퇴직소득)은 1,500만 원 판단에 들어가지 않는다 — 수령액 연 {won(yearly)}에 퇴직금 재원이 섞여 있으면 '
                     '그 부분은 빼고 판단하고, 퇴직금 재원은 퇴직소득세의 70%(실제 수령 10년차 이하)·60%(11~20년차)·'
                     f'50%(21년차~)로 따로 과세된다. 위 계산은 연 {won(yearly)} 전부가 세액공제 받은 납입금·운용수익일 때다')
        keys.append('retire_rate')
    elif re.search(r'퇴직금|퇴직급여', q):
        lines.append('질문에 퇴직금이 나온다 — 퇴직금 재원은 1,500만 원 판단에서 빠지고 이연퇴직소득세 기준으로 따로 과세된다')
    if yearly > 1500:
        keys.append('not_always')
    return _block('doc38.txt', '연금 수령 세금', lines, keys, key_figure=key, yearly=yearly, figures=figs, alts=alts)


# 어느 쪽이 나은지 묻는 말 — 이때는 계산 블록이 어느 쪽이든 '비교만 하고 권하지 마라'를 붙인다(calc_policy)
_COMPARE_Q = re.compile(r'나아|나을|낫|좋아요|좋을까|좋은가|좋은지|유리|불리|어디에|어디로|어느\s*(?:쪽|게|것|계좌)|뭐가\s*좋|최적|추천')


def _acct_choice(p, q):
    """한 금액을 연금저축과 IRP 중 어디에 넣을지 묻는 질문인가."""
    return (len(p['contrib']) == 1 and '연금저축' in q and bool(re.search(r'IRP|개인형', q, re.I))
            and bool(re.search(r'어디에|어느\s*(?:쪽|계좌|게)|중에?\s*(?:뭐|어디|어느)|나아|나을|좋아|유리', q)))


def _combos(contrib):
    """납입 나열을 조합으로 나눈다 — 같은 계좌가 다시 나오면 새 조합. → [(연금저축 합, IRP 합)]"""
    out, cur = [], {'savings': 0.0, 'irp': 0.0}
    seen = set()
    for c in contrib:
        if c['acct'] in seen:
            out.append((cur['savings'], cur['irp']))
            cur, seen = {'savings': 0.0, 'irp': 0.0}, set()
        cur[c['acct']] += c['v']
        seen.add(c['acct'])
    out.append((cur['savings'], cur['irp']))
    return out if len(out) >= 2 else []


def deduction(p, q):
    """세액공제. 소득을 알면 공제율을 정하고, 납입액을 알면 공제액까지."""
    lines = []
    rate = None
    if p['income']:
        kind, v = p['income']
        cut = 5500 if kind == '총급여' else 4500
        rate = 16.5 if v <= cut else 13.2
        word = '연봉' if re.search(r'연봉', q) and kind == '총급여' else kind
        lines.append(f'입력: {word} {won(v)}' + (' (총급여로 봄)' if word == '연봉' else '')
                     + f' → 기준 {won(cut)} {"이하" if v <= cut else "초과"} → 세액공제율 {pct(rate)}')
    else:
        lines.append('공제율을 정할 소득이 질문에 분명하지 않음 (직장인 총급여 5,500만 원, 종합소득자 종합소득금액 4,500만 원 '
                     '이하면 16.5%, 초과면 13.2%)')
    s = sum(c['v'] for c in p['contrib'] if c['acct'] == 'savings')
    i = sum(c['v'] for c in p['contrib'] if c['acct'] == 'irp')
    has_s = any(c['acct'] == 'savings' for c in p['contrib'])
    has_i = any(c['acct'] == 'irp' for c in p['contrib'])
    combos = _combos(p['contrib']) if re.search(r'비교|vs|VS|아니면|각각|이랑\s*.{0,20}(?:중|어느)|중\s*(?:뭐|어느|어떤)|것과|거랑', q) else []
    if not combos and _acct_choice(p, q):
        # '연금저축이랑 IRP 중에 어디에 600 넣는 게 나아요?' — 한 금액을 두 계좌에 넣는 경우를 나란히(C18 대화: 블록 없이 '연금저축이 유리'라고 권했다)
        v = p['contrib'][0]['v']
        combos = [(v, 0.0), (0.0, v)]
    if len(combos) >= 2:
        # '연금저축 600 + IRP 300이랑 연금저축 900 비교' — 같은 계좌가 다시 나오면 다른 조합이다(DIY 설계 7쪽)
        figs = []
        for k, (cs, ci) in enumerate(combos):
            el = min(min(cs, 600) + ci, 900)
            name = '조합 ' + 'ABCDE'[k]
            parts = ([f'연금저축 {won(cs)}'] if cs else []) + ([f'IRP {won(ci)}'] if ci else [])
            line = f'{name}: ' + ' + '.join(parts) + f' → 공제 대상 {won(el)}'
            if rate:
                line += f' → 세액공제 {won(el)} × {pct(rate)} = {won(el * rate / 100)}'
                figs.append(won(el * rate / 100))
            over = cs + ci - el
            if over > 0:
                line += f' · 공제 못 받는 {won(over)}은 나중에 꺼낼 때 원금만 세금 없음(과세제외)'
            lines.append(line)
        lines.append('비교만 보여 준다 — 중도인출 필요성(연금저축은 사유 없이, IRP는 법정 사유가 있어야)도 함께 따져 고객이 고른다')
        lines.append('주의: 납부할 세금(결정세액)이 공제액보다 적으면 그만큼만 공제된다. 종합소득이 있어야 공제받는다')
        blocks = [_block('doc41.txt', '세액공제(조합 비교)', lines, ['ded_limits', 'ded_rates', 'ded_cap_tax', 'ded_need_income'],
                         numbers=bool(figs), key_figure=figs[0] if figs else None, figures=figs)]
        return blocks
    figs, key = [], None

    def both(x):
        """공제율을 모를 때 두 공제율로 — 공제 대상만 보이면 '얼마 돌려받나'에 닿지 못한다."""
        a, b = won(x * 0.165), won(x * 0.132)
        figs.extend([a, b])
        return f'16.5%면 {won(x)} × 16.5% = {a}, 13.2%면 {won(x)} × 13.2% = {b}'
    if p['contrib']:
        for c in p['contrib']:
            if c['monthly']:
                lines.append(f'입력: {"IRP" if c["acct"] == "irp" else "연금저축"} 월 {won(c["raw"])} × 12 = 연 {won(c["v"])}')
        elig = min(min(s, 600) + i, 900)
        lines.append('납입: ' + ', '.join(([f'연금저축 {won(s)}'] if has_s else []) + ([f'IRP {won(i)}'] if has_i else []))
                     + f' → 공제 대상 {won(elig)} (연금저축 한도 600만 원, IRP 포함 합산 900만 원)')
        if rate:
            key = won(elig * rate / 100)
            figs.append(key)
            lines.append(f'세액공제액 = {won(elig)} × {pct(rate)} = {key}')
        else:
            lines.append('세액공제액(공제율에 따라): ' + both(elig))
        room = 900 - elig
        if room > 0 and re.search(r'더|추가|남은|남았|채우|채울|마저', q):
            # '연금저축에 600만 원 넣었는데 IRP에 더 넣으면' — 남은 공제 한도와 다 채울 때 더 받는 세액공제(장면 4)
            via = 'IRP로' if min(s, 600) >= 600 else '연금저축 600만 원까지 또는 IRP로'
            line = f'남은 공제 한도: 900만 원 − {won(elig)} = {won(room)} ({via} 채울 수 있음)'
            if rate:
                line += f' → 다 채우면 {won(room)} × {pct(rate)} = {won(room * rate / 100)} 더 공제'
                figs.append(won(room * rate / 100))
            else:
                line += ' → 다 채우면 더 받는 세액공제: ' + both(room)
            lines.append(line)
            if rate:
                lines.append(f'다 채운 뒤 세액공제 합계 = 900만 원 × {pct(rate)} = {won(900 * rate / 100)}')
                figs.append(won(900 * rate / 100))
            else:
                lines.append('다 채운 뒤 세액공제 합계: ' + both(900))
            lines.append('그해 12월 31일까지 납입한 금액이 그해 공제 대상이다')
    elif re.search(r'최대|얼마까지|얼마나\s*넣|한도|(?:다|꽉|가득|모두)\s*채[우워울]|만\s*원?\s*(?:을|를)?\s*채[우워울]', q):
        # '900만 원을 다 채우면 세액공제는 총 얼마' — 한도를 다 채우는 질문도 최대 공제를 보인다(C18 대화)
        if rate:
            lines.append(f'최대: 연금저축만이면 600만 원 × {pct(rate)} = {won(600 * rate / 100)}, '
                         f'IRP와 합쳐 900만 원 × {pct(rate)} = {won(900 * rate / 100)}')
            figs += [won(600 * rate / 100), won(900 * rate / 100)]
        else:
            lines.append('최대: 연금저축 단독 600만 원, IRP 포함 합산 900만 원까지 공제 대상')
    lines.append('주의: 납부할 세금(결정세액)이 공제액보다 적으면 그만큼만 공제된다. 종합소득이 있어야 공제받는다')
    keys = ['ded_limits', 'ded_rates', 'ded_cap_tax', 'ded_need_income']
    # 계산값이 없는 블록(공제율 규칙만)은 numbers=False — '새로 계산하지 마라'·초안 1개가 걸리지 않게
    blocks = [_block('doc41.txt', '세액공제', lines, keys, numbers=bool(figs), key_figure=key, figures=figs)]
    if p['isa']:
        blocks.append(isa_block(p, rate, s, i))
    return blocks


def isa_block(p, rate, s, i):
    """ISA 만기 전환 연도의 공제 대상(doc4 업무매뉴얼 산식).
    일반 한도분 = min(전환액 + 일반 납입, 600만[연금저축] / 900만[IRP 포함]),
    추가분 = min(전환액 × 10%, 300만), 합계는 실제 납입액(전환액 + 일반 납입)을 넘지 않는다."""
    isa = p['isa']
    extra = min(isa * 0.1, 300)
    lines = [f'입력: ISA 만기 전환 {won(isa)}' + (f', 올해 연금저축 납입 {won(s)}' if s else '')
             + (f', IRP 납입 {won(i)}' if i else '')]

    def total(dest):
        to_s = isa if dest == 'savings' else isa / 2 if dest == 'split' else 0
        gs = min(s + to_s, 600)
        g = min(gs + i + (isa - to_s), 900)
        return g, min(g + extra, isa + s + i)

    # 60일은 계산의 전제다(doc4: 만기일부터 60일이 지나면 전환 불가) — 블록 끝 줄에 두었더니 총 공제액 줄을 더한 뒤 답에서 빠졌다
    lines.append(f'추가 공제 대상 = min(전환액 × 10%, 300만 원) = {won(extra)} (전제: ISA 만기일부터 60일 안에 연금계좌로 옮긴 경우 — '
                 '60일이 지나면 전환 불가. 전환액은 연 1,800만 원 납입한도와 별도)')
    dests = ['savings', 'irp', 'split'] if p.get('isa_split') else [p['isa_dest']] if p['isa_dest'] else ['savings', 'irp']
    figs = [won(extra)] + ([won(extra * rate / 100)] if rate else [])
    totals = {}
    for d in dests:
        g, t = total(d)
        name = ('연금저축계좌로 옮기면' if d == 'savings' else 'IRP로 옮기면' if d == 'irp'
                else f'나눠 옮기면(연금저축 {won(isa / 2)} + IRP {won(isa / 2)})')
        totals[name] = t
        line = f'{name}: 일반 한도분 {won(g)} + 추가분 {won(extra)}'
        line += f' = 공제 대상 {won(t)}' if t == g + extra else f' → 실제 납입액 상한으로 공제 대상 {won(t)}'
        if rate:
            line += f' → 세액공제 {won(t)} × {pct(rate)} = {won(t * rate / 100)}'
            figs.append(won(t * rate / 100))
            base = min(min(s, 600) + i, 900)
            if base:
                # 이미 넣은 납입분의 공제보다 얼마나 늘어나는지 — 고객이 실제로 궁금한 값(옮기면 더 받는 금액)
                line += f' (옮기지 않을 때 {won(base * rate / 100)}보다 {won((t - base) * rate / 100)} 더)'
        lines.append(line)
    if not rate:
        lines.append(f'추가분 {won(extra)}의 세액공제액: 공제율 16.5%면 {won(extra * 0.165)}, 13.2%면 {won(extra * 0.132)}')
        # 추가분만 보이면 답이 좁아진다 — 옮긴 해의 총 공제액도 싣는다
        for t in dict.fromkeys(totals.values()):
            who = [n for n, v in totals.items() if v == t]
            head = '' if len(set(totals.values())) == 1 else ' / '.join(who) + ' — '
            lines.append(f'{head}옮긴 해 공제 대상 {won(t)} 전체의 세액공제액: 공제율 16.5%면 {won(t * 0.165)}, '
                         f'13.2%면 {won(t * 0.132)}')
            figs += [won(t * 0.165), won(t * 0.132)]
    if (p['isa_dest'] == 'savings' or p['no_irp']) and not i:
        lines.append('IRP 납입이 없으면 일반 한도분의 상한이 연금저축 600만 원이다(IRP 포함 시 900만 원)')
    return _block('doc4.txt', 'ISA 전환 세액공제', lines, ['isa_to_savings', 'isa_to_irp', 'isa_formula'],
                  figures=list(dict.fromkeys(figs)))


def withdraw_limit(p, q):
    """연금수령한도와 인출 과세. 평가액·인출액·연차가 모두 분명할 때만."""
    if p['balance'] is None or p['withdraw'] is None or not p['year_n']:
        return None
    if isinstance(p['age'], int) and p['age'] < 55:
        return None                       # 55세 전에는 연금수령이 아니다 → payout 의 규칙만 블록으로
    n = p['year_n']
    lines = [f'입력: 평가액 {won(p["balance"])}, 연금수령연차 {n}년차, 인출 {won(p["withdraw"])}']
    keys = ['limit_formula', 'limit_year1', 'over_limit', 'limit_rates']
    if p['year_assumed']:
        lines.append('연차: 올해가 처음 연금개시가 가능한 해라면 1년차 (연금수령연차는 실제 수령 여부와 무관하게 개시 가능한 해부터 센다. '
                     '2013년 3월 1일 이전 가입 계좌는 6년차부터)')
        keys += ['year_count', 'year6']
    if n >= 11:
        lines.append('연금수령연차 11년차부터는 연금수령한도가 적용되지 않는다')
        lim = None
    else:
        lim = p['balance'] / (11 - n) * 1.2
        lines.append(f'연금수령한도 = {won(p["balance"])} ÷ (11 − {n}) × 120% = {won(lim)}')
    w = p['withdraw']
    inside = w if lim is None else min(w, lim)
    over = 0 if lim is None else max(0.0, w - lim)
    band = rate_for_age(rate_age(p))
    figs = [won(lim)] if lim is not None else []
    t_over = over * 0.165
    if over:
        lines.append(f'인출 {won(w)} 중 한도 안 {won(inside)}은 연금수령, 한도 초과 {won(over)}은 연금외수령')
        lines.append(f'한도 초과분이 세액공제 받은 납입금·운용수익이면 16.5% 기타소득세: {won(over)} × 16.5% = {won(t_over)}')
        figs.append(won(t_over))
    else:
        lines.append(f'인출 {won(w)}은 한도 안 → 연금수령')
    t_in = None
    if inside > 1500:
        t_in = inside * 0.165
        lines.append(f'연금수령분 {won(inside)}도 연 1,500만 원 초과 → 초과분이 아니라 전액이 종합과세 또는 16.5% 분리과세 대상: '
                     f'{won(inside)} × 16.5% = {won(t_in)}')
        keys += ['over_1500', 'whole_amount']
    elif band:
        t_in = inside * band[0] / 100
        lines.append(f'연금수령분 {won(inside)}: {won(inside)} × {pct(band[0])} = {won(t_in)} (나이 기준)')
    else:
        # 나이를 모르면 구간별로 — 고객 구간 하나만 정하지 않고 세 구간을 모두 싣는다(나눠 더한 합계도)
        alt = [inside * x / 100 for x, _ in _BANDS]
        figs += [won(a) for a in alt]
        lines.append(f'연금수령분 {won(inside)}: 나이가 질문에 없어 구간별로 '
                     + ' · '.join(f'{name} {pct(x)} → {won(a)}' for (x, name), a in zip(_BANDS, alt)))
        if over:
            figs += [won(t_over + a) for a in alt]
            lines.append('나이 구간별 올해 세금 합계: '
                         + ' · '.join(f'{name} {won(t_over)} + {won(a)} = {won(t_over + a)}'
                                      for (x, name), a in zip(_BANDS, alt)))
    key = None
    if t_in is not None:
        figs.append(won(t_in))
        key = won(t_over + t_in)
        if over:
            lines.append(f'올해 예상 세금 합계 = {won(t_over)} + {won(t_in)} = {key}'
                         + (' (연금수령분을 16.5% 분리과세로 고를 때)' if inside > 1500 else ''))
            figs.append(key)
    elif over:
        key = won(t_over)              # 나이를 몰라도 한도 초과분 세금은 정해진다 — 숫자 대조가 '2,970만 원' 같은 오산을 잡게
    lines.append('전제: 세액공제 받은 납입금·운용수익 재원 기준. 세액공제 안 받은 납입금 → 퇴직금 → 세액공제 받은 납입금·운용수익 '
                 '순으로 인출되므로 재원 구성에 따라 과세가 달라진다 — 재원 구성은 질문에 없다')
    return _block('doc39.txt', '연금수령한도', lines, keys, key_figure=key, figures=list(dict.fromkeys(figs)))


# ---------------------------------------------------------------------------
# 재원별 인출 — 퇴직금이 섞인 계좌, 55세 전 해지 (R-04·R-05·R-06·R-09·R-10)
# ---------------------------------------------------------------------------
FUND_NAME = {'free': '① 세액공제 안 받은 돈', 'retire': '② 퇴직금(이연퇴직소득)', 'credit': '③ 세액공제 받은 돈·운용수익'}


def _reduction(m):
    """연금실제수령연차 → (퇴직소득세에 곱할 비율, 설명). doc39 표."""
    if m <= 10:
        return 0.7, '30% 감면 — 연금실제수령연차 1~10년차'
    if m <= 20:
        return 0.6, '40% 감면 — 연금실제수령연차 11~20년차'
    return 0.5, '50% 감면 — 연금실제수령연차 21년차~'


def _alloc(sources, w, lim):
    """인출 w 를 법정 순서(①→②→③)로 재원에 나누고 연금수령한도 lim(None=제한 없음) 안/밖을 가른다.
    과세제외금액도 한도 안에서 먼저 빠지는 것으로 센다. → ([(재원, 인출, 한도 안, 한도 밖)], 잔액 부족분)"""
    rows, rem, room = [], w, (float('inf') if lim is None else lim)
    for kind, amt in sources:
        take = min(rem, amt)
        if take <= 0:
            continue
        inside = min(take, max(0.0, room))
        rows.append((kind, take, inside, take - inside))
        room -= inside
        rem -= take
    return rows, max(0.0, rem)


def _row_tax(kind, inside, over, eff, red, rate, lines, early):
    """재원 한 줄의 세금. 금액을 못 정하면 None 을 돌려준다(설명 줄은 그래도 쓴다)."""
    name = FUND_NAME[kind]
    if kind == 'free':
        lines.append(f'{name} {won(inside + over)}: 비과세(한도를 넘어도 과세 없음)')
        return 0.0
    if kind == 'retire':
        tax, parts = 0.0, []
        if inside:
            if eff is not None:
                t = inside * eff / 100 * red[0]
                # '퇴직소득세의 70%(30% 감면(…))'를 '70% 감면'으로 옮겨 쓴 답이 있었다 — '…%만 냄'으로 분명히
                parts.append(f'한도 안 {won(inside)} = 연금수령 → 퇴직소득세의 {round(red[0] * 100)}%만 냄({red[1]}): '
                             f'{won(inside)} × {eff:g}% × {round(red[0] * 100)}% = {won(t)}')
                tax += t
            else:
                parts.append(f'한도 안 {won(inside)} = 연금수령 → 원래 퇴직소득세의 {round(red[0] * 100)}%만 냄({red[1]})')
                tax = None
        if over:
            what = '55세 전·해지 인출이라 연금외수령' if early else '한도 초과분 = 연금외수령'
            if eff is not None:
                t = over * eff / 100
                parts.append(f'{what} {won(over)} → 퇴직소득세 100%(감면 없음): {won(over)} × {eff:g}% = {won(t)}')
                if tax is not None:
                    tax += t
            else:
                parts.append(f'{what} {won(over)} → 퇴직소득세 100%(감면 없음)')
                tax = None
        lines.append(f'{name} {won(inside + over)}: ' + ' / '.join(parts))
        return tax
    # credit
    tax, parts = 0.0, []
    if inside:
        if inside > 1500:
            t = inside * 0.165
            parts.append(f'한도 안 {won(inside)} = 연금수령이지만 연 1,500만 원 초과 → 전액 16.5% 분리과세(또는 종합과세) '
                         f'{won(inside)} × 16.5% = {won(t)}')
            tax += t
        elif rate:
            t = inside * rate[0] / 100
            parts.append(f'한도 안 {won(inside)} = 연금수령 → 연금소득세 {pct(rate[0])}({rate[1]}): {won(t)}')
            tax += t
        else:
            parts.append(f'한도 안 {won(inside)} = 연금수령 → 연금소득세 5.5~3.3%(나이에 따라)')
            tax = None
    if over:
        t = over * 0.165
        what = '55세 전·해지 인출이라 연금외수령' if early else '한도 초과분 = 연금외수령'
        parts.append(f'{what} {won(over)} → 기타소득세 16.5%: {won(over)} × 16.5% = {won(t)}')
        if tax is not None:
            tax += t
    lines.append(f'{name} {won(inside + over)}: ' + ' / '.join(parts))
    return tax


def _year_tax(sources, w, lim, eff, m, age, early):
    """한 해 인출의 재원별 세금 → (줄들, 합계 또는 None, 배분)."""
    lines = []
    rows, short = _alloc(sources, w, None if early is False and lim is None else (0.0 if early else lim))
    total = 0.0
    red = _reduction(m or 1)
    rate = rate_for_age(age)
    for kind, take, inside, over in rows:
        t = _row_tax(kind, inside, over, eff, red, rate, lines, early)
        total = None if t is None or total is None else total + t
    return lines, total, rows, short


def _irp_early(q, acct):
    """55세 전 인출 금액을 계산할 때 함께 싣는 규칙 → (줄들, 원문 키들).
    부득이한 사유(세법, 요양 3개월)는 늘 — 없으면 답이 근퇴법 표의 '6개월 이상 요양'을 부득이한 사유로 옮겨 적었다(C10 대화 실측).
    IRP 면 인출 요건(doc20)과, 주택·전세 말이 있으면 '부득이한 사유가 아니라 세금 그대로'(doc20)."""
    lines = ['세법상 부득이한 사유(가입자의 사망·해외이주·천재지변·가입자 또는 부양가족의 3개월 이상 요양·파산 또는 개인회생 등)로 '
             '인출하면 기타소득세 대신 연금소득세 5.5~3.3% — 사유가 확인된 날부터 6개월 안에 서류를 내야 한다']
    keys = ['unavoidable', 'unavoidable_care', 'unavoidable_doc']
    if acct != 'IRP':
        return lines, keys
    lines.insert(0, 'IRP는 근퇴법상 중도인출 사유(무주택자 주택 구입·전세보증금 등 법으로 정한 사유)가 있어야 55세 전에 '
                    '인출할 수 있다 — 사유가 없으면 중도인출이 안 된다(연금저축은 사유 없이 인출 가능)')
    keys += ['irp_reason_req', 'irp_reasons', 'savings_withdraw']
    if re.search(r'주택|전세|집\s*(?:사|구입|마련)', q):
        lines.append('주택 구입·전세보증금은 IRP 중도인출 사유지만 세법상 부득이한 사유는 아니어서 세금은 그대로다 — '
                     '세액공제 받은 돈·수익은 기타소득세 16.5%')
        keys.append('house_not_unavoidable')
    return lines, keys


_CANCEL = re.compile(r'해지|해약|깨(?!끗)')


def _cancel_check(q, acct):
    """해지 질문이면 '해지 신청 전 확인' 줄 → (줄들, 원문 키들).
    연금저축은 해지를 신청할 때 과세재원이 확정되고 신청 뒤에는 바꿀 수 없다(doc5) — 공제 안 받은 돈은 신청 전에 확인해야 한다.
    IRP 는 재원확정(doc19)으로 공제 안 받은 돈을 과세제외한다. doc19 의 '재원확정: 내점만 가능'은 MAPS 개시/해지 화면의
    공제확인서 발급번호 입력칸·FAQ 와 어긋날 수 있어 싣지 않는다(본선_작업정리/12 D-4)."""
    if not _CANCEL.search(q or ''):
        return [], []
    if acct == 'IRP':
        return (['해지 신청 전 확인: IRP는 재원확정으로 금융회사의 과세대상금액을 국세청 소득·세액공제합계액과 맞춰 '
                 '세액공제 안 받은 돈을 과세에서 뺀다 — 필요 서류는 연금보험료등 소득·세액공제확인서(국세청 자료)'],
                ['irp_fix', 'irp_fix_doc'])
    return (['해지 신청 전 확인: 연금저축은 해지를 신청할 때 과세재원이 확정돼 국세청에 통보되고, 신청 뒤에는 과세재원을 '
             '바꾸거나 그에 따라 환급받을 수 없다(해지 뒤 전년도 납입금을 공제받지 않았음을 증빙하면 예외) — '
             '세액공제 안 받은 납입금이 있으면 해지 신청 전에 확인해 둔다'],
            ['cancel_fix', 'cancel_fix_exc'])


def by_source(p, q):
    """재원별 인출 세금. 퇴직금이 섞였거나(재원 언급), 55세 전·해지 인출일 때.
    필요한 값이 빠지면 금액 대신 규칙과 '빠진 정보'(되묻기용)를 담은 블록을 낸다."""
    w = p['withdraw']
    if w is None:
        return None
    age = p['age'] if isinstance(p['age'], int) else None
    band_age = p['age'] if isinstance(p['age'], tuple) and p['age'][1] >= 60 else age   # '60대'는 55세 이상 구간에 온전히 든다
    early = bool(p['early'] or (age is not None and age < 55 and not p['future']))
    mentioned = p['fund_mentioned']
    if not early and 'retire' not in mentioned and not p['fund']:
        return None                       # 퇴직금 얘기가 없는 55세 이후 인출은 withdraw_limit(③ 재원 기준)이 맡는다
    if p['unavoidable']:
        return unavoidable_rule(p, q, early)

    f = dict(p['fund'])
    B = p['balance']
    keys = ['order', 'free'] if 'free' in mentioned or f.get('free') else ['order']
    needs, assume = [], []
    if p.get('cancel_all'):
        assume.append(f"'해지'를 계좌 전부를 꺼내는 것으로 봄 — 인출 {won(w)}")
    acct = 'IRP' if re.search(r'IRP|개인형', q, re.I) else '연금저축' if '연금저축' in q else '연금계좌'
    # 재원 구성 채우기 — 평가액과 두 재원을 알면 나머지 하나는 뺄셈으로
    if B is None and len(f) >= 2 and all(k in f for k in mentioned):
        B = sum(f.values())
        assume.append(f'평가액 = 재원 합계 {won(B)}')
    elif B is None and set(f) == {'retire'} and mentioned == {'retire'}:
        # 'IRP에 퇴직금 1억 원 들어 있어요' — 다른 재원 얘기가 없으면 평가액 = 퇴직금
        B = f['retire']
        f['credit'] = 0.0
        assume.append(f'평가액 = 퇴직금 {won(B)}(다른 재원 없음)')
    if B is not None:
        known = sum(f.values())
        missing = [k for k in ('retire', 'credit') if k in mentioned and k not in f]
        if len(missing) == 1 and B - known >= 0:
            f[missing[0]] = B - known
            if missing[0] == 'credit' and known:
                assume.append(f'{FUND_NAME["credit"]} = 평가액 − 나머지 재원 = {won(B - known)}')
    free_known = 'free' in f
    if early and free_known and 'retire' not in mentioned and 'credit' not in f:
        # '공제 안 받은 돈이 500만 원 있어요, 2,000만 원 해지하면' — 나머지는 ③ 재원으로 본다
        f['credit'] = max(0.0, (B if B is not None else w) - f['free'])
        assume.append(f'나머지 {won(f["credit"])}은 세액공제 받은 돈·운용수익으로 봄')
    if 'free' in mentioned and 'free' not in f and not early:
        needs.append('세액공제 안 받은 돈(비과세 원금) 금액 — 인출 순서상 가장 먼저 빠져 세금 계산이 달라짐')
        f['free'] = None                  # 금액을 모르면 배분 순서를 정할 수 없다 → 규칙만
    if 'free' not in f:
        f['free'] = 0.0
        if 'free' not in mentioned and (f.get('retire') or f.get('credit')):
            assume.append('세액공제 안 받은 돈은 없다고 봄')
    if 'retire' not in f and 'retire' not in mentioned and (free_known or f.get('credit')):
        f['retire'] = 0.0                 # 퇴직금 얘기가 없는 연금저축 해지
    if not early and 'credit' not in f and f.get('retire') is not None and B is None and w <= f['retire'] + (f['free'] or 0):
        f['credit'] = 0.0                 # 인출이 퇴직금 안에서 끝나면 ③ 금액은 몰라도 된다
    if early and not free_known and not f.get('retire') and not f.get('credit'):
        irp_l, irp_k = _irp_early(q, acct)
        cl, ck = _cancel_check(q, acct)
        irp_l, irp_k = irp_l + cl, irp_k + ck
        if 'retire' in mentioned:
            # 퇴직금이 섞였으면 퇴직소득세가 개인마다 달라 '최대'도 말할 수 없다
            lines = [f'입력: {acct} {won(w)} 해지·인출' + (f', 나이 {age}세' if age else ''),
                     '55세 전 인출·해지는 연금외수령(R-04) — ① 세액공제 안 받은 돈 비과세, ② 퇴직금 퇴직소득세 100%, '
                     '③ 세액공제 받은 돈·수익 기타소득세 16.5% (인출 순서대로 빠짐)'] + irp_l
            return _block('doc39.txt', '55세 전·해지 인출 세금(규칙)', lines,
                          ['pension_req', 'order', 'free', 'early_credit', 'early_retire'] + irp_k, numbers=False,
                          needs=['재원별 금액(세액공제 안 받은 돈·퇴직금·세액공제 받은 돈과 수익)',
                                 '퇴직소득세(원천징수영수증의 퇴직소득세 또는 실효세율)'])
        # '연금저축 2,000만 원 해지' — 재원 구성을 모르면 전부 ③으로 본 값이 최대 세금이다
        upper = w * 0.165
        lines = [f'입력: {acct} {won(w)} 해지·인출' + (f', 나이 {age}세' if age else ''),
                 # '넣은 돈 전부가 아니라'를 '일부에만 16.5%'로 옮겨 쓴 답이 3회 모두였다 — 모두 공제받았으면 전부 과세된다
                 '55세 전 인출·해지는 연금수령이 아니라 연금외수령이다(R-04). 세금은 재원별로 정해진다(R-06·R-10) — '
                 '비과세는 세액공제 안 받은 원금뿐이고, 운용수익은 세액공제를 받았는지와 상관없이 기타소득세 16.5%',
                 f'{FUND_NAME["free"]}: 비과세 / {FUND_NAME["credit"]}: 기타소득세 16.5% / {FUND_NAME["retire"]}: 퇴직소득세 100%',
                 f'넣은 돈을 모두 세액공제 받았다면 {won(w)} 전부가 16.5% 대상(최대): {won(w)} × 16.5% = {won(upper)}',
                 f'세액공제 안 받은 돈이 X원 있으면 세금 = ({won(w)} − X) × 16.5% — 예: 500만 원이면 '
                 f'{won(w - 500)} × 16.5% = {won((w - 500) * 0.165)}' if w > 500 else
                 '세액공제 안 받은 돈이 있으면 그만큼은 세금이 없다'] + irp_l
        needs.insert(0, '세액공제 안 받은 납입금(비과세 원금) 금액 — 연말정산 때 공제받지 않은 납입액')
        return _block('doc39.txt', '55세 전·해지 인출 세금', lines,
                      ['pension_req', 'order', 'free', 'early_credit', 'early_retire', 'early_out'] + irp_k,
                      key_figure=won(upper), needs=needs, figures=[won(upper)])
    # 연금수령한도
    n = p['year_n']
    lim = None
    if not early:
        if n is None:
            needs.append('연금수령연차(연금 개시가 가능해진 해부터 몇 년째인지) — 한도 계산에 필요')
            if B is None:
                needs.append('연금계좌 평가액(올해 1월 1일 또는 개시일 기준) — 한도 계산에 필요')
        elif n <= 10:
            if B is None:
                needs.append('연금계좌 평가액(올해 1월 1일 또는 개시일 기준) — 한도 계산에 필요')
            else:
                lim = B / (11 - n) * 1.2
        keys += ['pension_req', 'no_period_req', 'limit_formula']
        if p['year_assumed']:
            keys += ['limit_year1', 'year_count']
    eff = p['retire_eff']
    if eff is None and p['rtax'] is not None and f.get('retire'):
        eff = p['rtax'] / f['retire'] * 100
    if f.get('retire') is None and 'retire' in mentioned:
        needs.append('퇴직금(이연퇴직소득) 금액')
    if 'credit' in mentioned and f.get('credit') is None and not (f.get('retire') is not None and w <= f['retire'] + (f['free'] or 0)):
        needs.append('세액공제 받은 돈·수익 금액')
    retire_used = f.get('retire') and w > (f.get('free') or 0)
    if eff is None and ('retire' in mentioned or f.get('retire')) and (retire_used or f.get('retire') is None):
        needs.append('퇴직소득세(원천징수영수증의 퇴직소득세 또는 실효세율)')
    m = p['actual_n']
    if f.get('retire') and not early:
        keys += ['retire_years', 'retire_over'] + (['actual_year'] if m else [])
        if m is None:
            m = 1 if p['year_assumed'] or n == 1 else None
            if m is None:
                m = 1
                assume.append('연금 실제 수령 10년차 이하(퇴직소득세 30% 감면)')
    if early:
        keys += ['pension_req', 'early_credit', 'early_retire']
    elif f.get('credit') or 'credit' in mentioned:
        keys += ['limit_rates', 'over_limit']
    head = [f'입력: {acct} 인출 {won(w)}' + (f', 나이 {age}세' if age else '')
            + (f', 평가액 {won(B)}' if B is not None else '')
            + ''.join(f', {FUND_NAME[k].split(" ", 1)[1]} {won(f[k])}' for k in ('free', 'retire', 'credit')
                      if f.get(k)) + (f', 퇴직소득세 실효세율 {eff:g}%' if eff is not None else '')]
    if early:
        head.append('55세 전 인출·해지 → 전액 연금외수령(R-04): 재원별로 ① 비과세 ② 퇴직소득세 100% ③ 기타소득세 16.5%')
    else:
        req = '만 55세 이상' + (' ✓' if band_age is not None and (isinstance(band_age, tuple) or band_age >= 55)
                               else '(나이 확인 필요)')
        if band_age is None and not p['future']:
            needs.append('나이(만 55세 이상인지 — 연금수령 요건)')
        req += ' · 퇴직금이 있어 가입기간 5년 요건 없음' if f.get('retire') or 'retire' in mentioned else ' · 가입 5년 이상'
        head.append(f'연금수령 요건(R-04): {req}')
        if n is not None and n >= 11:
            head.append('연금수령연차 11년차부터는 연금수령한도가 없다')
        elif lim is not None:
            head.append(f'연금수령한도(R-05) = {won(B)} ÷ (11 − {n}) × 120% = {won(lim)}'
                        + (' (올해가 연금 개시 가능한 첫해 = 1년차라고 봄)' if p['year_assumed'] else ''))
            if p['year_assumed']:
                assume.append('올해가 연금수령연차 1년차(2013.3.1 이후 가입, 올해 처음 수령 요건 충족)')
    # 배분 순서가 모두 정해져야 금액을 낸다
    order_known = all(f.get(k) is not None for k in ('free', 'retire')) and \
        (f.get('credit') is not None or w <= (f.get('free') or 0) + (f.get('retire') or 0))
    if not order_known or (not early and lim is None and not (n is not None and n >= 11)):
        lines = head + ['인출 순서(R-06): ① 세액공제 안 받은 돈 → ② 퇴직금 → ③ 세액공제 받은 돈·수익 — 고객이 고를 수 없다',
                        '한도 안에서 받은 퇴직금은 원래 퇴직소득세의 70%(실제수령 10년차 이하, 11~20년차 60%, 21년차~ 50%), '
                        '한도를 넘긴 퇴직금은 퇴직소득세 100%, 한도를 넘긴 ③ 재원은 기타소득세 16.5%(R-09·R-10)']
        if f.get('retire') is None and 'retire' in mentioned:
            lines.append(f'인출 {won(w)}이 퇴직금보다 적으면 전부 퇴직금에서 나간다(공제 안 받은 돈이 없을 때)')
        if early:
            cl, ck = _cancel_check(q, acct)
            lines, keys = lines + cl, keys + ck
        return _block('doc39.txt', '재원별 인출 세금(규칙)', lines,
                      list(dict.fromkeys(keys + ['retire_in', 'retire_over', 'over_limit'])), numbers=False,
                      needs=needs, assume=assume)
    srcs = [('free', f.get('free') or 0.0), ('retire', f.get('retire') or 0.0), ('credit', f.get('credit') or 0.0)]
    if band_age is None and not early:
        assume.append('만 55세 이상으로 봄(연금수령 요건)')
    lines, total, rows, short = _year_tax(srcs, w, lim, eff, m, band_age, early)
    lines = head + [
        '인출 순서(R-06): ' + ' → '.join(f'{FUND_NAME[k]} {won(t)}' for k, t, _, _ in rows)
        + (f' (계좌 잔액보다 {won(short)} 많음)' if short else '')] + lines
    figures = []
    key = None
    if total is not None:
        key = won(total)
        figures.append(key)
        lines.append(f'올해 예상 세금 합계 = {key}')
    # 나눠 받기 비교 — 한도를 넘는 인출이면 '올해 한도까지 + 내년 나머지'를 나란히(고르지 않고 비교만)
    over_sum = sum(o for _, _, _, o in rows)
    if not early and lim is not None and over_sum > 0 and B is not None and total is not None:
        rest = w - lim
        n2 = n + 1
        left = [(k, max(0.0, a - next((t for kk, t, _, _ in _alloc(srcs, lim, None)[0] if kk == k), 0.0))) for k, a in srcs]
        lim2 = None if n2 >= 11 else (B - lim) / (11 - n2) * 1.2
        l1, t1, _, _ = _year_tax(srcs, lim, lim, eff, m, age, False)
        l2, t2, _, _ = _year_tax(left, rest, lim2, eff, (m or 1) + 1, age + 1 if age else None, False)
        if t1 is not None and t2 is not None:
            lines.append(f'비교(나눠 받기): 올해 한도까지 {won(lim)} → {won(t1)} + 내년 나머지 {won(rest)}'
                         # 내년 한도 값만 적자 '1억 ÷ (11 − 2) × 120%'처럼 맞지 않는 식을 지어낸 답이 있었다 — 식을 함께
                         + (f'(내년 한도 = ({won(B)} − {won(lim)}) ÷ (11 − {n2}) × 120% = {won(lim2)} 안, '
                            f'내년 평가액은 올해 인출만 뺀 값)' if lim2 is not None and rest <= lim2 else '')
                         + f' → {won(t2)} = 합계 {won(t1 + t2)}  ·  올해 한꺼번에 {won(w)} → {key}')
            figures += [won(t1), won(t2), won(t1 + t2)]
            assume.append('내년 평가액은 수익률 0%로 봄(올해 인출만 뺌)')
            if re.search(r'한도까지만|나눠\s*(?:받|빼)|나머지는\s*내년|내년에\s*(?:받|빼)', q):
                # 고객이 나눠 받는 경우를 물었다 — 핵심 금액은 나눠 받기 합계
                key = won(t1 + t2)
                lines.append(f'질문한 방식(올해 한도까지 + 내년 나머지)의 두 해 세금 합계 = {key} '
                             f'(한꺼번에 받을 때보다 {won(total - t1 - t2)} 적음)')
    if lim is not None:
        figures.append(won(lim))
    keys += ['retire_in'] if f.get('retire') else []
    if early:
        cl, ck = _cancel_check(q, acct)
        lines, keys = lines + cl, keys + ck
    return _block('doc39.txt', '재원별 인출 세금', lines, list(dict.fromkeys(keys)), numbers=True,
                  key_figure=key, needs=needs, assume=assume, figures=figures)


# ---------------------------------------------------------------------------
# 공용 계산 함수 — 질문 문장 없이 숫자로 부른다(알림 alerts.py · DIY 설계 diy.py · 시연 화면).
# 질문 경로(compute)와 같은 규칙·같은 식을 쓴다. 금액 단위는 모두 '만 원'.
# ---------------------------------------------------------------------------
def credit_rate(income, kind='총급여'):
    """세액공제율(%) — R-02. kind: '총급여' 또는 '종합소득'."""
    return 16.5 if income <= (5500 if kind == '총급여' else 4500) else 13.2


def eligible(savings, irp):
    """세액공제 대상 금액 — R-01. 연금저축 600, 합산 900."""
    return min(min(savings, 600) + irp, 900)


def isa_extra(isa_amount):
    """ISA 만기 전환 추가 공제 대상 — R-03."""
    return min(isa_amount * 0.1, 300)


def pension_limit(balance, year_n):
    """연금수령한도 — R-05. 11년차부터 None(한도 없음)."""
    return None if year_n >= 11 else balance / (11 - year_n) * 1.2


def age_rate(age):
    """연금소득세율(%) — R-07. 55세 미만이면 None."""
    r = rate_for_age(age)
    return r[0] if r else None


def withdraw_tax(w, free=0.0, retire=0.0, credit=0.0, balance=None, year_n=None, eff=None, actual_n=None,
                 age=None, early=False):
    """한 해 인출의 재원별 세금 — R-04·R-05·R-06·R-09·R-10.
    → {'limit', 'rows': [(재원, 인출, 한도 안, 한도 밖)], 'total'(eff 가 필요한데 없으면 None), 'lines', 'short'}"""
    lim = None
    if not early and balance is not None and year_n is not None:
        lim = pension_limit(balance, year_n)
    lines, total, rows, short = _year_tax([('free', free), ('retire', retire), ('credit', credit)], w, lim, eff,
                                          actual_n or 1, age, early)
    return {'limit': lim, 'rows': rows, 'total': total, 'lines': lines, 'short': short}


def restate_split(p):
    """재원별 인출 계산의 조건을 모두 담아 '올해 한도까지만 받고 나머지는 내년에' 를 묻는 한 문장.
    다음 행동 버튼이 이 문장을 그대로 보낸다 — 앞 대화 재작성이 금액을 잃거나 지어내는 일을 피한다."""
    f = p.get('fund') or {}
    parts = []
    if isinstance(p.get('age'), int):
        parts.append(f'만 {p["age"]}세')
    if p.get('balance') is not None:
        parts.append(f'IRP 평가액 {won(p["balance"])}')
    names = {'retire': '퇴직금', 'credit': '세액공제 받은 돈과 수익', 'free': '세액공제 안 받은 돈'}
    for k in ('retire', 'credit', 'free'):
        if f.get(k):
            parts.append(f'{names[k]} {won(f[k])}')
    if p.get('retire_eff') is not None:
        parts.append(f'퇴직소득세 실효세율 {p["retire_eff"]:g}%')
    elif p.get('rtax') is not None:
        parts.append(f'퇴직소득세 {won(p["rtax"])}')
    if p.get('year_n'):
        parts.append(f'연금수령연차 {p["year_n"]}년차')
    w = p.get('withdraw')
    if not w:
        return ''
    return (', '.join(parts) + f'예요. 올해 {won(w)}을 한꺼번에 빼지 않고, 올해는 연금수령한도까지만 받고 나머지는 '
            '내년에 받으면 세금이 어떻게 달라져요?')


def unavoidable_rule(p, q, early=None):
    """부득이한 사유 인출(R-11) — 금액 없이 규칙만. 사유로 인정받지 못할 때(연금외수령)의 규칙도 함께 싣는다.
    요양 기준은 세법상 3개월 이상이다(doc1 부득이한 사유 목록, doc20 표 주석) — 6개월은 근퇴법 중도인출(R-13) 기준."""
    if early is None:
        early = bool(p['early'] or (isinstance(p['age'], int) and p['age'] < 55 and not p['future']))
    lines = ['세법상 부득이한 사유(가입자의 사망·해외이주·천재지변·가입자 또는 부양가족의 3개월 이상 요양·파산 또는 개인회생 등)로 '
             '인출하면 55세 전이나 중도 해지여도 연금수령으로 보아 세액공제 받은 돈·수익에 나이별 연금소득세 5.5%(69세 이하)·'
             '4.4%(70~79세)·3.3%(80세 이상) 분리과세 — 연 1,500만 원을 넘어도 다른 소득과 합산하지 않는다',
             '사유가 확인된 날부터 6개월 안에 확인 서류를 금융회사에 내야 한다 — 놓치거나 서류가 없으면 일반 인출로 기타소득세가 부과될 수 있다']
    keys = ['unavoidable', 'unavoidable_care', 'unavoidable_sep', 'unavoidable_doc']
    if re.search(r'요양|입원|치료|질병|부상|아파|투병', q):
        lines.append('요양은 연금소득세 적용에 한도가 있다(200만 원 + 의료실비 + 휴직 월수 × 150만 원)')
    if p.get('emigrate'):
        lines.append('해외이주(이민): 근퇴법상 IRP 중도인출 사유는 아니어서 IRP는 부분 인출이 아닌 전액 해지만 가능하지만, '
                     '세법상 부득이한 사유에는 해당한다. 「해외이주법」에 따른 해외이주신고확인서가 필요하고, '
                     '단순 장기체류나 영주권 취득만으로는 인정되지 않는다')
        lines.append('계좌에 퇴직금(이연퇴직소득)이 있으면 퇴직금 입금일부터 3년이 지나야 퇴직금도 연금수령으로 보아 퇴직소득세 30% 감면 — '
                     '3년이 안 됐으면 납입금·운용수익 부분만 연금소득세 저율과세')
        keys += ['unavoidable_emigrate', 'emigrate_proof', 'emigrate_retire']
    else:
        lines.append('퇴직금 재원의 부득이한 사유 인출 과세는 문서마다 조건이 달라(입금 후 3년 등) 금액을 계산하지 않음')
    if early:
        # 사유로 인정받지 못하면 — '그냥 깨면 16.5% 다 떼이죠?'에 답하려면 이 규칙도 있어야 한다
        lines.append('부득이한 사유로 인정받지 못하면 55세 전 인출·해지는 연금외수령 — 세금은 인출 순서대로 재원별: '
                     '① 세액공제 안 받은 원금은 비과세, ② 퇴직금은 퇴직소득세 100%, ③ 세액공제 받은 돈과 운용수익(공제 여부와 '
                     '상관없이)은 기타소득세 16.5% — 넣은 돈을 모두 세액공제 받았다면 전부 16.5%')
        keys += ['order', 'free', 'early_credit', 'early_retire']
    else:
        keys.append('order')
    return _block('doc20.txt', '부득이한 사유 인출(규칙)', lines, keys, numbers=False)


def early_rule(p, q):
    """금액 없이 55세 전·해지 인출 과세를 묻는 질문 — 넣은 돈 전부가 아니라 재원별로 과세된다는 규칙."""
    lines = ['55세 전 인출·해지는 연금수령이 아니라 연금외수령이다(R-04)',
             '세금은 재원별로 정해진다 — 인출 순서(R-06)대로 ① 세액공제 안 받은 원금은 비과세, '
             '② 퇴직금은 퇴직소득세 100%, ③ 세액공제 받은 돈과 운용수익(공제 여부와 상관없이)은 기타소득세 16.5%(R-10). '
             '넣은 돈을 모두 세액공제 받았다면 전부 16.5%',
             '예: 2,000만 원 해지, 그중 공제 안 받은 돈 500만 원 → 1,500만 원 × 16.5% = 247만 5천 원 (전부 공제받은 돈이면 330만 원)',
             '부득이한 사유(사망·해외이주·3개월 이상 요양 등)면 연금소득세 5.5~3.3%(R-11)']
    # 이직·퇴직 때 받은 퇴직금은 IRP로 들어온다(55세 전 퇴직, doc51) — '퇴직금 깨면'은 IRP 규칙으로
    acct = 'IRP' if re.search(r'IRP|개인형|퇴직금|퇴직\s*연금', q, re.I) and '연금저축' not in q else '연금저축'
    # IRP 인출 요건·'집 사려고 중도인출'(주택은 부득이한 사유가 아님 → 16.5%) — 금액이 없는 질문에도 싣는다(발표 13장 장면, 10-14)
    hk = []
    if acct == 'IRP':
        irp_l, irp_k = _irp_early(q, 'IRP')
        lines += [x for x in irp_l if not x.startswith('세법상 부득이한 사유')]
        hk = [k for k in irp_k if k not in ('unavoidable', 'unavoidable_care', 'unavoidable_doc')]
    elif re.search(r'주택|전세|집\s*(?:사|구입|마련)', q):
        lines.append('주택 구입·전세보증금은 세법상 부득이한 사유가 아니어서 세금은 그대로다 — 세액공제 받은 돈·수익은 기타소득세 16.5%')
        hk = ['house_not_unavoidable']
    cl, ck = _cancel_check(q, acct)
    return _block('doc39.txt', '55세 전·해지 인출 세금(규칙)', lines + cl,
                  ['pension_req', 'order', 'free', 'early_credit', 'early_retire', 'unavoidable'] + hk + ck, numbers=False,
                  needs=['해지·인출하려는 금액', '그중 세액공제 안 받은 납입금(비과세 원금)'])


_DC = re.compile(r'(?<![A-Za-z])DC(?![A-Za-z])|확정\s*기여', re.I)
_RP_CTX = re.compile(r'퇴직\s*연금|(?<![A-Za-z])(?:DC|IRP)(?![A-Za-z])|확정\s*기여|개인형|퇴직금', re.I)
_RP_WITHDRAW = re.compile(r'중도\s*인출|중간에\s*(?:빼|꺼내|찾)|집\s*(?:을\s*)?(?:사|구입|마련|살)|주택\s*(?:을\s*)?(?:사|구입|마련)|전세')
_LOAN = re.compile(r'담보|대출|대여|빌리|빌릴|빌려')


def retire_plan_rule(p, q):
    """퇴직연금(DC·IRP) 중도인출·담보대출 규칙 — 금액 없이 규칙만(10-13 외부 시험 15·19·20번).
    DC 는 퇴직금 몫이 퇴직소득세, 본인이 넣은 가입자부담금 몫이 기타소득세다(doc55 표). 담보 제공은 원칙 금지·법정 사유 예외(doc16
    근퇴법 제7조), 대출 원리금 상환은 IRP 중도인출 사유(doc20 표). 사유별 한도·금리·절차는 문서에 없어 상담으로."""
    if not _RP_CTX.search(q) or ('연금저축' in q and not re.search(r'퇴직\s*연금|IRP|DC|퇴직금|개인형', q, re.I)):
        return None
    loan, wd = bool(_LOAN.search(q)), bool(_RP_WITHDRAW.search(q))
    if not (loan or wd):
        return None
    dc = bool(_DC.search(q))
    house = bool(re.search(r'주택|전세|집\s*(?:을\s*)?(?:사|구입|마련|살)', q))
    lines, keys, needs = [], [], []
    if wd:
        lines.append('퇴직연금 중도인출은 DC·IRP만 가능하고 DB는 허용되지 않는다 — 무주택자의 본인 명의 주택 구입, 전세보증금 등 '
                     '법으로 정한 사유가 있어야 한다')
        keys += ['dc_withdraw_ok', 'irp_reason_req', 'irp_reasons']
        if house:
            lines.append('주택 구입 중도인출은 신청일 기준 본인 명의 주택이 없어야 하고, 주택매매계약 체결일부터 소유권 이전 등기 후 '
                         '1개월 안에 신청한다')
            keys.append('house_req')
        lines.append('신청: DC 중도인출은 재직 중인 회사에 서류를 내고, IRP 중도인출은 가까운 지점에 내점해 신청한다')
        keys.append('dc_submit')
        if dc:
            lines.append('세금(DC): 퇴직금 몫은 퇴직소득세, 가입자부담금(본인이 추가로 넣은 돈) 몫은 기타소득세')
            keys.append('dc_tax')
            needs = ['중도인출하려는 금액', '그중 퇴직금 몫과 본인이 넣은 돈 몫', '퇴직소득세(퇴직소득 원천징수영수증)']
        else:
            lines.append('세금(IRP): ① 세액공제 안 받은 돈은 비과세, ② 퇴직금은 퇴직소득세 100%, ③ 세액공제 받은 돈·운용수익은 '
                         '기타소득세 16.5%')
            keys += ['early_retire', 'early_credit']
            if house:
                lines.append('주택 구입·전세보증금은 중도인출 사유지만 세법상 부득이한 사유는 아니어서 세금이 낮아지지 않는다'
                             '(연금소득세 5.5~3.3% 적용 안 됨)')
                keys.append('house_not_unavoidable')
            needs = ['중도인출하려는 금액', '그중 퇴직금 몫과 세액공제 안 받은 납입금']
    if loan:
        lines.append('퇴직연금 급여를 받을 권리는 원칙적으로 담보로 제공할 수 없지만, 주택 구입 등 법령이 정한 사유와 요건을 갖추면 '
                     '정해진 한도 안에서 담보로 제공할 수 있다(근로자퇴직급여 보장법 제7조)')
        lines.append('담보로 대출받은 뒤 그 대출 원리금을 갚기 위한 경우는 IRP 중도인출 사유다 — 세법상 부득이한 사유는 아니어서 '
                     '세액공제 받은 돈·수익은 기타소득세 16.5%')
        lines.append('담보대출의 구체적인 사유·한도·금리·신청 방법은 받은 문서에 없어 상담에서 확인해야 한다')
        keys += ['loan_no', 'loan_ok', 'loan_repay']
    return _block('doc49.txt' if wd else 'doc16.txt', '퇴직연금 중도인출·담보(규칙)', lines, list(dict.fromkeys(keys)),
                  numbers=False, needs=needs)


# 'DC형 연금상품으로 수령 시'(10-14 실측 재작성문)도 받는다
_PAYOUT = re.compile(r'연금(?:\s*(?:상품|계좌|형태|형식))?\s*(?:으로|로)\s*(?:받|수령|타)|연금\s*(?:을\s*)?(?:수령|개시)')
_PAYOUT_TAX = re.compile(r'세금|비과세|과세|세율|퇴직소득세|연금소득세|떼|감면')


def dc_payout_ask(q):
    """'연금으로 받으면 세금은?' 꼴 — 계좌 이름은 보지 않는다(앞 대화의 DC 를 이어 붙일 때도 쓴다)."""
    return bool(_PAYOUT.search(q) and _PAYOUT_TAX.search(q)) and not (_RP_WITHDRAW.search(q) or _LOAN.search(q))


def dc_payout_rule(p, q):
    """DC 적립금을 연금으로 받을 때 세금 — 금액 없이 규칙만(10-13 외부 시험 22번: 적립금 전체에 5.5%를 붙여 '198만 원'이라 했다).
    DC 운용수익도 퇴직 때 퇴직소득(doc42), DC 법정퇴직금은 55세 미만 퇴직이면 IRP 의무이전(doc51 표), 퇴직금 몫은 한도 안 연금수령 시
    30~50% 감면(doc39), 감면은 퇴직소득세에만·세액공제 받은 돈과 수익은 연금소득세(doc51). DC 에 본인이 넣은 돈이 IRP 로 옮겨질 때
    어느 재원으로 나뉘는지는 문서에 없어 상담으로."""
    if not (_DC.search(q) and dc_payout_ask(q)):
        return None
    lines = ['비과세는 아니다 — DC에서 운용해 생긴 수익도 퇴직할 때 퇴직소득으로 과세되고, 퇴직금 몫은 연금으로 받아도 퇴직소득세를 낸다'
             '(다만 감면됨)',
             'DC 퇴직금은 퇴직할 때 IRP로 옮겨 IRP에서 연금으로 받는다 — 55세 미만에 퇴직하면 IRP 이전이 의무, 55세 이상이면 IRP·일반계좌 중 선택',
             '퇴직금 몫을 연금수령한도 안에서 받으면 퇴직소득세를 1~10년차 30%, 11~20년차 40%, 21년차부터 50% 감면받는다'
             '(원래 세금의 70~50%만 냄)',
             '감면은 퇴직소득세에만 적용된다 — 연금계좌에서 세액공제를 받은 납입금과 그 운용수익은 연금소득세 5.5~3.3%'
             '(만 55~69세 5.5%, 70~79세 4.4%, 80세 이상 3.3%)',
             '세액공제를 받지 않은 납입금은 꺼낼 때 비과세',
             '한도를 넘겨 받으면 넘은 금액 중 퇴직금 몫은 퇴직소득세 그대로, 세액공제 받은 돈·수익은 기타소득세 16.5%',
             'DC에 본인이 추가로 넣은 돈이 IRP로 옮겨질 때 어느 몫으로 나뉘는지는 받은 문서에 없어 상담에서 확인해야 한다']
    keys = ['dc_profit', 'dc_irp_route', 'retire_in', 'retire_years', 'retire_only', 'limit_rates', 'free', 'retire_over',
            'over_limit']
    return _block('doc51.txt', 'DC 연금 수령 세금(규칙)', lines, keys, numbers=False,
                  needs=['DC 적립금(퇴직금) 규모', '퇴직소득세(퇴직소득 원천징수영수증)', '그중 본인이 넣어 세액공제 받은 돈',
                         '연금 받기 시작할 나이'])


def compute(q):
    """질문 → {'blocks': [...], 'summary': str, 'parsed': dict} 또는 None(넣을 게 없음)."""
    try:
        return _compute(norm_ws(q))
    except Exception:
        return None                       # 계산기 오류로 답변이 깨지면 안 된다


def _compute(q):
    p = parse(q)
    if p['old']:
        return None
    try:
        from understand_guard import old_pension      # 가드와 같은 정의를 쓴다
        if old_pension(q):
            return None
    except Exception:
        pass
    if p['insure'] and not p['private']:
        return None
    blocks = []
    # 재원별 인출(퇴직금이 섞인 계좌·55세 전 해지)이 먼저 — 아니면 ③ 재원 기준 연금수령한도
    pension_ctx = p['private'] or bool(p['fund_mentioned']) or '연금' in q
    src_b = by_source(p, q) if pension_ctx else None
    # '올해 처음으로 연금 받기 시작했는데 계좌에 1억'처럼 계좌 이름 없이 연금 계좌 얘기를 하는 경우도 받는다
    lim = None if src_b else \
        withdraw_limit(p, q) if p['private'] or (p['balance'] is not None and '연금' in q) else None
    if not src_b and not lim and p['early'] and pension_ctx and not p['unavoidable'] and \
            p['withdraw'] is None and not p['payout'] and _TAX_Q.search(q):
        src_b = early_rule(p, q)          # '55세 전에 빼면 넣은 돈 전부에 16.5% 떼죠?' — 금액 없는 전제 질문
    if not src_b and not lim and p['unavoidable'] and pension_ctx and _TAX_Q.search(q) and \
            (p['early'] or p['withdraw'] is not None or re.search(r'인출|해지|깨|정리|찾|빼', q)):
        # '이민 가는데 55세 전에 깨면 16.5% 다 떼이죠?' — 부득이한 사유 규칙(+ 인정받지 못할 때의 규칙)
        src_b = unavoidable_rule(p, q)
    if src_b:
        blocks.append(src_b)
    elif lim:
        blocks.append(lim)
    elif (_TAX_Q.search(q) or len(p['payout']) >= 2 and _ALT_Q.search(q)) and not p['unavoidable']:
        # '연 1,500만 원씩 받는 것과 연 2,000만 원씩 받는 것 중 어느 쪽이 나은가요?' — 세금 말이 없어도 수령 방식 비교는 세금 비교다
        b = payout(p, q)
        if b:
            blocks.append(b)
    ded_intent = _DED_Q.search(q) or (p['isa'] and re.search(r'세금|절세|아끼|아낄|이득|공제|나아|나을|유리|어디로', q)) \
        or (p['income'] and len(p['contrib']) >= 2 and re.search(r'비교|어느\s*쪽|뭐가\s*나아|차이|각각', q)) \
        or (p['income'] and _acct_choice(p, q))
    if ded_intent and (p['income'] or p['contrib'] or p['isa'] or p['income_unknown_kind']) and not blocks:
        blocks.extend(deduction(p, q))
    # 퇴직연금 중도인출·담보 규칙 — 담보를 물었거나 DC 이거나 다른 블록이 없을 때(IRP 해지 세금 블록과 겹치지 않게)
    if not any(b.get('numbers') for b in blocks) and (_LOAN.search(q) or _DC.search(q) or not blocks):
        rp = retire_plan_rule(p, q) or dc_payout_rule(p, q)
        if rp:
            blocks.append(rp)
    if not blocks:
        return None
    amount_assume = [n['text'] for n in p.get('amount_notes', [])]
    if amount_assume:
        for b in blocks:
            if b.get('numbers'):
                b['assume'] = list(dict.fromkeys(b['assume'] + amount_assume))
                b['text'] = b['text'].replace('\n[원문]\n', '\n- 금액 읽기(질문에 단위가 없어 이렇게 봄): '
                                              + ' · '.join(amount_assume) + '\n[원문]\n', 1)
                break
    rids = list(dict.fromkeys(r for b in blocks for r in b.get('rids', [])))
    return {'blocks': blocks, 'parsed': p, 'rids': rids, 'compare': bool(_COMPARE_Q.search(q)),
            'ambiguous': [n for n in p.get('amount_notes', []) if n.get('ask')],
            'needs': list(dict.fromkeys(x for b in blocks for x in b.get('needs', []))),
            'assume': list(dict.fromkeys(x for b in blocks for x in b.get('assume', []))),
            'figures': [x for b in blocks for x in b.get('figures', [])],
            'summary': ' / '.join(b['topic'] + (f'({"·".join(b["rids"])})' if b.get('rids') else '') + ': '
                                  + b['text'].split('\n')[1][2:80]
                                  + (f' → {b["key_figure"]}' if b.get('key_figure') else '')
                                  + (f' · 빠진 정보 {len(b["needs"])}개' if b.get('needs') else '') for b in blocks)}


# ---------------------------------------------------------------------------
# 사후 검사 — '1,500만 원 초과분만 16.5%' 같은 분할 과세 서술을 찾고 고친다
# ---------------------------------------------------------------------------
_MISREAD = [
    # 0: '1,500만 원 초과분(을 넘는 금액)에 16.5%/종합과세'
    re.compile(r'1[,.]?500\s*만\s*원?\s*(?:을|를)?\s*(?:초과|넘)(?:하는|는|한)?\s*(?:금액|분|부분|액수)[^.\n]{0,25}?(?:16\.5\s*%|종합\s*과세|분리\s*과세)'),
    # 1: '(1,500만 원 …) 이를 초과하는 금액/초과분에 16.5%' — 앞에 1,500만 원이 있어야 한다
    re.compile(r'초과(?:분|하는\s*(?:금액|부분))\s*(?:\(?\s*\**\d[\d,]*\s*만\s*원\**\s*\)?)?\s*(?:에|에만|만|은|는)\s*(?:대해서?(?:는|만)?\s*)?(?:\**\s*)?(?:16\.5\s*%|종합\s*과세)'),
    # 2: '1,500만 원까지는 5.5% … 초과/나머지'
    re.compile(r'1[,.]?500\s*만\s*원?\s*까지(?:는|만)?\s*[^.\n]{0,12}?(?:5\.5|4\.4|3\.3)\s*%[^.\n]{0,40}?(?:초과|나머지|그\s*이상|넘는)'),
]
# 오독을 바로잡으려고 인용한 문장, 또는 전액 과세를 말하는 문장은 오독이 아니다
_OK_SENT = re.compile(r'아니|않|전부|모두|총액|전액|전체|오해|착각|잘못|틀|흔히|생각하기\s*쉽')


def _sentences(text):
    out, start = [], 0
    for m in re.finditer(r'[.!?。]\s|\n', text):
        out.append((start, m.end()))
        start = m.end()
    if start < len(text):
        out.append((start, len(text)))
    return out


def misread_spans(text, calc=None):
    """분할 과세 오독 문장들 [(시작, 끝, 구절)]."""
    if not text:
        return []
    found = []
    for a, b in _sentences(text):
        sent = text[a:b]
        if _OK_SENT.search(sent):
            continue
        for k, rx in enumerate(_MISREAD):
            m = rx.search(sent)
            if not m:
                continue
            if k == 1:
                ctx = text[max(0, a + m.start() - 70):a + m.start()]
                if not re.search(r'1[,.]?500\s*만', ctx) or re.search(r'한도|연금외수령', sent[max(0, m.start() - 12):m.start()]) \
                        or '기타소득' in sent:
                    continue        # '연금수령한도 초과분은 16.5% 기타소득세'는 맞는 서술이다(doc39)
            found.append((a, b, m.group(0)))
            break
    if calc:
        # 숫자로 확인: 연 X(>1,500)를 '1,500까지 r% + 초과분 16.5%'로 나눠 계산한 금액
        for blk in calc['blocks']:
            x = blk.get('yearly')
            if not x or x <= 1500 or blk['topic'] != '연금 수령 세금':
                continue
            band = rate_for_age(rate_age(calc['parsed']))
            wrong = [won((x - 1500) * 0.165)]
            if band:
                wrong.append(won(1500 * band[0] / 100 + (x - 1500) * 0.165))
            for w in wrong:
                if w in blk['text']:
                    continue        # 계산 블록에 정답으로도 나오는 금액은 오독 표지가 아니다
                for m in re.finditer(re.escape(w), text):
                    if any(a <= m.start() < b for a, b, _ in found):
                        continue
                    sa, sb = next(((a, b) for a, b in _sentences(text) if a <= m.start() < b), (m.start(), m.end()))
                    if re.search(r'초과|나머지|합계|총', text[max(sa, m.start() - 40):m.start()]):
                        found.append((sa, sb, w))
    return sorted(set(found))


def misread(text, calc=None):
    """오독 구절 하나(없으면 None)."""
    s = misread_spans(text, calc)
    return s[0][2] if s else None


def correction(calc, cite=None):
    """오독 문장을 갈아 끼울 문장."""
    ref = f'[문서{cite}]' if cite else ''
    for blk in (calc or {}).get('blocks', []):
        x = blk.get('yearly')
        if blk['topic'] == '연금 수령 세금' and x and x > 1500:
            return (f'연 {won(x)}은 1,500만 원을 넘으므로 초과분만이 아니라 수령액 전액({won(x)})이 종합과세 또는 '
                    f'16.5% 분리과세 대상이며, 16.5% 분리과세를 고르면 {won(x * 0.165)}입니다{ref}. ')
    return ('세액공제 받은 납입금·운용수익을 연 1,500만 원 넘게 연금으로 받으면 초과분만이 아니라 수령액 전액이 '
            f'종합과세 또는 16.5% 분리과세 대상입니다{ref}. ')


def _split_pieces(calc):
    """분할 과세로 나눠 계산하면 나오는 금액들 — '1,500만 원까지 r%' 몫, '초과분 16.5%' 몫, 그 합.
    계산 블록에 정답으로도 나오는 금액은 뺀다."""
    out = set()
    for blk in (calc or {}).get('blocks', []):
        x = blk.get('yearly')
        if not x or x <= 1500 or blk['topic'] != '연금 수령 세금':
            continue
        over = (x - 1500) * 0.165
        band = rate_for_age(rate_age(calc['parsed']))
        rates = [band[0]] if band else [r for r, _ in _BANDS]
        low = {won(1500 * r / 100) for r in rates}
        out |= {won(over)} | {won(1500 * r / 100 + over) for r in rates}
        out -= {w for w in list(out) if w in blk['text']}
        # '1,500만 원까지 5.5% → 82만 5천 원'은 블록에도(1,500만 원 이하로 받을 때의 비교값) 나올 수 있다 —
        # 오독과 함께 '1,500만 원까지·이하' 구간 몫으로 쓰였을 때만 분할 조각이다
        out |= {'LOW:' + w for w in low}
    return out


_LOW_CTX = re.compile(r'1[,.]?500\s*만\s*원?\s*(?:까지|이하|이내)')


def _is_piece(seg, pieces):
    if any(w in seg for w in pieces if not w.startswith('LOW:')):
        return True
    return bool(_LOW_CTX.search(seg)) and any(w[4:] in seg for w in pieces if w.startswith('LOW:'))


_SPLIT_CTX = re.compile(r'1[,.]?500\s*만\s*원?\s*(?:까지|이하|이내)|초과|합계|따라서|총|더하면|\+')


def repair(text, calc=None, cite=None):
    """(고친 글, 고친 곳 수). 오독 문장 첫 개를 바른 문장으로 바꾸고 나머지 오독 문장은 지운다.
    분할 계산의 나머지 조각(1,500만 원까지 몫·초과분 몫·합계 줄)도 지운다 — 검토: 오독 문장만 바꾸면
    '따라서 세금은 82만 5천 + 49만 5천 = 132만 원'이 남았고, 표 안에서 고치면 표가 깨졌다."""
    if not misread_spans(text, calc):
        return text, 0
    pieces = _split_pieces(calc)
    n = 0
    # 1) 표 — 오독이나 분할 조각이 든 행을 지우고, 바른 문장은 표 뒤에 둔다. 머리줄만 남은 표는 통째로 지운다
    lines = text.split('\n')
    out_lines, table, put_after = [], [], False

    def flush():
        nonlocal table, put_after
        body = [t for t in table if not re.match(r'\s*\|?\s*:?-{2,}', t)]
        if len(body) > 1:
            out_lines.extend(table)
        if put_after:
            out_lines.append('')
            out_lines.append(correction(calc, cite).rstrip())
        table, put_after = [], False

    for ln in lines:
        if ln.lstrip().startswith('|'):
            bad = misread_spans(ln, calc) or _is_piece(ln, pieces)
            if bad:
                n += 1
                put_after = put_after or bool(misread_spans(ln, calc)) or bool(_SPLIT_CTX.search(ln))
                continue
            table.append(ln)
            continue
        if table or put_after:
            flush()
        out_lines.append(ln)
    if table or put_after:
        flush()
    text2 = '\n'.join(out_lines)
    corrected = n > 0 and correction(calc, cite).rstrip() in text2
    # 2) 문장 — 첫 오독 문장은 바른 문장으로(표에서 이미 넣었으면 지우기만), 나머지 오독·분할 조각 문장은 지운다
    spans = misread_spans(text2, calc)
    drop = [(a, b) for a, b in _sentences(text2)
            if not any(a <= x < b for x, _, _ in spans) and _is_piece(text2[a:b], pieces)
            and _SPLIT_CTX.search(text2[a:b])]
    marks = sorted([(a, b, 'fix') for a, b, _ in spans] + [(a, b, 'drop') for a, b in drop])
    out, last = [], 0
    for a, b, kind in marks:
        if a < last:
            continue
        out.append(text2[last:a])
        if kind == 'fix' and not corrected:
            tail = '\n' if text2[a:b].endswith('\n') else ''
            out.append(correction(calc, cite).rstrip() + (tail or ' '))
            corrected = True
        elif text2[a:b].endswith('\n'):
            out.append('\n')
        last = b
        n += 1
    out.append(text2[last:])
    fixed = re.sub(r'\n{3,}', '\n\n', ''.join(out))
    fixed = '\n'.join(ln for ln in fixed.split('\n') if not re.fullmatch(r'\s*[-*]\s*', ln))
    return fixed, n
