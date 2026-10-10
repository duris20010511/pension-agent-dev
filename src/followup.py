# -*- coding: utf-8 -*-
"""
이어지는 질문 재작성(O5) — "그럼 70살이면요?" 를 앞 대화로 "70살인데 IRP에서 연 1,800만 원 받으면 세금은?" 으로.

검색·질의 확장·세금 계산(taxcalc)은 질문 하나만 보고 동작한다. 이어지는 질문을 그대로 넣으면 나이·금액·계좌가 빠져
엉뚱한 문서를 찾고 계산도 못 한다. 그래서 그 앞에서 한 번 고쳐 쓴다.

모델은 HCX-005 — 분당 한도가 생성(HCX-007)과 따로라 생성 처리량을 깎지 않는다. 처음엔 경량 모델(DASH-002)을
썼는데 직전 답에 기대는 이어 묻기에서 금액을 지어내거나 뜻을 바꿔 HCX-005 로 바꿨다(아래 REWRITE_MODEL 주석).

검사 — 경량 모델은 새 값을 놓칠 때가 있다(대화 43개 측정: '그럼 70살이면요?'에 63살을 그대로 두거나,
'IRP로 옮기면요?'에 연금저축을 그대로 둠). 그래서 마지막 질문에 나온 나이·금액·숫자·계좌가 결과에 그대로
있는지 코드로 확인하고, 빠졌으면 무엇이 빠졌는지 짚어 한 번 더 쓰게 한다. 그래도 나이만 틀리면 나이 숫자를
직접 바꾸고, 그 밖에는 memory.fallback_rewrite 로 대체한다.
"""
import os
import re

import clova
import memory
import taxcalc

# 대화 43개(이어지는 턴 80개) 비교: DASH-002 숫자 일치 75.0% · 뜻 90.0% · 재시도 8회,
# HCX-005 숫자 일치 78.8% · 뜻 91.1% · 재시도 2회(호출당 약 1.5초). 'IRP도 300 더 넣으면요?'처럼
# 직전 답의 금액(최대 600만 원)에 더하는 질문을 DASH 는 '각각 300만 원씩'으로 잘못 풀었고 HCX-005 는 맞혔다.
# 둘 다 생성(HCX-007)과 분당 한도가 따로다. 운영에서 HCX-005 는 이것 말고 쓰지 않는다(채점은 측정 때만).
REWRITE_MODEL = os.environ.get('AGENT_REWRITE_MODEL', 'HCX-005')

SYSTEM = """너는 연금 상담 대화에서 고객의 [마지막 질문]을 앞 대화 없이도 뜻이 통하는 완전한 질문 하나로 고쳐 쓴다.

규칙
1. 답하지 말고, 고쳐 쓴 질문 한 줄만 출력한다.
2. 마지막 질문이 바꾼 조건(나이·금액·계좌·소득·상품)은 반드시 새 값으로 쓰고, 바꾸지 않은 조건은 앞 대화에서 그대로 가져온다.
3. 마지막 질문이 다른 주제로 넘어갔으면 앞 대화의 조건을 붙이지 말고 마지막 질문을 거의 그대로 쓴다.
4. '그거·그 펀드·아까 말한 것' 같은 말은 앞 대화에서 가리키는 대상의 이름으로 바꾼다.
5. 고객이 쓴 요청·지시 문장은 지우거나 고치지 말고 그대로 둔다.
6. 앞 대화에 없는 조건이나 사실을 지어내지 않는다. '상담(요약)'은 직전 답변이다 — 고객이 '그렇게·더' 같은 말로
   직전 답변의 금액을 이어받을 때만 그 금액을 쓴다.
7. 고객 나이는 'N살인데 …'처럼 고객 자신의 나이로 쓴다('N살부터'·'N세 이후'는 제도 설명처럼 읽히므로 쓰지 않는다).
8. 단위 없는 금액('300 넣으면', '150이면요')은 앞 대화의 단위(만 원)와 기간(연·월)을 붙여 쓴다.
9. 직전 상담이 계산에 필요한 정보를 물었고 마지막 말이 그 정보(평가액·재원별 금액·세율·연차 등)를 알려 주는 것이면,
   앞 질문에 그 정보를 모두 넣어 앞 질문을 다시 묻는 한 문장으로 쓴다(마지막 말의 숫자·%는 하나도 빠뜨리지 않는다).

예시
[앞 대화]
고객: 63살인데 연금저축에서 1년에 1,200만 원 받으면 세금 얼마예요?
[마지막 질문]
그럼 70살이면요?
[고쳐 쓴 질문]
70살인데 연금저축에서 1년에 1,200만 원 받으면 세금 얼마예요?

[앞 대화]
고객: 62살인데 IRP에서 연 1,800만 원 받으면 세금은 얼마예요?
[마지막 질문]
그럼 1,500만 원으로 줄이면요?
[고쳐 쓴 질문]
62살인데 IRP에서 연 1,500만 원 받으면 세금은 얼마예요?

[앞 대화]
고객: 연봉 6,000만 원인데 연금저축에 600만 원 넣으면 세액공제 얼마 받아요?
[마지막 질문]
IRP도 300 넣으면?
[고쳐 쓴 질문]
연봉 6,000만 원인데 연금저축에 600만 원, IRP에 300만 원 넣으면 세액공제 얼마 받아요?

[앞 대화]
고객: 61살인데 연금저축에서 매달 100만 원 받으면 세금 얼마 떼요?
[마지막 질문]
150이면요
[고쳐 쓴 질문]
61살인데 연금저축에서 매달 150만 원 받으면 세금 얼마 떼요?

[앞 대화]
고객: ISA 만기 3천만 원을 연금저축으로 옮기면 추가로 공제 되나요?
[마지막 질문]
IRP로 옮기면요?
[고쳐 쓴 질문]
ISA 만기 3천만 원을 IRP로 옮기면 추가로 공제 되나요?

[앞 대화]
고객: 만 62세예요. IRP에 퇴직금이랑 공제받은 돈이 섞였는데, 올해 1,800만 원 빼면 세금이 어떻게 돼요?
상담(요약): 정확히 계산하려면 연금계좌 평가액, 퇴직금 금액, 퇴직소득세, 연금수령연차가 필요합니다 …
[마지막 질문]
평가액은 1억이고 퇴직금이 6,000만 원, 나머지는 공제받은 돈이에요. 퇴직소득세 실효세율은 4%, 올해 퇴직해서 처음 받아요.
[고쳐 쓴 질문]
만 62세예요. IRP 평가액 1억 원 중 퇴직금이 6,000만 원, 나머지는 공제받은 돈이고 퇴직소득세 실효세율은 4%, 올해 퇴직해서 처음 받아요. 올해 1,800만 원 빼면 세금이 어떻게 돼요?

[앞 대화]
고객: 미래에셋 전략배분TDF 2045 수수료가 어떻게 돼요?
[마지막 질문]
그거 위험등급은요?
[고쳐 쓴 질문]
미래에셋 전략배분TDF 2045의 위험등급은 어떻게 돼요?

[앞 대화]
고객: 62살인데 IRP에서 연 1,800만 원 받으면 세금은 얼마예요?
[마지막 질문]
디폴트옵션은 어떻게 설정해요?
[고쳐 쓴 질문]
디폴트옵션은 어떻게 설정해요?"""


def _prompt(question, turns, hint='', note=''):
    lines = ['[앞 대화]']
    real = [t for t in turns[-3:] if not t.get('flagged')]
    for k, t in enumerate(real):
        lines.append(f'고객: {t.get("standalone") or t["q"]}')
        if k == len(real) - 1 and t.get('answer'):
            # 직전 답변의 첫머리 — 'IRP도 300 더 넣으면요?'의 '더'가 무엇에 더하는지(직전 답의 '최대 600만 원')를 알게 한다.
            # 이게 없으면 경량 모델이 '연금저축에 400만 원'처럼 금액을 지어냈다(시연 대화).
            lines.append(f'상담(요약): {memory._brief(t["answer"], 160)}')
    prof = memory.profile_text(memory.merge_profile(turns))
    if prof:
        # 정정이 있으면 앞 대화에 옛 값과 새 값이 함께 있다 — 지금 값이 무엇인지 따로 알려 준다(나중 턴이 이김)
        lines += ['[지금까지 고객 정보(정정 반영)]', prof]
    lines += ['[마지막 질문]', question]
    if hint:
        lines.append(f'(주의: 마지막 질문의 {hint}을(를) 고쳐 쓴 질문에 반드시 그대로 넣어라)')
    if note:
        lines.append(f'(주의: {note})')
    lines.append('[고쳐 쓴 질문]')
    return '\n'.join(lines)


# '150 세금은요'의 '50 세'를 나이로 읽지 않게 앞(숫자)·뒤(세금·세액)를 막는다(검토에서 나이 보정이 61세를 50세로 바꾼 사례)
_AGE_ANY = re.compile(r'(?<![\d,.])(\d{2})\s*(?:살|세(?![금액율제법]))')
# 단위 없는 숫자 — 'TDF2040'·'KOSPI200'·'S&P500'처럼 이름에 붙은 숫자, '70대', 2020~2079 같은 연도·빈티지는 금액이 아니다
_BARE = re.compile(r'(?<![\w&.,])(\d{1,3}(?:,\d{3})+|\d{2,5})(?![\d,]*\s*(?:만|원|억|천|세|살|%|년|월|개|일|시|번|등급|차|대))')
_VINTAGE = re.compile(r'^20[2-7]\d$')


def must_keep(q):
    """마지막 질문에서 결과에 그대로 남아야 할 것: 나이, 금액(만원), 단위 없는 숫자, 계좌·상품.
    '연금저축 아니고 IRP'처럼 부정한 계좌는 남기지 않아도 된다."""
    ages = {int(a) for a in _AGE_ANY.findall(q)}
    amounts = {round(v) for v, _, _ in taxcalc.money_spans(q)}
    bare = {int(x.replace(',', '')) for x in _BARE.findall(q)
            if int(x.replace(',', '')) >= 50 and not _VINTAGE.match(x)}
    prods = set()
    for name, rx in memory._PRODUCTS:
        for m in re.finditer(rx, q, re.I):
            if not re.match(r'\s*(?:은|는|이|가)?\s*(?:아니|말고|대신)', q[m.end():m.end() + 8]):
                prods.add(name)
    return ages, amounts, bare, prods


def _known_amounts(q, turns):
    """질문과 앞 대화에 나온 금액(만원)과 그 월→연 환산값 — 재작성 결과에 이것 밖의 금액이 있으면 지어낸 것이다."""
    # 앞 답변의 금액도 넣는다 — '600만 원까지 넣으세요'라고 답한 뒤 'IRP도 300 더 넣으면요?'의 600은 지어낸 것이 아니다
    texts = [q] + [x for t in (turns or []) if not t.get('flagged')
                   for x in (t.get('q', ''), t.get('standalone', ''), t.get('answer', ''))]
    known = set()
    for s in texts:
        for v, a, _ in taxcalc.money_spans(s):
            known.add(round(v))
            if re.search(r'반반|반씩|절반|둘이|부부|나눠\s*(?:서|넣|받)', q):
                known.add(round(v / 2))                            # '반반' 나누기 — 지금 질문이 나누기를 말할 때만
            if re.search(r'월|매달|달마다|한\s*달', s[max(0, a - 10):a]):
                known.add(round(v * 12))                           # 월→연 환산(월 금액일 때만)
        for b in _BARE.findall(s):
            known |= {int(b.replace(',', '')), int(b.replace(',', '')) * 12}
        # '총급여 5천'·'연봉 7천'·'천만원'·'2천5백' — 만 없이 쓴 한국어 금액(첫 구현이 이걸 못 읽어 맞는 재작성을 지어냈다고 봤다)
        for a, b in re.findall(r'(\d)\s*천(?:\s*(\d)\s*백)?', s):
            known.add(int(a) * 1000 + (int(b) * 100 if b else 0))
        if re.search(r'(?<!\d)천\s*만', s):
            known.add(1000)
        if re.search(r'(?<!\d)백\s*만', s):
            known.add(100)
    return known


def missing(q, out, turns=None):
    ages, amounts, bare, prods = must_keep(q)
    ages_o, amounts_o, _, _ = must_keep(out)
    prods_o = {name for name, rx in memory._PRODUCTS if re.search(rx, out, re.I)}
    miss = []
    if ages - ages_o:
        miss.append('나이 ' + ', '.join(f'{a}살' for a in sorted(ages - ages_o)))
    elif len(ages) == 1:
        # '70살부터는요?' → '62세인데 … 70살부터'처럼 옛 나이를 고객 나이로 남기고 새 나이를 곁들이면 계산이 옛 나이로 된다
        a = next(iter(ages))
        # 결과에 나이가 새 나이 하나뿐이면 된다('80세에 연금저축에서…'는 customer_age 가 제도 표현으로 보고 건너뛴다)
        if taxcalc.customer_age(out) != a and ages_o != {a}:
            miss.append(f"고객 나이를 {a}살로('{a}살인데 …' 꼴로, 옛 나이는 빼고)")
    if turns is not None:
        # 앞 대화에도 질문에도 없는 금액을 지어 넣었다(시연 대화: '연금저축에 400만 원'을 만들어 넣음)
        made = {a for a in amounts_o if a not in _known_amounts(q, turns)}
        if made:
            miss.append('앞 대화에 없는 금액(' + ', '.join(taxcalc.won(a) for a in sorted(made)) + ')은 넣지 말 것')
    if amounts - amounts_o:
        miss.append('금액 ' + ', '.join(taxcalc.won(a) for a in sorted(amounts - amounts_o)))
    # 단위 없는 숫자('300 넣으면')는 결과에서 '300만 원'처럼 금액이 되거나(나이면 '70살') 해야 한다
    lost = bare - amounts_o - ages_o
    if lost:
        miss.append('금액 단위(만 원)를 붙인 숫자 ' + ', '.join(str(b) for b in sorted(lost)))
    if prods - prods_o:
        miss.append('계좌·상품 ' + ', '.join(sorted(prods - prods_o)))
    # 연차를 정하는 말('올해 퇴직해서')은 계산의 전제다 — '올해 처음으로'로 바꿔 쓰면 1년차 근거가 사라져 다시 되묻게 된다
    for rx, name in ((r'(?:올해|이번에|이번\s*달|지난\s*달|최근)\s*퇴직|퇴직(?:하면서|하고서?|해서)', '올해 퇴직했다는 말'),
                     (r'\d{1,2}\s*년\s*차', '연차')):
        if re.search(rx, q) and not re.search(rx, out):
            miss.append(name)
    # 비율('퇴직소득세 실효세율 4%')도 그대로 남아야 계산이 이어진다
    pq = set(re.findall(r'(\d+(?:\.\d+)?)\s*%', q))
    po = set(re.findall(r'(\d+(?:\.\d+)?)\s*%', out))
    if pq - po:
        miss.append('비율 ' + ', '.join(f'{x}%' for x in sorted(pq - po)))
    return miss


_SAME_TOPIC = re.compile(r'세금|세율|떼|공제|한도|얼마|달라|수령|받|넣|빼|인출|옮기|이득|나아|유리')
_SWITCH = re.compile(r'^\s*(?:근데|그런데|그건\s*그렇고|다른\s*(?:얘기|질문)|참|아\s*그리고)')
_ADDITIVE = re.compile(r'(?:[가-힣A-Za-z\d]도|(?:^|\s)더|추가|합쳐|같이|함께)(?:\s|$)')


def carry_missing(question, turns, out):
    """이어 묻는 질문에서 앞 턴의 조건이 빠졌는지.

    - 숫자 없이 이어 묻는 질문('주택 구입 때문이면 달라지나요?')이면 앞 턴의 나이·금액이 그대로 남아야 한다
      (대화 43개 측정에서 경량 모델이 이런 질문에 앞 조건을 자주 빠뜨렸다 — C10·C21·C08·C20).
    - 더하는 질문('IRP도 300 넣으면?')이면 숫자가 있어도 앞 턴 금액이 남아야 한다(C04: 600을 빠뜨려 공제액이 틀어짐).
    - 주제를 바꾸는 말(근데·그런데…)로 시작하거나, 앞 턴에 없던 계좌·상품을 새로 꺼내면 검사하지 않는다.
    - 앞 턴 나이는 customer_age 로 읽는다('55살 넘으면' 같은 제도 나이는 고객 나이가 아니다)."""
    if not turns or _SWITCH.search(question):
        return []
    prev = turns[-1].get('standalone') or turns[-1]['q']
    prods_q = must_keep(question)[3]
    prods_p = {name for name, rx in memory._PRODUCTS if re.search(rx, prev, re.I)}
    additive = bool(_ADDITIVE.search(question))
    has_nums = bool(_AGE_ANY.search(question) or taxcalc.money_spans(question) or _BARE.search(question))
    if has_nums and not additive:
        return []
    if not _SAME_TOPIC.search(question):
        return []
    if prods_q - prods_p and not additive:
        return []
    age_p = taxcalc.customer_age(prev)
    _, amounts_p, _, _ = must_keep(prev)
    _, amounts_o, _, _ = must_keep(out)
    miss = []
    if isinstance(age_p, int) and not has_nums and taxcalc.customer_age(out) != age_p:
        miss.append(f'앞 대화의 나이 {age_p}살')
    if amounts_p - amounts_o:
        miss.append('앞 대화의 금액 ' + ', '.join(taxcalc.won(a) for a in sorted(amounts_p - amounts_o)))
    return miss


# '900 다 채우면'·'700 꽉 채우면' — 넣는 돈의 합계를 한도까지 채우는 말(앞 납입 금액에 더하는 말이 아니다)
_FILL = re.compile(r'(?:다|꽉|가득|모두)\s*채[우워울]')


def fill_conflict(question, turns, out):
    """합계를 채우는 질문인데 결과에 앞 턴의 납입 금액이 남았는지 → 남은 금액(만원) 목록.
    C18 대화: '그럼 900 다 채우면 공제 총 얼마예요?'를 HCX-005 가 매번 '연금저축에 600, IRP에 900 넣으면'으로 썼다
    (앞 턴의 600에 900을 더함 — 공제 대상은 우연히 같아도 다음 턴 재작성이 그 600을 이어받았다).
    '도·더·추가'가 있으면 더하는 질문이라 검사하지 않는다. 앞 턴의 소득·나이는 금액이 아니다."""
    if not turns or not out or not _FILL.search(question) or _ADDITIVE.search(question):
        return []
    _, amounts_q, bare_q, _ = must_keep(question)
    new = amounts_q | bare_q
    if not new:
        return []
    prev = turns[-1].get('standalone') or turns[-1]['q']
    p = taxcalc.parse(prev)
    _, amounts_p, bare_p, _ = must_keep(prev)
    old = (amounts_p | bare_p) - new - ({round(p['income'][1])} if p['income'] else set())
    _, amounts_o, bare_o, _ = must_keep(out)
    bad = [taxcalc.won(x) for x in sorted(old & (amounts_o | bare_o))]
    # 합계를 계좌마다로 나눠 읽었다('각각 900만 원씩, 총 1,800만 원')
    per = '씩' in out and '씩' not in question and not re.search(r'매달|매월|월\s*\d|달마다', question)
    # '둘 다에 900만 원'·'둘 다 900만 원' — 계좌마다로도 읽힌다
    per = per or bool(re.search(r'둘\s*다\s*(?:에\s*)?[\d,]', out) and '합쳐' not in out)
    if any(round(n * k) in amounts_o for n in new for k in (2, 3)) or '각각' in out or per:
        bad.append('계좌마다 따로')
    # 앞 턴의 소득은 그대로 남아야 한다(재시도에서 '연봉 8천'을 빠뜨렸다)
    po = taxcalc.parse(out)
    if p['income'] and not (po['income'] and round(po['income'][1]) == round(p['income'][1])):
        bad.append(f"소득 빠짐({taxcalc.won(p['income'][1])})")
    return bad


def _fill_note(question, bad):
    if not bad:
        return ''
    _, amounts_q, bare_q, _ = must_keep(question)
    n = ', '.join(taxcalc.won(x) for x in sorted(amounts_q | bare_q))
    old = [b for b in bad if b[0].isdigit()]
    parts = [f'마지막 질문의 {n}은 넣는 돈 전체의 합계다 — 계좌마다 {n}씩이 아니라 앞 대화의 계좌에 합쳐서 {n}']
    if old:
        parts.append(f'앞 대화의 {", ".join(old)}은 빼라')
    inc = [b for b in bad if b.startswith('소득')]
    if inc:
        parts.append('앞 대화의 소득은 그대로 넣어라')
    return ' · '.join(parts)


def _fill_template(question, turns):
    """합계 채우기 질문을 두 번 다 못 고쳐 썼을 때 — 앞 질문의 소득·계좌와 지금 합계로 직접 만든다(아니면 None).
    대체(앞 질문 붙이기)로 가면 다음 턴 재작성이 앞 질문('어디에 600 넣는 게 나아요')으로 되돌아갔다(C18 대화 실측).
    직전 답이 권한 배분('연금저축 600 + IRP 300')을 모델이 끌어오는 일이 잦아, 고객이 한 말만으로 만든다."""
    _, amounts_q, bare_q, _ = must_keep(question)
    new = amounts_q | bare_q
    if len(new) != 1 or not turns:
        return None
    prev = turns[-1].get('standalone') or turns[-1]['q']
    accts = [name for name, rx in (('연금저축', r'연금저축'), ('IRP', r'IRP|개인형')) if re.search(rx, prev, re.I)]
    if not accts:
        return None
    p = taxcalc.parse(prev)
    inc = ''
    if p['income']:
        word = '연봉' if '연봉' in prev else p['income'][0]
        inc = f"{word} {taxcalc.won(p['income'][1])}인데 "
    parts = re.split(r'채[우워울][가-힣]*\s*', question, maxsplit=1)
    tail = parts[1].strip() if len(parts) > 1 else ''
    if not re.search(r'[가-힣A-Za-z]', tail):            # '700 꽉 채우면?' — 묻는 말이 없으면 세액공제를 묻는다
        tail = '세액공제는 얼마예요?'
    where = '연금저축과 IRP에 합쳐서' if len(accts) == 2 else f'{accts[0]}에'
    out = f'{inc}{where} {taxcalc.won(next(iter(new)))}을 다 채워 넣으면 {tail}'
    return None if missing(question, out, turns) or fill_conflict(question, turns, out) else out


def _fill_last(question, turns):
    if not _FILL.search(question):
        return None
    tpl = _fill_template(question, turns)
    return tpl if tpl and not carry_missing(question, turns, tpl) else None


def _unit_fix(question, out, turns):
    """재시도도 '합쳐서 900 넣으면'처럼 단위만 빠뜨렸으면 '900만 원'을 붙여 돌려준다(아니면 None).
    규칙 8(단위 없는 금액에는 만 원)을 모델이 놓친 것만 고친다 — 100 이상(나이일 수 없는 숫자)일 때만."""
    miss = missing(question, out, turns)
    if not miss or not all(m.startswith('금액 단위') for m in miss):
        return None
    ages_o, amounts_o, _, _ = must_keep(out)
    lost = sorted(must_keep(question)[2] - amounts_o - ages_o)
    if not lost or any(b < 100 for b in lost):
        return None
    fixed = out
    for b in lost:
        m = re.search(rf'(?<![\d,])(?:{b:,}|{b})(?![\d,]*\s*(?:만|원|억|천|세|살|%|년|월|개|일|시|번|등급|차|대))', fixed)
        if not m:
            return None
        fixed = fixed[:m.start()] + taxcalc.won(b) + fixed[m.end():]
    return None if missing(question, fixed, turns) else fixed


def _call(question, turns, hint='', note=''):
    # 재작성은 1초 남짓 걸린다. 한도·장애로 늦어지면 기다리지 말고 대체로 넘어간다(기본값은 120초·6회 재시도).
    out = clova.chat([{'role': 'system', 'content': SYSTEM},
                      {'role': 'user', 'content': _prompt(question, turns, hint, note)}],
                     temperature=0.0, max_tokens=200, model=REWRITE_MODEL, timeout=15, retries=2)
    return memory.clean_rewrite(out, question)


def rewrite(question, turns):
    """(고쳐 쓴 질문, 방식). 방식: '재작성' · '재작성(재시도)' · '재작성(나이 보정)' · '재작성(단위 보정)' ·
    '재작성(합계 틀)' · '대체'."""
    try:
        t = _call(question, turns)
        if t and not missing(question, t, turns):
            carry = carry_missing(question, turns, t)
            fill = fill_conflict(question, turns, t)
            if not carry and not fill:
                return t, '재작성'
            # 앞 조건이 빠졌거나 합계를 앞 금액에 더했다 — 한 번만 다시 쓰고, 다 고쳤을 때만 바꾼다
            t2 = _call(question, turns, hint=' · '.join(carry), note=_fill_note(question, fill))
            if t2 and not missing(question, t2, turns) and not carry_missing(question, turns, t2) \
                    and not fill_conflict(question, turns, t2):
                return t2, '재작성(재시도)'
            # 두 번 다 앞 조건을 빠뜨렸다 — 빠진 질문을 쓰면 계산이 앞 조건 없이 돈다(시연 3턴: 평가액·퇴직금을 잃고
            # '나머지 900만 원'을 지어냄). 앞 질문을 붙이는 대체로 간다(대체는 계산도 앞 질문 + 지금 말로 한다).
            # 합계 채우기 질문이면 대체 전에 앞 질문의 소득·계좌로 직접 만든 문장을 먼저 써 본다
            tpl = _fill_last(question, turns)
            if tpl:
                return tpl, '재작성(합계 틀)'
            return memory.fallback_rewrite(question, turns), '대체'
        miss = missing(question, t, turns) if t else ['질문 전체']
        t2 = _call(question, turns, hint=' · '.join(miss), note=_fill_note(question, fill_conflict(question, turns, t)))
        if t2 and not missing(question, t2, turns) and not fill_conflict(question, turns, t2):
            return t2, '재작성(재시도)'
        if t2 and not fill_conflict(question, turns, t2):
            fx = _unit_fix(question, t2, turns)
            if fx:
                return fx, '재작성(단위 보정)'
        cand = t2 or t
        if cand:
            m2 = missing(question, cand, turns)
            ages_q = must_keep(question)[0]
            new_age = taxcalc.customer_age(question)
            if new_age is None and len(ages_q) == 1:
                new_age = next(iter(ages_q))          # '70살부터는요?' — 질문 속 나이가 하나뿐이면 그게 새 고객 나이다
            old_age = taxcalc.customer_age(cand)
            # 나이만 틀렸고(지어낸 금액 등 다른 문제 없음), 결과의 고객 나이가 하나로 분명할 때만 그 숫자를 바꾼다.
            # 결과에 새 나이가 '70살부터'처럼 곁들여 있으면 그 구절을 지워 고객 나이 하나만 남긴다.
            if m2 and all('나이' in x for x in m2) and isinstance(new_age, int) and isinstance(old_age, int):
                hits = [m for m in _AGE_ANY.finditer(cand) if int(m.group(1)) == old_age]
                if len(hits) == 1:
                    m = hits[0]
                    fixed = cand[:m.start(1)] + str(new_age) + cand[m.end(1):]
                    # 새 나이가 다른 자리에도 곁들여 있으면(… 70살부터) 그 곁들인 구절만 지운다
                    rest = [x for x in _AGE_ANY.finditer(fixed) if int(x.group(1)) == new_age]
                    if len(rest) > 1:
                        last = rest[-1]
                        tail = re.match(r'\s*(?:부터는?|이후에?는?|되면|일\s*때)', fixed[last.end():])
                        cut_end = last.end() + (tail.end() if tail else 0)
                        fixed = (fixed[:last.start()].rstrip() + ' ' + fixed[cut_end:].lstrip()).strip()
                    if taxcalc.customer_age(fixed) == new_age:
                        return fixed, '재작성(나이 보정)'
        tpl = _fill_last(question, turns)
        if tpl:
            return tpl, '재작성(합계 틀)'
    except Exception:
        pass
    return memory.fallback_rewrite(question, turns), '대체'
