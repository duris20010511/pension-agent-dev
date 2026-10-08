# -*- coding: utf-8 -*-
"""④ 찾기 — BM25 / 임베딩 / 하이브리드(RRF) / HyDE / Small-to-Big 비교."""
import os, sys, json, io, re, time
import numpy as np
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import clova
from answer import get_index, CACHE, _fingerprint
from index_search import BM25, tokenize
from expand import expand
from hybrid import Hybrid, hyde

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(ROOT, 'eval', 'evalset.json')
KS = (1, 3, 5, 10, 20)


def norm(s):
    return re.sub(r'\s+', '', s or '')


def evidence_locs(it):
    locs = [(it.get('evidence_file', ''), it.get('evidence_page') or None)]
    for part in re.split(r'[,\n]', it.get('extra_evidence', '') or ''):
        m = re.match(r'(\S+?\.txt)\s*[:：]\s*(\d+)', part.strip())
        if m:
            locs.append((m.group(1), int(m.group(2))))
        elif part.strip().endswith('.txt'):
            locs.append((part.strip(), None))
    return locs


def gold_of(it, by_file):
    sn = norm(it.get('evidence_span', ''))
    g = set()
    for c in by_file.get(it.get('evidence_file', ''), []):
        if sn and sn in norm(c['text']):
            g.add(id(c))
    for f2, p2 in evidence_locs(it):
        for c in by_file.get(f2, []):
            if p2 is None or c['page'] == p2:
                g.add(id(c))
    return g


def report(label, per_item_hits, n):
    hit = {k: 0 for k in KS}
    full = {k: 0 for k in KS}
    for got, gold in per_item_hits:
        for k in KS:
            top = set(got[:k])
            if top & gold:
                hit[k] += 1
            if gold.issubset(top):
                full[k] += 1
    line = "  ".join(f"R@{k} {hit[k]/n*100:5.1f}" for k in KS)
    print(f"  {label:26} {line}   전체@10 {full[10]/n*100:5.1f}  전체@20 {full[20]/n*100:5.1f}")
    return {'label': label, **{f'R@{k}': round(hit[k]/n*100, 1) for k in KS},
            'full@10': round(full[10]/n*100, 1), 'full@20': round(full[20]/n*100, 1)}


def main():
    items = [i for i in json.load(io.open(EVAL, encoding='utf-8'))['items']
             if i.get('evidence_file') != '(해당없음)' and i.get('trap') != '프롬프트공격']
    idx, chunks = get_index()
    by_file = {}
    for c in chunks:
        by_file.setdefault(c['file'], []).append(c)

    emb_path = os.path.join(CACHE, f'emb_{_fingerprint()}.npy')
    if not os.path.exists(emb_path):
        print(f'임베딩 없음: {emb_path}\n먼저 build_embeddings.py 를 돌리십시오.')
        return
    H = Hybrid(chunks, idx.bm25, emb_path)
    print(f"조각 {len(chunks):,} · 임베딩 {H.emb.shape}\n")

    golds = {it['id']: gold_of(it, by_file) for it in items}
    items = [it for it in items if golds[it['id']]]
    n = len(items)

    # 질의 임베딩 미리 계산 (병렬)
    print("질의 임베딩 계산 중...")
    def qv(text):
        v = np.asarray(clova.embed(text), dtype=np.float32)
        nn = np.linalg.norm(v)
        return v / (nn if nn else 1)
    with ThreadPoolExecutor(max_workers=2) as ex:
        plain = dict(zip([i['id'] for i in items],
                         ex.map(qv, [i['question'] for i in items])))
        expd = dict(zip([i['id'] for i in items],
                        ex.map(qv, [expand(i['question'])[0] for i in items])))

    # HyDE 는 제외한다. docs 283조각 검증에서 R@10 은 동급인데
    # 전체근거는 오히려 63.3 → 60.0 으로 떨어졌고 질의당 LLM 호출이 1회 더 든다.
    # 분당 60회 한도에서 호출 하나는 비싸다.

    print()
    print("=" * 96)
    results = []

    def run(label, fn):
        per = []
        for it in items:
            got = [id(c) for c, _ in fn(it)]
            per.append((got, golds[it['id']]))
        results.append(report(label, per, n))

    run('BM25 단독(확장O) 현행', lambda it: H.search(expand(it['question'])[0], 20, w_dense=0))
    run('임베딩 단독(원문)', lambda it: H.search(it['question'], 20, w_bm=0, qvec=plain[it['id']]))
    run('임베딩 단독(확장)', lambda it: H.search(it['question'], 20, w_bm=0, qvec=expd[it['id']]))
    run('하이브리드 RRF 1:1', lambda it: H.search(expand(it['question'])[0], 20, qvec=expd[it['id']]))
    run('하이브리드 RRF 2:1(BM25↑)', lambda it: H.search(expand(it['question'])[0], 20,
                                                      w_bm=2.0, qvec=expd[it['id']]))
    run('하이브리드 RRF 1:2(임베딩↑)', lambda it: H.search(expand(it['question'])[0], 20,
                                                      w_bm=1.0, w_dense=2.0, qvec=expd[it['id']]))
    run('하이브리드 RRF 1:3', lambda it: H.search(expand(it['question'])[0], 20,
                                                w_bm=1.0, w_dense=3.0, qvec=expd[it['id']]))

    print("=" * 96)
    json.dump(results, io.open(os.path.join(ROOT, 'eval', '_hybrid_score.json'), 'w',
                               encoding='utf-8'), ensure_ascii=False, indent=1)
    best = max(results, key=lambda r: r['R@10'])
    print(f"\nR@10 최고: {best['label']} — {best['R@10']}")
    bestf = max(results, key=lambda r: r['full@10'])
    print(f"전체근거@10 최고: {bestf['label']} — {bestf['full@10']}")


if __name__ == '__main__':
    main()
