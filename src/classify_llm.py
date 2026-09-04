# -*- coding: utf-8 -*-
"""
③ 유형 분류 — LLM 전용.

앞서 understand_llm.analyze() 로 분류·역질문판정·하위질의분해를 한 번에 시켰을 때
분류 정확도가 50.0% 였다. 한 호출에 네 가지를 시키느라 프롬프트가 복잡했다.
여기서는 **분류만** 시킨다. 짧은 프롬프트 + 유형 정의 + 판단 순서.

호출이 질의당 1회 늘어난다 (분당 60회 한도에서 비용이 있다).
규칙 분류(45.0%)보다 얼마나 나은지 재보고 결정한다.
"""
import re
import clova

PROMPT = """연금 상담 질의를 7개 유형 중 하나로 분류하십시오.

제도  : 퇴직연금 제도의 규칙 자체 — 가입대상·규약·제도변경·급여산정·압류·지연이자·가입자교육
세제  : 세금 — 세액공제·연금소득세·종합과세·수령한도·퇴직소득세·과세이연
절차  : 어떻게 하는지 — 신청 방법·필요 서류·기한·처리 절차·해지/이체/인출 방법
상품설명: 특정 펀드 하나의 속성 — 위험등급·총보수·클래스·투자대상·수익률
상품비교: 둘 이상의 상품을 견줌 — 뭐가 다른지, 어느 쪽이 나은지
조건부추천: 상품을 골라달라는 요청 — 추천·"뭐 살까"·"하나만 찍어줘"
종합  : 여러 제도가 얽혀 하나로 분류되지 않음

판단 순서:
1. 상품을 골라달라고 하면 → 조건부추천 (주제가 세금이어도)
2. 두 개 이상을 견주면 → 상품비교
3. "어떻게 하는지"를 물으면 → 절차 (주제가 제도여도)
4. 세금·한도·세율이 핵심이면 → 세제
5. 특정 펀드의 속성을 물으면 → 상품설명
6. 제도 규칙 자체를 물으면 → 제도
7. 둘 이상 제도가 얽혀 하나로 못 고르면 → 종합

유형 이름 하나만 출력하십시오. 다른 말 금지.

질의: {q}"""

VALID = {'제도', '세제', '절차', '상품설명', '상품비교', '조건부추천', '종합'}
_cache = {}


def classify(question):
    if question in _cache:
        return _cache[question]
    try:
        prev = clova.CHAT_MODEL
        clova.CHAT_MODEL = 'HCX-005'          # 분류에 추론 모델은 과하고 느리다
        try:
            out = clova.chat([{'role': 'user', 'content': PROMPT.format(q=question)}],
                             temperature=0.0, max_tokens=200)
        finally:
            clova.CHAT_MODEL = prev
        for v in VALID:                        # 응답 안에서 유형명을 찾는다
            if v in out:
                _cache[question] = v
                return v
    except Exception:
        pass
    from classify2 import classify as rule
    r = rule(question)
    _cache[question] = r
    return r
