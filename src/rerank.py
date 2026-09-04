# -*- coding: utf-8 -*-
"""
④ 찾기 보강 — CLOVA 리랭커.

이름은 리랭커지만 실제 동작은 '근거 인용 + 답변 초안'이다. 실측 응답:
  result          : "답변: ... <doc3>연 900만원까지 세액공제</doc3> ..."
  citedDocuments  : [{id, doc}]   ← 실제로 쓴 문서만 남는다
  suggestedQueries: []

우리에게 중요한 건 citedDocuments 다. 무관한 문서를 스스로 배제해 주므로
평가지표의 "근거 완전성 — 질의 대상과 무관한 근거를 배제했는가"에 직접 대응한다.

측정에서 나온 약점이 바로 이것이다:
  대상혼동 함정 retrieved 1/2  (엉뚱한 문서가 섞여 오답 유발)
  예) "연금저축+IRP 세액공제 얼마?" → ISA 전환 문서가 섞여 900만원을 1,200만원으로 답함
"""
import re
import clova

MAX_DOCS = 20          # 리랭커에 한 번에 넣을 문서 수
MAX_DOC_CHARS = 1800   # 문서 하나가 너무 길면 잘라 넣는다


def rerank(query, hits, topk=6):
    """(chunk, score) 목록 → 리랭커가 실제로 인용한 것만 추려 반환.

    반환: (선별된 hits, 리랭커 답변초안 or '', 실패사유 or '')
    실패하면 원본 hits 를 그대로 돌려준다 — 리랭커가 죽어도 파이프라인은 살아야 한다.
    """
    if not hits:
        return hits, '', 'no hits'

    use = hits[:MAX_DOCS]
    docs, by_id = [], {}
    for i, (c, _s) in enumerate(use, 1):
        did = f'd{i}'
        by_id[did] = (c, _s)
        body = c['text']
        if len(body) > MAX_DOC_CHARS:
            body = body[:MAX_DOC_CHARS]
        title = c.get('title') or ''
        head = f"[{c['source']}]" + (f" {title}" if title else '')
        docs.append({'id': did, 'doc': f'{head}\n{body}'})

    try:
        out = clova._post(f'{clova.BASE}/v1/api-tools/reranker',
                          {'query': query, 'documents': docs}, timeout=120)
    except Exception as e:
        return hits[:topk], '', f'{type(e).__name__}: {e}'

    res = out.get('result') or {}
    draft = res.get('result', '') or ''
    cited = res.get('citedDocuments') or []

    picked = []
    for cd in cited:
        did = cd.get('id')
        if did in by_id:
            picked.append(by_id[did])

    if not picked:
        # 인용이 하나도 없으면 근거 부족 신호다. 원본 상위를 그대로 쓴다.
        return hits[:topk], draft, 'no citation'

    # 인용된 것만 쓴다. topk 를 채우려고 무관 문서를 덧붙이지 않는다.
    #
    # 처음엔 모자라면 원래 순위로 채웠는데, 그게 바로 오답의 원인이었다.
    # "연금저축+IRP 세액공제" 질의에서 리랭커는 doc41 하나만 인용했는데
    # 내가 ISA 전환 문서 4개를 도로 채워 넣어 900만원이 1,200만원이 됐다.
    # 근거는 많이 넣는 게 아니라 정확히 넣는 것이다 — 평가지표도
    # "질의 대상과 무관한 근거를 배제했는가"를 본다.
    return picked[:topk], draft, ''


def cited_ratio(query, hits):
    """리랭커가 상위 몇 개를 실제로 인용했는지 — 무관 문서 배제 정도를 본다."""
    picked, draft, err = rerank(query, hits, topk=len(hits))
    if err:
        return None
    return len(picked) / max(1, len(hits))
