# -*- coding: utf-8 -*-
"""
④ 찾기 — BM25 + 임베딩 하이브리드.

결합은 RRF(Reciprocal Rank Fusion)로 한다.
  score(d) = Σ 1 / (k + rank_i(d))

점수를 그냥 더하지 않는 이유: BM25 점수(0~100대)와 코사인 유사도(0~1)는
스케일이 달라 한쪽이 압도한다. RRF 는 점수 대신 순위만 쓰므로 스케일 문제가 없고
가중치 튜닝이 거의 필요 없다.

앞서 하위질의 분해를 라운드로빈으로 대충 합쳤다가 R@10 을 75.0 → 55.0 으로
20%p 날린 적이 있다. 합치는 방법 자체가 성능을 좌우한다.
"""
import os
import threading
from collections import OrderedDict
import numpy as np

import clova
from index_search import tokenize

RRF_K = 60          # 표준값. 상위 순위 간 격차를 완만하게 만든다.

# 질의 임베딩 캐시 — 같은 글이면 API 를 다시 부르지 않는다. 임베딩 API 는 같은 글에 매번 똑같은 벡터를 준다
# (10-15 실측: 3회 최대 차이 0.0)라 검색 결과가 바뀌지 않는다. 시연 리허설처럼 같은 질문이 되풀이될 때 호출(약 0.2초)이 빠진다.
QVEC_CACHE = int(os.environ.get('AGENT_QVEC_CACHE', '512'))
_QVEC = OrderedDict()
_QVEC_LOCK = threading.Lock()


def query_vec(query):
    """질의 → 정규화한 임베딩 벡터(float32). 캐시를 먼저 본다."""
    with _QVEC_LOCK:
        v = _QVEC.get(query)
        if v is not None:
            _QVEC.move_to_end(query)
            return v
    v = np.asarray(clova.embed(query), dtype=np.float32)
    n = np.linalg.norm(v)
    v = v / (n if n else 1)
    v.setflags(write=False)        # 캐시에 든 벡터를 호출부가 고치지 못하게
    if QVEC_CACHE > 0:
        with _QVEC_LOCK:
            _QVEC[query] = v
            while len(_QVEC) > QVEC_CACHE:
                _QVEC.popitem(last=False)
    return v


class Hybrid:
    def __init__(self, chunks, bm25, emb_path):
        self.chunks = chunks
        self.bm25 = bm25
        self.emb = np.load(emb_path) if os.path.exists(emb_path) else None
        if self.emb is not None and len(self.emb) != len(chunks):
            raise ValueError(f'임베딩 {len(self.emb)}개 ≠ 조각 {len(chunks)}개')

    # ---------------------------------------------------------- 단일 경로
    def bm25_rank(self, query, topk=50):
        return [i for i, _s in self.bm25.search(query, topk)]

    def dense_rank(self, query, topk=50, qvec=None):
        if self.emb is None:
            return []
        if qvec is None:
            qvec = query_vec(query)
        sims = self.emb @ qvec
        idx = np.argpartition(-sims, min(topk, len(sims) - 1))[:topk]
        return list(idx[np.argsort(-sims[idx])])

    # ---------------------------------------------------------- 결합
    def search(self, query, topk=10, pool=50, w_bm=1.0, w_dense=1.0,
               dense_query=None, qvec=None):
        """dense_query 를 주면 임베딩 검색만 그 문장으로 한다 (HyDE 용)."""
        scores = {}
        if w_bm:
            for r, i in enumerate(self.bm25_rank(query, pool)):
                scores[i] = scores.get(i, 0) + w_bm / (RRF_K + r + 1)
        if w_dense and self.emb is not None:
            dq = dense_query if dense_query is not None else query
            for r, i in enumerate(self.dense_rank(dq, pool, qvec=qvec)):
                scores[i] = scores.get(i, 0) + w_dense / (RRF_K + r + 1)
        order = sorted(scores.items(), key=lambda x: -x[1])[:topk]
        return [(self.chunks[i], s) for i, s in order]

    # ------------------------------------------------- 형제 조각 (같은 문서 안)
    def file_index(self):
        """파일명 → 그 파일의 조각 목록. 한 번만 만들어 둔다."""
        if getattr(self, '_by_file', None) is None:
            d = {}
            for n, c in enumerate(self.chunks):
                d.setdefault(c['file'], []).append((n, c))
            self._by_file = d
        return self._by_file

    def rank_within(self, query, file, exclude=(), topk=5, qvec=None):
        """한 문서 안에서만 질의와 가까운 조각을 고른다.

        전체 순위에서는 밀렸지만 그 문서 안에서는 상위인 조각을 건지는 용도다.
        검색 자체는 임베딩만 쓴다 — 후보가 한 문서 안(보통 수십 개)이라
        BM25 순위를 다시 매길 이득이 없고, 임베딩은 이미 계산돼 있다.
        """
        if self.emb is None:
            return []
        cand = [(n, c) for n, c in self.file_index().get(file, []) if n not in exclude]
        if not cand:
            return []
        if qvec is None:
            qvec = query_vec(query)
        sims = self.emb[[n for n, _ in cand]] @ qvec
        order = np.argsort(-sims)[:topk]
        return [cand[i] for i in order]


HYDE_PROMPT = """아래 질문에 대한 답변을 3~4문장으로 써 주십시오.
연금 상담 문서에 실제로 적혀 있을 법한 문장으로, 관련 법정 용어·제도 명칭·수치 형식을 포함해서 씁니다.
정확할 필요는 없습니다. 검색용이므로 문서에 나올 만한 표현을 쓰는 것이 중요합니다.
설명이나 인사 없이 답변 본문만 출력하십시오.

질문: {q}"""


def hyde(question):
    """질의로 가상 답변을 만들어 그걸로 임베딩 검색한다.
    고객 표현("만 72세")과 문서 표현("70세 이상 80세 미만")의 격차를 줄이는 게 목적."""
    try:
        return clova.chat([{'role': 'user', 'content': HYDE_PROMPT.format(q=question)}],
                          temperature=0.3, max_tokens=350)
    except Exception:
        return question
