# -*- coding: utf-8 -*-
"""금액 읽기 이중 확인(crosscheck.py) 시험 — 모델을 부르지 않는다(모델 출력은 손으로 만든 JSON)."""
import sys

import answer
import crosscheck
import taxcalc


def parsed(q):
    return taxcalc.parse(taxcalc.normalize(taxcalc.norm_ws(q)))


def main():
    fail, n = [], 0

    def ok(cond, msg):
        nonlocal n
        n += 1
        if not cond:
            fail.append(msg)

    q = '연금저축 연 1,000만 원 받는 연말정산 어떻게 해요? 65세'
    c = taxcalc.compute(q)
    if c:   # 정규식이 이 질문을 고쳐 읽게 되면(파서 수정) 이 시험은 건너뛴다
        p = c['parsed']
        role = [s['role'] for s in p['spans'] if abs(s['v'] - 1000) < 0.6]
        if role and role[0] == 'contrib':
            ext = {'amounts': [{'text': '연 1,000만 원', 'man': 10000000, 'use': '연금수령', 'period': '1년', 'whose': '본인'}], 'age': 65}
            d = crosscheck.compare(p, ext)
            ok(d and d[0][1] == '납입' and d[0][2] == '연금수령', f'쓰임 다름을 못 잡음 {d}')
            ok('연금으로 받으면' in crosscheck.message(d, p), '수령 질문에 수령 예시가 아님')

    p = parsed('총급여 5,000만 원이고 연금저축 600 넣었어요. 12월에 IRP 300 더 넣으면 얼마나 돌려받아요?')
    same = {'amounts': [{'text': '총급여 5,000만 원', 'man': 5000, 'use': '소득'}, {'text': '연금저축 600', 'man': 600, 'use': '납입', 'period': '1년'},
                        {'text': 'IRP 300', 'man': 300, 'use': '납입'}]}
    ok(crosscheck.compare(p, same) == [], '같은 읽기인데 다르다고 함')
    topic = {'amounts': [{'text': '600', 'man': 600, 'use': '세액공제'}, {'text': '300', 'man': 300, 'use': '소득공제'}]}
    ok(crosscheck.compare(p, topic) == [], '모델이 질문 주제(세액공제)를 쓰임 칸에 적은 것을 다르다고 봄')
    unknown = {'amounts': [{'text': '600', 'man': 600, 'use': '모름'}]}
    ok(crosscheck.compare(p, unknown) == [], "'모름'을 다르다고 봄")
    listy = {'amounts': [{'text': '600', 'man': 600, 'use': ['납입'], 'period': ['1년']}]}
    ok(crosscheck.compare(p, listy) == [], '칸에 목록이 와도 같은 읽기로 봐야 함')
    won = {'amounts': [{'text': '300만 원', 'man': 3000000, 'use': '퇴직금'}]}
    p2 = parsed('총급여 5천만 원인데 IRP에 300만 원 넣으면 세액공제 얼마예요?')
    d = crosscheck.compare(p2, won)
    ok(d and d[0][2] == '퇴직금', f'원 단위로 적은 금액(3000000)을 원문 표현으로 맞추지 못함 {d}')
    ok('제 돈' in crosscheck.message(d, p2), '퇴직금↔납입 확인에 맞는 예시가 아님')
    spurious = {'amounts': [{'text': '총급여 5천만 원', 'man': 50000, 'use': '소득'}]}
    p3 = parsed('세액공제 얼마예요? 총급여 5000만원에 연금저축 월 5만원 납입하면요')
    ok(crosscheck.compare(p3, spurious) == [], '총급여(만 원 단위 바꿔 보기)를 월 5만 원과 엉뚱하게 맞춤')

    p4 = parsed('만 62세예요. IRP 평가액 1억 원이고 올해 1,800만 원 빼면 세금이 얼마예요?')
    compat = {'amounts': [{'text': '1,800만 원', 'man': 1800, 'use': '연금수령'}]}
    ok(crosscheck.compare(p4, compat) == [], '인출 ↔ 연금수령은 같은 꼴로 봐야 함(55세 이후 빼는 돈)')
    age = {'amounts': [], 'age': 52}
    d = crosscheck.compare(p4, age)
    ok(d and d[0][3] == 'age', f'나이 다름을 못 잡음 {d}')

    msg = crosscheck.message([(1000.0, '납입', '연금수령', '쓰임')], parsed('연금저축 연 1,000만 원 받는 연말정산 어떻게 해요? 65세'))
    ok(answer._XC_ASKED in msg, '확인 질문 첫 줄과 되묻기 반복 막기 표시가 어긋남(answer._XC_ASKED)')
    ok(crosscheck.check('연금저축이 뭐예요?', taxcalc.compute('연금저축이 뭐예요?')) is None, '계산 숫자 없는 질문에 확인을 돌림')
    ok(crosscheck.check('x', None) is None, 'calc 없음')

    print(f'{n - len(fail)}/{n} 통과')
    for f in fail:
        print('  ' + f)
    return 1 if fail else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
