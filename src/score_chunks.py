# -*- coding: utf-8 -*-
"""
자르기·검색 설정이 '정답 문장을 몇 글자로 가져오는가'를 잰다. 생성·채점 없음(결정적).

    정답문장 회수율 : 평가셋 evidence_span(파일에서 글자 그대로 복사한 정답 근거)이
                     검색해 온 조각 안에 통째로 들어 있는 문항 비율
    정답파일 회수율 : 정답 근거 파일이 검색 결과에 들어 있는 비율(문항 평균)
    근거 글자 수    : 모델에 넣는 조각 글자 수 합(문항 평균). 토큰 ≈ 글자 × 0.54

왜 재는가 — 질문마다 조각 8개(약 13,600자 ≈ 8천 토큰)를 넣는데 답변이 인용하는 건 평균 2.8개였다.
CLOVA 테스트 키는 '입력 + 출력 상한'을 분당 6만 토큰에서 빼므로, 같은 정답을 더 적은 글자로
가져오면 그대로 처리량이 된다. 자르는 방식을 바꿀 때 이 표가 기준이다.

질의 임베딩은 .cache/qemb.json 에 저장해 재사용한다(설정을 바꿔 다시 재도 API 를 안 쓴다).

  python score_chunks.py            # 현재 색인
"""
import os
import re
import sys
import io
import json
import hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import numpy as np
import clova
import answer as A
import doc_policy
from expand import expand

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QCACHE = os.path.join(ROOT, '.cache', 'qemb.json')
KS = (3, 4, 5, 6, 8)


def norm(s):
    return re.sub(r'\s+', '', s or '')


def evidence_files(it):
    return set(re.findall(r'(doc\d+\.txt|KR[0-9A-Z]+\.txt)',
                          f"{it.get('evidence_file', '')} {it.get('extra_evidence', '')}"))


_qc = None


def qvec(text):
    global _qc
    if _qc is None:
        _qc = json.load(io.open(QCACHE, encoding='utf-8')) if os.path.exists(QCACHE) else {}
    k = hashlib.md5(text.encode('utf-8')).hexdigest()
    if k not in _qc:
        _qc[k] = clova.embed(text)
        json.dump(_qc, io.open(QCACHE, 'w', encoding='utf-8'))
    v = np.asarray(_qc[k], dtype=np.float32)
    return v / (np.linalg.norm(v) or 1)


_STOP = {'투자신탁', '집합투자기구', '수수료미징구', '이투자신탁', '투자자는', '있습니다', '합니다', '따라서'}


def key_terms(span):
    """정답 문장의 핵심어 — 숫자(단위 포함)와 3글자 이상 낱말."""
    nums = re.findall(r'\d+(?:[.,]\d+)*\s*(?:%|등급|년|개월|일)?', span)
    words = [w for w in re.findall(r'[가-힣A-Za-z]{3,}', span) if w not in _STOP]
    return {norm(t) for t in nums + words if len(norm(t)) >= 2}


def has_content(span, hits, need=0.6):
    """정답 문장의 핵심어 중 need 이상이 근거 안에 있는가. 요약 카드처럼 원문을 그대로 옮기지 않은
    근거도 공정하게 재려는 보조 지표다."""
    ks = key_terms(span)
    if not ks:
        return None
    ctx = norm(' '.join(c['text'] for c in hits))
    return sum(1 for k in ks if k in ctx) / len(ks) >= need


def has_span(span, hits):
    """정답 문장이 조각 하나에, 또는 같은 파일의 이어진 조각들을 이은 글에 통째로 있는가."""
    s = norm(span)
    if not s:
        return None
    by_file = {}
    for c in hits:
        if s in norm(c['text']):
            return True
        by_file.setdefault(c['file'], []).append(c)
    for cs in by_file.values():
        cs = sorted(cs, key=lambda c: c.get('idx', 0))
        if s in norm(''.join(c['text'] for c in cs)):
            return True
    return False


def evaluate(H, label, search=None, ks=KS, show=True):
    """H: Hybrid. search(eq, k, qv) 를 주면 그걸로 찾는다(조각 묶기 같은 후처리 실험용)."""
    items = json.load(io.open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
    wb = float(os.environ.get('AGENT_W_BM', '1.0'))
    wd = float(os.environ.get('AGENT_W_DENSE', '3.0'))
    rows = {k: {'span': [], 'file': [], 'chars': [], 'span_doc': [], 'span_kr': [], 'cont_kr': []} for k in ks}
    for it in items:
        q = it['question']
        eq, _ = expand(q)
        v = qvec(eq)
        ev = evidence_files(it)
        span = it.get('evidence_span')
        for k in ks:
            if search:
                hits = search(eq, k, v, q)
            else:                   # 운영 경로 그대로 (정책 필터, v3 면 펀드 카드 라우팅까지)
                hits = [c for c, _ in A.retrieve(eq, q, k, qvec=v)[0]]
            if span and ev:
                ok = bool(has_span(span, hits))
                rows[k]['span'].append(ok)
                kr = (it.get('evidence_file') or '').startswith('KR')
                rows[k]['span_kr' if kr else 'span_doc'].append(ok)
                if kr:
                    hc = has_content(span, hits)
                    if hc is not None:
                        rows[k]['cont_kr'].append(hc)
            if ev:
                rows[k]['file'].append(len({c['file'] for c in hits} & ev) / len(ev))
            rows[k]['chars'].append(sum(len(c['text']) for c in hits))
    out = {}
    if show:
        print(f'\n=== {label} ===')
        print(f"  {'조각 수':>6} {'정답문장':>8} {'(제도':>6} {'설명서)':>7} {'설명서내용':>9} {'정답파일':>8} {'근거 글자':>9} {'≈토큰':>7}")
    for k in ks:
        r = rows[k]
        sp = sum(r['span']) / len(r['span']) * 100
        fl = sum(r['file']) / len(r['file']) * 100
        ch = sum(r['chars']) / len(r['chars'])
        sd = sum(r['span_doc']) / max(1, len(r['span_doc'])) * 100
        sk = sum(r['span_kr']) / max(1, len(r['span_kr'])) * 100
        ck = sum(r['cont_kr']) / max(1, len(r['cont_kr'])) * 100
        out[k] = {'span': round(sp, 1), 'span_doc': round(sd, 1), 'span_kr': round(sk, 1),
                  'cont_kr': round(ck, 1), 'file': round(fl, 1), 'chars': round(ch)}
        if show:
            print(f'  {k:>6} {sp:7.1f}% {sd:5.1f}% {sk:6.1f}% {ck:8.1f}% {fl:7.1f}% {ch:9,.0f} {ch * 0.54:7,.0f}')
    return out


def rank_metrics(H, k, show=True):
    """순위 지표 — Recall@K·Precision@K·MRR·nDCG@K.

    관련도: 정답 문장이 든 조각(카드는 정답 핵심어 60% 이상) = 2, 정답 파일의 다른 조각 = 1, 그 밖 = 0.
    nDCG 의 이상적 순위는 색인 전체에서 그 문항의 관련 조각들로 만든다.
    """
    import math
    items = json.load(io.open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
    by_file = {}
    for c in H.chunks:
        by_file.setdefault(c['file'], []).append(c)

    def rel(c, ev, span):
        if c['file'] not in ev:
            return 0
        if span and (has_span(span, [c]) or (c.get('kind') == 'card' and has_content(span, [c]))):
            return 2
        return 1

    R = {'recall': [], 'prec': [], 'mrr': [], 'ndcg': []}
    for it in items:
        ev = evidence_files(it)
        if not ev:
            continue
        q = it['question']
        eq, _ = expand(q)
        hits = [c for c, _ in A.retrieve(eq, q, k, qvec=qvec(eq))[0]]
        span = it.get('evidence_span')
        rels = [rel(c, ev, span) for c in hits]
        R['recall'].append(len({c['file'] for c, r in zip(hits, rels) if r}) / len(ev))
        R['prec'].append(sum(1 for r in rels if r) / max(1, len(hits)))
        first = next((i for i, r in enumerate(rels) if r), None)
        R['mrr'].append(0 if first is None else 1 / (first + 1))
        dcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rels))
        pool = sorted((rel(c, ev, span) for f in ev for c in by_file.get(f, [])), reverse=True)[:k]
        idcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(pool))
        R['ndcg'].append(dcg / idcg if idcg else 0)
    out = {m: round(sum(v) / len(v), 3) for m, v in R.items()}
    if show:
        print(f"  K={k}: Recall@K {out['recall']:.3f} · Precision@K {out['prec']:.3f} · "
              f"MRR {out['mrr']:.3f} · nDCG@K {out['ndcg']:.3f}  ({len(R['mrr'])}문항)")
    return out


if __name__ == '__main__':
    H = A.get_hybrid()
    if H is None:
        sys.exit('임베딩 캐시가 없어 측정을 멈춥니다.')
    ks = KS if A.CHUNK_MODE == 'v1' else (4, 5, 6, 8, 10, 12)
    evaluate(H, f'색인 {A.CHUNK_MODE} ({len(H.chunks):,}조각, 하이브리드 1:3)', ks=ks)
    print('\n  순위 지표 (운영 근거 수 기준)')
    rank_metrics(H, A.DEFAULT_TOPK)
