# -*- coding: utf-8 -*-
"""
온톨로지 판독 기준표(src/onto_check.py) 시험 — API 없이 돈다.  python -X utf8 src/test_onto_check.py

모델(HCX-005) 출력은 흉내 낸 chat 함수로 넣는다. 계산 블록은 taxcalc.compute 결과를 그대로 쓰고,
계산기가 지금 다르게 읽는 경우를 만들기 어려운 칸만 손으로 만든 calc 를 쓴다.

무엇을 고정하나
  1. 규격 — check(q, calc) 는 블록 없으면 None, 있으면 flag·diffs·ask·consulted·sec. ask_message·trace_line 꼴.
  2. 칸마다 다름·같음·근거 없음·모름 — 근거 글이 질문에 없거나 모델이 모른다고 하면 막지 않는다.
     판정이 같으면(같은 세율 구간) 칸 값이 달라도 막지 않는다. 블록이 이미 밝힌 것(두 사람 각자)은 다르다고 보지 않는다.
  3. 모델 실패·시간 초과·깨진 출력은 막지 않는다(flag False). 깨진 JSON 은 너그럽게 읽는다.
  4. 데이터 주도 — 온톨로지 임시 사본에 조건을 더하면 검사가 늘고, 지우면 준다. 칸 key·ask 말을 바꾸면 프롬프트·되묻기가 따라 바뀐다.
     onto_check.py 에 속성 id·규칙 번호·칸 key·허용 값을 적지 않는다.
  5. 검증(10-15) 헛경보 — '같이·함께'·'세금 적게 내려면'은 근거가 아니다. R-01 계좌 조건은 퇴직금으로 풀리지 않는다.
     되묻기는 read.ask·unknown_say(고객 말)를 쓰고, 해만 다르면 해를 묻는다.
"""
import ast
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import onto_check  # noqa: E402
import taxcalc  # noqa: E402

results = []          # (이름, 통과 여부, 설명)


def check(name, ok, why=''):
    results.append((name, bool(ok), why))


ONTO = onto_check.load()
KEY = {a['id']: a['read']['key'] for a in ONTO['attributes'] if (a.get('read') or {}).get('key')}


def fake(cells=None, raw=None, calls=None):
    """흉내 모델 — cells {속성 id: (근거 글, 값)} 을 온톨로지 칸 key 로 바꿔 JSON 으로 돌려준다. raw 는 글 그대로."""
    def chat(msgs):
        if calls is not None:
            calls.append(msgs)
        if raw is not None:
            return raw
        return json.dumps({KEY.get(k, k): {'q': q, 'v': v} for k, (q, v) in (cells or {}).items()}, ensure_ascii=False)
    return chat


def boom(msgs):
    raise AssertionError('모델을 부르면 안 되는 자리')


def run(q, cells=None, calc=None, onto=None, raw=None, calls=None):
    c = calc if calc is not None else taxcalc.compute(q)
    return onto_check.check(q, c, onto=onto, chat=fake(cells, raw, calls))


def attrs_of(res):
    return [d['attr'] for d in (res or {}).get('diffs') or []]


def _onto_copy(edit):
    """온톨로지 사본을 고쳐 임시 파일로 — 원본은 건드리지 않는다."""
    o = copy.deepcopy(ONTO)
    edit(o)
    fd, path = tempfile.mkstemp(suffix='.json')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(o, f, ensure_ascii=False)
    return path


def _rule(o, rid):
    return next(r for r in o['rules'] if r['id'] == rid)


def _attr(o, aid):
    return next(a for a in o['attributes'] if a['id'] == aid)


# 질문(연습·평가 세트와 다른 손 문장)
Q_AGE_UNREAD = '올해 딱 70 됐어요. 연금저축에서 매달 90만 원씩 받는데 세금 얼마예요?'
Q_AGE_READ = '65세인데 IRP에서 매달 100만 원씩 연금으로 받아요. 세금 얼마예요?'
Q_MONTH = '72세예요. IRP에서 매달 80만 원씩 받는데 한 달에 세금 얼마 떼요?'
Q_REASON = '52세인데 수술해서 연금저축에서 500만 원 빼려고요. 세금 얼마예요?'
Q_BOTH = ('총급여 7천이에요. 연금저축에 4년 동안 매년 600씩 넣고 공제받았어요. 53세인데 2,400 다 해지하면 '
          '공제받은 거보다 세금이 더 나가요?')
Q_COUPLE = '와이프랑 저 각자 연금저축에 600씩 넣어요. 저는 총급여 4천인데 공제 얼마씩 받아요?'
Q_NEXT = '총급여 4천이에요. 내년에 연금저축에 600 넣으면 공제 얼마 받아요?'
Q_IRP = '총급여 5,500만 원이에요. 올해 IRP에 300만 원 넣었는데 공제 얼마예요?'


def _payout_calc(age):
    """연금 수령 세금 블록(R-07·R-08) — 계산기가 읽은 나이만 바꿔 본다."""
    return {'blocks': [{'topic': '연금 수령 세금', 'rids': ['R-07', 'R-08'], 'numbers': [1], 'text': '연금소득세'}],
            'parsed': {'age': age, 'payout': [{'v': 100.0, 'period': 'monthly'}]}}


# ---------------------------------------------------------------------------
def test_spec():
    check('규격: calc 없음 → None', onto_check.check('세금 얼마예요?', None, chat=boom) is None)
    q = '제일 좋은 연금 상품이 뭐예요?'
    check('규격: 블록 없는 질문 → None', onto_check.check(q, taxcalc.compute(q), chat=boom) is None)
    q = '52세인데 요양 때문에 연금저축에서 500만 원 빼려고요. 세금 얼마예요?'
    c = taxcalc.compute(q)
    r = onto_check.check(q, c, chat=boom) if c and c.get('blocks') else {'flag': False, 'note': '블록 없음'}
    check('규격: 숫자 없는 규칙 블록 → 모델 안 부르고 통과', r and r['flag'] is False and r.get('note'), str(r))
    r = run(Q_AGE_UNREAD, {'attr.age': ('올해 딱 70 됐어요', 70)})
    check('규격: 결과 키', r and set(r) >= {'flag', 'diffs', 'ask', 'consulted', 'sec'}, str(r and sorted(r)))
    check('규격: diff 키', r and r['diffs'] and set(r['diffs'][0]) == {'attr', 'label', 'rule', 'engine', 'question', 'quote'},
          str(r and r['diffs']))
    check('규격: consulted 는 블록 규칙·조건 속성', r and r['consulted']['rules'] == ['R-07', 'R-08']
          and 'attr.age' in r['consulted']['attrs'], str(r and r['consulted']))
    check('규격: sec 는 수', r and isinstance(r['sec'], float), str(r and r['sec']))


def test_age():
    r = run(Q_AGE_UNREAD, {'attr.age': ('올해 딱 70 됐어요', 70)})
    d = (r or {}).get('diffs') or [{}]
    check('나이 다름: 계산기가 못 읽은 나이를 질문은 말함 → 막음', r and r['flag'] and attrs_of(r) == ['attr.age']
          and d[0].get('rule') == 'R-07' and d[0].get('question') == '70세' and '딱 70' in d[0].get('quote', ''), str(r))
    r = run(Q_AGE_READ, {'attr.age': ('65세인데', 65)})
    check('나이 같음 → 통과', r and not r['flag'], str(r))
    r = run(Q_AGE_READ, {'attr.age': ('72세', 72)})
    check('나이 근거 없음(질문에 72 없음) → 통과', r and not r['flag'], str(r))
    r = run(Q_AGE_UNREAD, {'attr.age': ('', None)})
    check('나이 모름 → 통과', r and not r['flag'], str(r))
    r = run(Q_AGE_UNREAD, {'attr.age': ('올해 딱 70 됐어요', 170)})
    check('나이 범위 밖(170) → 근거로 안 봄', r and not r['flag'], str(r))
    # 모델이 다른 사람 말을 근거로 적으면 되묻기에는 그 값이 든 문장을 싣는다
    q = '누나가 75 넘으면 세금이 3.3이래요. 저는 지금 66이고 IRP에서 매달 90씩 받아요. 세금 얼마예요?'
    r = run(q, {'attr.age': ('누나가 75 넘으면', 66)}, calc=_payout_calc(None))
    d = (r or {}).get('diffs') or [{}]
    check('근거 글: 다른 사람 말 대신 값이 든 문장', r and attrs_of(r) == ['attr.age'] and '66' in d[0].get('quote', '')
          and '누나' not in d[0].get('quote', ''), str(r))
    q = '저는 65세, 남편은 66세예요. IRP에서 매달 100만 원씩 받아요. 세금 얼마예요?'
    r = run(q, {'attr.age': ('저는 65세', 65)}, calc=_payout_calc(66))
    check('나이 값은 달라도 같은 세율 구간(65·66) → 통과', r and not r['flag'], str(r))
    q = '저는 71세, 남편은 66세예요. IRP에서 매달 100만 원씩 받아요. 세금 얼마예요?'
    r = run(q, {'attr.age': ('저는 71세', 71)}, calc=_payout_calc(66))
    check('나이 구간이 갈림(66 ↔ 71) → 막음', r and attrs_of(r) == ['attr.age'], str(r))


def test_period_month():
    r = run(Q_MONTH, {'attr.ask_period': ('한 달에 세금 얼마 떼요?', '월')})
    d = (r or {}).get('diffs') or [{}]
    check('묻는 기간 다름: 한 달 치를 물음 ↔ 1년 치 계산 → 막음', r and attrs_of(r) == ['attr.ask_period']
          and d[0].get('engine') == '1년 치 금액' and d[0].get('question') == '한 달 치 금액', str(r))
    r = run(Q_AGE_READ, {'attr.ask_period': ('세금 얼마예요?', '월')})
    check('묻는 기간 근거 없음(한 달 말 없음) → 통과', r and not r['flag'], str(r))
    r = run(Q_MONTH, {'attr.ask_period': ('', '연')})
    check('묻는 기간 같음(연) → 통과', r and not r['flag'], str(r))
    q = '72세예요. 8월부터 IRP에서 매달 80만 원씩 받기 시작했어요. 올해 세금 얼마예요?'
    r = run(q, {'attr.months': ('8월부터 IRP에서 매달 80만 원씩', 5)})
    check('올해 달 수 다름: 5달 ↔ 12달 계산 → 막음', r and 'attr.months' in attrs_of(r), str(r))
    r = run(Q_MONTH, {'attr.months': ('매달 80만 원씩', 12)})
    check('올해 달 수 같음(12) → 통과', r and not r['flag'], str(r))


def test_reason():
    r = run(Q_REASON, {'attr.reason': ('수술해서', '요양')})
    d = (r or {}).get('diffs') or [{}]
    check('인출 사유 다름: 요양(부득이) ↔ 일반 인출 계산 → 막음', r and attrs_of(r) == ['attr.reason']
          and d[0].get('rule') == 'R-10', str(r))
    r = run(Q_REASON, {'attr.reason': ('500만 원 빼려고요', '주택구입')})
    check('인출 사유 근거 없음(주택구입 말 없음) → 통과', r and not r['flag'], str(r))
    r = run(Q_REASON, {'attr.reason': ('', None)})
    check('인출 사유 모름 → 통과', r and not r['flag'], str(r))
    r = run(Q_REASON, {'attr.age': ('52세인데', 52)})
    check('나이 같음(52, 55세 전 판정 같음) → 통과', r and not r['flag'], str(r))


def test_covers():
    r = run(Q_BOTH, {'attr.ask_kind': ('2,400 다 해지하면 공제받은 거보다 세금이 더 나가요?', ['인출', '납입'])})
    d = (r or {}).get('diffs') or [{}]
    check('묻는 일 다름: 해지 세금만 계산 ↔ 공제와 함께 물음 → 막음', r and attrs_of(r) == ['attr.ask_kind']
          and '함께 물음' in d[0].get('question', ''), str(r))
    r = run(Q_BOTH, {'attr.ask_kind': ('2,400 다 해지하면', ['인출'])})
    check('묻는 일 같음(인출) → 통과', r and not r['flag'], str(r))
    q = '53세예요. 연금저축 2,400 다 해지하면 세금 얼마예요?'
    r = run(q, {'attr.ask_kind': ('해지하면 세금 얼마예요?', ['인출', '납입'])})
    check('묻는 일 근거 없음(견주는 말·공제 말 없음) → 통과', r and not r['flag'], str(r))


def test_same_and_block():
    r = run(Q_COUPLE, {'attr.person': ('와이프랑 저 각자', '여럿각자')})
    check('사람: 블록이 이미 두 사람 각자라고 밝힘 → 통과', r and not r['flag'], str(r))
    r = run(Q_COUPLE, {'attr.contrib_acct': ('연금저축에 600씩', ['연금저축'])})
    check('납입 계좌 같음 → 통과', r and not r['flag'], str(r))
    calc = {'blocks': [{'topic': '세액공제', 'rids': ['R-01', 'R-02'], 'numbers': [1], 'text': '세액공제'}],
            'parsed': {'income': ['총급여', 5500], 'contrib': [{'v': 300.0, 'acct': 'savings', 'monthly': False}]}}
    r = run(Q_IRP, {'attr.contrib_acct': ('IRP에 300만 원 넣었는데', ['IRP'])}, calc=calc)
    d = (r or {}).get('diffs') or [{}]
    check('납입 계좌 다름: 고객이 말하지 않은 연금저축을 계산 → 막음', r and attrs_of(r) == ['attr.contrib_acct']
          and '연금저축' in d[0].get('engine', '') and 'IRP' in d[0].get('question', ''), str(r))
    r = run(Q_IRP, {'attr.contrib_acct': ('', None)}, calc=calc)
    check('납입 계좌 모름 → 통과', r and not r['flag'], str(r))


def _ded_calc(text='세액공제', **parsed):
    """세액공제 블록(R-01·R-02) — 계산기가 읽은 값만 바꿔 본다."""
    p = {'income': ['총급여', 6000], 'contrib': [{'v': 600.0, 'acct': 'savings', 'monthly': False}]}
    p.update(parsed)
    return {'blocks': [{'topic': '세액공제', 'rids': ['R-01', 'R-02'], 'numbers': [1], 'text': text}], 'parsed': p}


def test_review_1015():
    """검증(10-15)에서 나온 헛경보·되묻기 말 — 근거 말을 좁히고 되묻기를 고객 말로."""
    # 사람: '같이·함께'는 사람 수를 말하지 않는다 — 모델이 여럿합산이라 해도 근거 아님
    q = '남편이랑 같이 노후 준비 중인데요, 저는 총급여 6천이고 IRP에 900 넣으면 공제 얼마예요?'
    r = run(q, {'attr.person': ('남편이랑 같이', '여럿합산')})
    check('사람: 가족과 같이·함께 → 여럿 근거 아님(통과)', r and not r['flag'], str(r))
    q = '어머니랑 함께 사는데 저는 66세고 IRP에서 매달 100만 원 받아요. 세금 얼마예요?'
    r = run(q, {'attr.person': ('어머니랑 함께 사는데', '여럿각자')})
    check('사람: 함께 산다 → 여럿각자 근거 아님(통과)', r and not r['flag'], str(r))
    q = '남편이랑 제 연금저축 합치면 1,200인데 공제 얼마예요?'
    r = run(q, {'attr.person': ('남편이랑 제 연금저축 합치면', '여럿합산')}, calc=_ded_calc())
    check('사람: 합치면 + 가족 + 금액 → 막음', r and attrs_of(r) == ['attr.person'], str(r))
    # 낼 세금: 줄이고 싶다는 바람은 '적음' 근거가 아니다 — 상태 꼴만
    q = '세금 적게 내려고 연금저축에 600 넣으려고요. 총급여 6천이면 공제 얼마예요?'
    r = run(q, {'attr.tax_due': ('세금 적게 내려고', '적음')})
    check('낼 세금: 적게 내려고(바람) → 통과', r and not r['flag'], str(r))
    q = '절세가 목표라 세금 덜 내고 싶어요. 총급여 6천인데 연금저축 600 넣으면 공제 얼마예요?'
    r = run(q, {'attr.tax_due': ('세금 덜 내고 싶어요', '적음')})
    check('낼 세금: 덜 내고 싶어요(바람) → 통과', r and not r['flag'], str(r))
    q = '결정세액이 40만 원밖에 안 돼요. 총급여 6천이고 연금저축에 600 넣으면 공제 얼마예요?'
    r = run(q, {'attr.tax_due': ('결정세액이 40만 원밖에 안 돼요', '적음')}, calc=_ded_calc())
    check('낼 세금: 결정세액이 적다(상태) → 막음', r and attrs_of(r) == ['attr.tax_due'], str(r))
    # R-01 계좌 조건은 퇴직금으로 풀리지 않는다 — DB 가입자 퇴직금 질문
    q = 'DB형 퇴직연금 가입자인데 퇴직금 들어오면 세액공제 얼마 받아요?'
    r = run(q, {'attr.account_kind': ('DB형 퇴직연금 가입자인데', 'DB')}, calc=_ded_calc(fund_mentioned=['retire']))
    check('R-01 계좌: 퇴직금이 있어도 DB면 막음', r and attrs_of(r) == ['attr.account_kind'] and r['diffs'][0]['rule'] == 'R-01', str(r))
    # 나이는 같게 읽고 해만 다름 — 물을 것은 해(read.ask), 근거 글은 두 문장 다
    q = '지금 69세예요. 내년부터 연금저축에서 매달 90씩 받으면 세금 얼마예요?'
    r = run(q, {'attr.age': ('지금 69세예요', 69), 'attr.withdraw_year': ('내년부터 연금저축에서 매달 90씩', 1)})
    d = (r or {}).get('diffs') or [{}]
    wy = _attr(ONTO, 'attr.withdraw_year')
    check('해만 다름: 인출 연도로 되물음', r and attrs_of(r) == ['attr.withdraw_year'], str(r))
    check('해만 다름: 근거 글에 나이와 내년 둘 다', '69' in d[0].get('quote', '') and '내년' in d[0].get('quote', ''), str(d[0]))
    check('해만 다름: 되묻기에 read.ask 말', r and wy['read'].get('ask') and wy['read']['ask'] in r['ask'], r and r['ask'])
    # 개설일 — 속성 ask 없이 read.ask 로 묻는다(ontology.path 되묻기 글은 그대로)
    calc = {'blocks': [{'topic': '연금수령한도', 'rids': ['R-05'], 'numbers': [1], 'text': '연금수령한도'}], 'parsed': {}}
    q = '2011년에 가입한 연금저축이에요. 올해 연금수령한도가 얼마예요?'
    r = run(q, {'attr.open_date': ('2011년에 가입한 연금저축이에요', 2011)}, calc=calc)
    od = _attr(ONTO, 'attr.open_date')
    check('개설일 다름: 막고 read.ask 로 물음', r and attrs_of(r) == ['attr.open_date'] and od['read'].get('ask')
          and od['read']['ask'] in r['ask'] and not od.get('ask'), r and r['ask'])
    # 나이를 못 읽은 계산 — '저는 …로 계산했는데'에 온톨로지 unknown_say(고객 말)
    r = run(Q_AGE_UNREAD, {'attr.age': ('올해 딱 70 됐어요', 70)})
    us = _attr(ONTO, 'attr.age')['read'].get('unknown_say')
    check('나이 못 읽음: 되묻기에 unknown_say', r and us and f"저는 '{us}'" in r['ask'], r and r['ask'])
    # 묻는 기간 — 되묻기 말은 온톨로지 ask
    r = run(Q_MONTH, {'attr.ask_period': ('한 달에 세금 얼마 떼요?', '월')})
    check('묻는 기간: 되묻기에 온톨로지 ask', r and _attr(ONTO, 'attr.ask_period')['ask'] in r['ask'], r and r['ask'])


def test_model_fail():
    def timeout(msgs):
        raise TimeoutError('20초')
    c = taxcalc.compute(Q_AGE_UNREAD)
    r = onto_check.check(Q_AGE_UNREAD, c, chat=timeout)
    check('모델 시간 초과 → 막지 않음', r and r['flag'] is False and r.get('error', '').startswith('모델 실패'), str(r))
    check('모델 실패 생각 과정 한 줄', '판독 못 함' in onto_check.trace_line(r), onto_check.trace_line(r))
    r = run(Q_AGE_UNREAD, raw='죄송합니다. 질문을 이해하지 못했어요.')
    check('모델 출력 깨짐(JSON 없음) → 막지 않음', r and r['flag'] is False and r.get('error'), str(r))
    r = run(Q_AGE_UNREAD, raw="```json\n{'age': {'q': '올해 딱 70 됐어요', 'v': 70},}\n```")
    check('깨진 JSON(울타리·홑따옴표·끝 쉼표) 너그럽게', r and attrs_of(r) == ['attr.age'], str(r))
    p = onto_check.parse_reply
    check('깨진 JSON: 숫자 뒤 단위', (p('{"age": {"q": "70", "v": 70세}}') or {}).get('age', {}).get('v') == 70)
    check('깨진 JSON: 값만 적은 칸', p('{"age": 70}') == {'age': {'q': '', 'v': 70}}, str(p('{"age": 70}')))
    check('깨진 JSON: {근거 글: 값} 꼴', p('{"age": {"딱 70": 70}}') == {'age': {'q': '딱 70', 'v': 70}})
    check('깨진 JSON: None/True', (p('{"age": {"q": "", "v": None}}') or {}).get('age') == {'q': '', 'v': None})


def test_messages():
    r = run(Q_AGE_UNREAD, {'attr.age': ('올해 딱 70 됐어요', 70)})
    m = (r or {}).get('ask') or ''
    check('되묻기: 해요체 맞나요 꼴', m.startswith('제가 질문을 이렇게 읽었는데 맞나요?') and '보여요.' in m
          and '다시 계산해 드릴게요.' in m and '습니다' not in m, m)
    check('되묻기: 온톨로지 ask 말', _attr(ONTO, 'attr.age')['ask'] in m, m)
    check('되묻기: 질문 근거 글을 보여 줌', '딱 70' in m, m)
    check('되묻기: diffs 만 받아도 됨', onto_check.ask_message((r or {}).get('diffs') or []) == m)
    check('되묻기: 다름 없으면 빈 글', onto_check.ask_message([]) == '')
    t = onto_check.trace_line(r)
    check('생각 과정: 다름', t.startswith('개념 지도 판독 기준표: 규칙 R-07·R-08 조건 [') and '대조 → 다름' in t, t)
    r = run(Q_AGE_READ, {'attr.age': ('65세인데', 65)})
    t = onto_check.trace_line(r)
    check('생각 과정: 통과', t.startswith('개념 지도 판독 기준표: 규칙 R-07·R-08 조건 [나이') and '대조 → 통과' in t, t)
    check('생각 과정: 블록 없음', '대조 안 함' in onto_check.trace_line(None))


def test_prompt():
    calls = []
    run(Q_REASON, {}, calls=calls)
    sysmsg = calls[0][0]['content'] if calls else ''
    _, _, _, ids, _ = onto_check.consult(taxcalc.compute(Q_REASON))
    check('프롬프트: 조건 속성 칸만', all(f'"{KEY[i]}"' in sysmsg for i in ids)
          and not any(f'"{KEY[i]}"' in sysmsg for i in KEY if i not in ids), sysmsg[:300])
    vals = [v['v'] for v in _attr(ONTO, 'attr.reason')['read']['values']]
    check('프롬프트: 허용 값은 온톨로지 값', all(v in sysmsg for v in vals), str(vals))
    check('프롬프트: 근거 q 를 v 보다 먼저', '{"q": "근거 글", "v"' in sysmsg)
    check('프롬프트: 고객 질문은 user 칸', calls and calls[0][1]['content'].endswith(Q_REASON))


def test_data_driven():
    # 조건 하나 더하기 — R-01 에 '올해 납입분'(인출 연도 = 0) 조건을 더한 사본
    def add(o):
        _rule(o, 'R-01')['read_conditions'].append({'attr': 'attr.withdraw_year', 'test': '올해 납입분', 'cmp': {'eq': 0}})
    path = _onto_copy(add)
    try:
        cells = {'attr.withdraw_year': ('내년에 연금저축에 600 넣으면', 1)}
        r0 = run(Q_NEXT, cells)
        calls = []
        r1 = run(Q_NEXT, cells, onto=path, calls=calls)
        check('조건 더하기 전: 인출 연도는 대조 안 함', r0 and 'attr.withdraw_year' not in r0['consulted']['attrs']
              and not r0['flag'], str(r0))
        check('조건 더한 뒤: 대조 속성에 들어감', r1 and 'attr.withdraw_year' in r1['consulted']['attrs'], str(r1))
        check('조건 더한 뒤: 프롬프트 칸이 늚', calls and f'"{KEY["attr.withdraw_year"]}"' in calls[0][0]['content'])
        check('조건 더한 뒤: 검사가 잡음', r1 and attrs_of(r1) == ['attr.withdraw_year'], str(r1))
    finally:
        os.remove(path)
    # 조건 지우기 — R-07 의 나이 조건을 뺀 사본
    def drop(o):
        r = _rule(o, 'R-07')
        r['conditions'] = [c for c in r['conditions'] if c['attr'] != 'attr.age']
    path = _onto_copy(drop)
    try:
        r = run(Q_AGE_UNREAD, {'attr.age': ('올해 딱 70 됐어요', 70)}, onto=path)
        check('조건 지운 뒤: 나이 검사가 사라짐', r and 'attr.age' not in r['consulted']['attrs'] and not r['flag'], str(r))
    finally:
        os.remove(path)
    # 칸 key·ask 말 바꾸기
    def rename(o):
        a = _attr(o, 'attr.age')
        a['read']['key'] = 'holder_age'
        a['ask'] = '가입자 본인의 만 나이(생일 기준)'
    path = _onto_copy(rename)
    try:
        calls = []
        r = run(Q_AGE_UNREAD, raw='{"holder_age": {"q": "올해 딱 70 됐어요", "v": 70}}', onto=path, calls=calls)
        check('칸 key 바꾸면 프롬프트가 따라 바뀜', calls and '"holder_age"' in calls[0][0]['content']
              and '"age"' not in calls[0][0]['content'])
        check('칸 key 바꾼 사본으로도 잡음', r and attrs_of(r) == ['attr.age'], str(r))
        m = onto_check.ask_message(r['diffs'], onto=path) if r else ''
        check('ask 말 바꾸면 되묻기가 따라 바뀜', '가입자 본인의 만 나이(생일 기준)' in m, m)
    finally:
        os.remove(path)
    # 깨진 온톨로지 — 답은 나가야 한다
    fd, path = tempfile.mkstemp(suffix='.json')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('{"attributes": [')
    try:
        r = onto_check.check(Q_AGE_UNREAD, taxcalc.compute(Q_AGE_UNREAD), onto=path, chat=boom)
        check('온톨로지 깨짐 → 막지 않음', r and r['flag'] is False and r.get('error'), str(r))
    finally:
        os.remove(path)


def test_no_hardcode():
    """onto_check.py 의 글자 상수(설명 글 제외)에 속성 id·규칙 번호·칸 key·허용 값이 없어야 한다."""
    src = open(onto_check.__file__, encoding='utf-8').read()
    tree = ast.parse(src)
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(n, clean=False) is not None:
            docs.add(id(n.body[0].value))
    consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and id(n) not in docs]
    ids = {a['id'] for a in ONTO['attributes']} | {r['id'] for r in ONTO['rules']}
    keys = set(KEY.values())
    bad = sorted({c for c in consts if c in ids or c in keys or any(i in c for i in ids)})
    check('코드에 칸 목록 없음(속성 id·규칙 번호·칸 key)', not bad, str(bad))
    # 허용 값('부득이'·'개시함' …)은 비교·꺼내기 자리에 없어야 한다 — 화면 글('규칙 없음')은 값과 글자가 같아도 괜찮다
    vals = {v['v'] for a in ONTO['attributes'] for v in (a.get('read') or {}).get('values') or []}
    used = []
    for n in ast.walk(tree):
        spots = []
        if isinstance(n, ast.Compare):
            spots = [n.left] + list(n.comparators)
        elif isinstance(n, ast.Subscript):
            spots = [n.slice]
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'get' and n.args:
            spots = [n.args[0]]
        for s in spots:
            used += [x.value for x in ast.walk(s) if isinstance(x, ast.Constant) and isinstance(x.value, str)]
    bad = sorted(set(used) & vals)
    check('코드가 허용 값으로 비교하지 않음', not bad, str(bad))
    n_read = sum(1 for a in ONTO['attributes'] if (a.get('read') or {}).get('key'))
    n_cond = sum(len(r.get('read_conditions') or []) for r in ONTO['rules'])
    check('온톨로지에 판독 칸·조건이 있음', n_read >= 15 and n_cond >= 30, f'{n_read} 칸 · {n_cond} 조건')


def test_quotes():
    """새 판독 조건마다 원문 인용(quotes) — 문서에 글자 그대로."""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache = {}

    def doc(d):
        if d not in cache:
            p = os.path.join(root, 'extracted', 'docs', f'{d}.txt')
            cache[d] = re.sub(r'\s+', ' ', open(p, encoding='utf-8').read()) if os.path.exists(p) else ''
        return cache[d]
    missing, wrong = [], []
    for r in ONTO['rules']:
        for c in r.get('read_conditions') or []:
            qs = c.get('quotes') or []
            if not qs:
                missing.append(f"{r['id']} {c['attr']}")
            for x in qs:
                if re.sub(r'\s+', ' ', x['quote']).strip() not in doc(x['doc']):
                    wrong.append(f"{r['id']} {x['doc']}")
    check('판독 조건마다 원문 인용', not missing, str(missing))
    check('판독 조건 인용이 문서에 글자 그대로', not wrong, str(wrong))
    missing, wrong = [], []
    for a in ONTO['attributes']:
        r = a.get('read')
        if not r:
            continue
        qs = [x for x in list(a.get('quotes') or []) + list(r.get('quotes') or []) if isinstance(x, dict)]
        if not qs:
            missing.append(a['id'])
        wrong += [f"{a['id']} {x['doc']}" for x in qs if re.sub(r'\s+', ' ', x['quote']).strip() not in doc(x['doc'])]
    check('판독 칸(read)마다 원문 인용', not missing, str(missing))
    check('판독 칸 인용이 문서에 글자 그대로', not wrong, str(wrong))


def main():
    for t in (test_spec, test_age, test_period_month, test_reason, test_covers, test_same_and_block, test_review_1015,
              test_model_fail,
              test_messages, test_prompt, test_data_driven, test_no_hardcode, test_quotes):
        try:
            t()
        except Exception as e:                  # 한 묶음이 죽어도 나머지는 돈다
            check(f'{t.__name__} 예외', False, repr(e))
    fail = [(n, w) for n, ok, w in results if not ok]
    print(f'{len(results) - len(fail)}/{len(results)} 통과')
    for n, w in fail:
        print(f'  [실패] {n} — {w}')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
