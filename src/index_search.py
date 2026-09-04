# -*- coding: utf-8 -*-
"""
② 색인 + ④ 찾기.

한국어 형태소 분석기(konlpy/kiwi)가 환경에 없어서 BM25 토크나이저를 직접 만든다.
  - 어절 토큰 + 어절 내부 char 2-gram (조사 변화를 흡수)
  - 수치는 단위 포함/제외 두 형태로 넣는다 (표 셀에 단위가 없는 경우 대응)
  - 종목코드(KR...), 영문 약어(DB/DC/IRP/ETF/TDF)는 통째로 보존

임베딩은 아직 붙이지 않았다. 과제 규정상 LLM 은 HyperCLOVA X 만 허용이라
임베딩도 HyperCLOVA X 임베딩 API 로 붙이는 게 안전하고, 지금은 키가 없다.
그래서 이번 측정은 '키워드 단독' 점수다. 임베딩을 얹으면 여기서 얼마나 오르는지 보면 된다.
"""
import re
import math
from collections import Counter

CODE_RE = re.compile(r'\bKR[0-9A-Z]{8,12}\b')
ABBR_RE = re.compile(r'\b(?:DB|DC|IRP|ETF|ETN|TDF|MMF|ELB|DLB|ELS|DLS|GIC|RP|ISA|REITs?)\b', re.I)
NUM_UNIT_RE = re.compile(r'\d[\d,]*(?:\.\d+)?\s*(?:%|만원|원|시간|일분|일|개월|년|배|점|등급|호)')
BARE_DEC_RE = re.compile(r'\d+\.\d{2,}')
WORD_RE = re.compile(r'[가-힣]+|[A-Za-z]+|\d[\d,.]*')

# 검색에 도움 안 되는 흔한 조사·어미 꼬리
TAIL = ('입니다', '습니다', '합니다', '하는', '에서', '으로', '에게', '까지', '부터',
        '이나', '거나', '이며', '하고', '지만', '는데', '으며', '해서')


def tokenize(text):
    t = text or ''
    toks = []
    for m in CODE_RE.finditer(t):
        toks.append(m.group(0).upper())
    for m in ABBR_RE.finditer(t):
        toks.append(m.group(0).upper())
    for m in NUM_UNIT_RE.finditer(t):
        s = re.sub(r'\s+', '', m.group(0))
        toks.append(s)
        toks.append(re.sub(r'[^\d.]', '', s))
    for m in BARE_DEC_RE.finditer(t):
        toks.append(m.group(0))
    for m in WORD_RE.finditer(t):
        w = m.group(0)
        if len(w) < 2:
            continue
        toks.append(w)
        if re.match(r'^[가-힣]+$', w):
            for tail in TAIL:
                if len(w) > len(tail) + 1 and w.endswith(tail):
                    toks.append(w[:-len(tail)])
                    break
            for i in range(len(w) - 1):          # char 2-gram
                toks.append(w[i:i + 2])
    return [x for x in toks if x]


class BM25:
    """BM25 직접 구현. rank_bm25 를 쓰면 토큰 가중치를 못 넣어서 직접 만든다."""

    def __init__(self, docs_tokens, k1=1.4, b=0.72):
        self.k1, self.b = k1, b
        self.N = len(docs_tokens)
        self.tf = [Counter(t) for t in docs_tokens]
        self.len = [len(t) for t in docs_tokens]
        self.avg = sum(self.len) / self.N if self.N else 0
        df = Counter()
        for c in self.tf:
            df.update(c.keys())
        self.idf = {w: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for w, n in df.items()}
        self.inv = {}
        for i, c in enumerate(self.tf):
            for w in c:
                self.inv.setdefault(w, []).append(i)

    def search(self, query, topk=10, boost=None):
        qt = tokenize(query)
        boost = boost or {}
        scores = {}
        for w in set(qt):
            if w not in self.inv:
                continue
            idf = self.idf.get(w, 0)
            wt = boost.get(w, 1.0)
            # 2-gram 은 노이즈가 많아 가중치를 낮춘다
            if len(w) == 2 and re.match(r'^[가-힣]{2}$', w):
                wt *= 0.35
            for i in self.inv[w]:
                f = self.tf[i][w]
                denom = f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)
                scores[i] = scores.get(i, 0) + wt * idf * (f * (self.k1 + 1)) / denom
        return sorted(scores.items(), key=lambda x: -x[1])[:topk]


class Index:
    def __init__(self, chunks, field='index_text'):
        self.chunks = chunks
        self.bm25 = BM25([tokenize(c.get(field) or c['text']) for c in chunks])

    def search(self, query, topk=10, boost=None):
        return [(self.chunks[i], s) for i, s in self.bm25.search(query, topk, boost)]
