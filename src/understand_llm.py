# -*- coding: utf-8 -*-
"""
③ 질의이해 — LLM(HyperCLOVA X) 판정.

규칙 기반(understand_guard.py)의 한계가 측정으로 드러났다:
  유형 분류 40.5 / 역질문 재현율 20.0

그리고 ④ 진단에서 나온 사실:
  gold 평균 2.5개, 24/40 문항이 2개 이상 파일에 걸쳐 있다.
  한 번의 검색으로는 여러 대상을 다 못 잡는다.
  예) "솔로몬 단기·중장기·장기 뭐가 달라요" → 펀드 3개를 각각 찾아야 한다.

그래서 이 모듈은 두 가지를 한 번의 LLM 호출로 처리한다.
  (가) 유형 분류 + 역질문 필요 판정   ← ③ 자체
  (나) 하위 질의 분해                ← ④ 전체근거 개선용

호출을 하나로 묶은 이유: 테스트 키가 429 에 민감하다. 질의당 LLM 호출 수를
늘리면 평가 중 실패 위험이 커진다. 분류와 분해는 같은 이해 과정이라 함께 시킨다.
"""
import json
import re
import clova

SYS = """당신은 연금 상담 질의를 분석하는 도구입니다. JSON만 출력합니다.

분석 항목:
1. category — 다음 중 하나
   제도 : DB/DC/IRP, 규약, 가입대상, 급여산정, 압류, 지연이자, 실물이전
   세제 : 세액공제, 연금소득세, 종합과세, 수령한도, 퇴직소득세, 과세이연
   상품설명 : 특정 펀드 하나의 구조·위험등급·보수
   상품비교 : 둘 이상의 상품을 견주는 질문
   조건부추천 : 상품을 골라달라는 요청
   절차 : 신청·해지·이체·인출 방법과 서류
   종합 : 여러 제도가 얽힌 복합 질문

2. need_clarification — 확정 답변에 필요한 조건이 빠졌으면 true
   판단 기준: 이 질문에 지금 정보만으로 "하나의 정확한 답"을 줄 수 있는가?
   - 상품 추천인데 계좌유형·투자기간·감내위험이 없다 → true
   - 금액 계산인데 필요한 수치(급여, 연령, 가입기간 등)가 없다 → true
   - 문서를 찾아 사실만 알려주면 되는 질문 → false
   무조건 true 로 하지 마십시오. 답할 수 있는 질문은 false 입니다.

3. ask — need_clarification 이 true 일 때 되물을 항목 2~4개. 아니면 빈 배열.

4. subqueries — 검색용 하위 질의 1~4개
   질문이 여러 대상을 다루면 대상별로 쪼갭니다.
   예) "솔로몬 단기·중장기·장기 뭐가 달라요"
       → ["솔로몬단기국공채 위험등급 총보수", "솔로몬중장기국공채 위험등급 총보수",
          "솔로몬장기국공채 위험등급 총보수"]
   예) "명퇴수당을 연금계좌에 넣으면 절세되나요"
       → ["명예퇴직수당 연금계좌 입금", "퇴직소득세 연금수령 감면"]
   대상이 하나면 1개만 만듭니다. 원문을 그대로 베끼지 말고 문서에 나올 법한
   용어(법정 용어, 제도 명칭)로 바꿔 씁니다.

5. false_premise — 고객이 틀린 사실을 전제로 깔았을 가능성이 있으면 true

출력 형식(다른 말 금지):
{"category":"세제","need_clarification":false,"ask":[],"subqueries":["..."],"false_premise":false}"""


# HCX-005 는 system 프롬프트의 "JSON 만 출력" 지시를 자주 무시하고 그냥 답해버린다.
# (실측: 42문항 중 22건이 JSON 대신 상담 답변을 내놨다. Structured Outputs 파라미터
#  response_format / outputSchema 는 400 을 피할 뿐 스키마를 강제하지 못한다.)
# 지시를 user 메시지로 옮기고 출력 예시를 한 줄 붙이면 안정적으로 JSON 이 나온다.
USER_TMPL = """{sys}

--- 분석할 질의 ---
{q}

위 질의를 분석해 JSON 한 개만 출력하십시오. 설명·인사·상담 답변 금지.
출력 예시(형식만 참고):
{{"category":"세제","need_clarification":false,"ask":[],"subqueries":["연금저축 세액공제 한도","IRP 세액공제 한도"],"false_premise":false}}"""


def analyze(question, timeout_fallback=True):
    """질의 분석. 실패하면 규칙 기반으로 떨어진다 — LLM 이 죽어도 서비스는 살아야 한다."""
    try:
        out = clova.chat(
            [{'role': 'user', 'content': USER_TMPL.format(sys=SYS, q=question)}],
            temperature=0.0, max_tokens=600)
        m = re.search(r'\{.*\}', out, re.S)
        if not m:
            raise ValueError('JSON 없음')
        d = json.loads(m.group(0))
        subs = [s for s in (d.get('subqueries') or []) if isinstance(s, str) and s.strip()]
        if not subs:
            subs = [question]
        return {
            'category': d.get('category') or '종합',
            'need_clarification': bool(d.get('need_clarification')),
            'ask': [a for a in (d.get('ask') or []) if isinstance(a, str)],
            'subqueries': subs[:4],
            'false_premise': bool(d.get('false_premise')),
            'source': 'llm',
        }
    except Exception as e:
        if not timeout_fallback:
            raise
        from understand_guard import classify, needs_clarification
        clar, what = needs_clarification(question)
        return {
            'category': classify(question),
            'need_clarification': clar,
            'ask': [what] if what else [],
            'subqueries': [question],
            'false_premise': False,
            'source': f'fallback ({type(e).__name__})',
        }


def multi_search(idx, subqueries, expand_fn, topk=10, per_query=6):
    """하위 질의별로 검색해 라운드로빈으로 합친다.

    라운드로빈인 이유: 점수 순으로 합치면 한 하위 질의가 상위를 독식한다.
    여러 대상을 골고루 담아야 '전체 근거'가 모인다.
    """
    lists = []
    for sq in subqueries:
        eq, _ = expand_fn(sq)
        lists.append(idx.search(eq, topk=per_query))

    merged, seen = [], set()
    for rank in range(per_query):
        for lst in lists:
            if rank >= len(lst):
                continue
            c, s = lst[rank]
            if id(c) in seen:
                continue
            seen.add(id(c))
            merged.append((c, s))
            if len(merged) >= topk:
                return merged
    return merged[:topk]
