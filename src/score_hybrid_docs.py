# -*- coding: utf-8 -*-
"""
④ 찾기 — 임베딩 효과 소규모 검증 (제도문서 283조각만).

전체 6,803조각 임베딩은 분당 60회 한도로 113분이 걸린다.
리랭커·하위질의 분해처럼 재보니 떨어지는 경우가 또 나오면 그 시간이 통째로 낭비다.
그래서 제도문서만 먼저 임베딩해 효과를 확인하고 확대 여부를 정한다.

검색 대상을 제도문서 283조각으로 한정하고,
정답 근거가 제도문서에 있는 문항만 평가한다. (조건을 맞춰야 비교가 성립한다)
"""
import os, sys, json, io, re, time
import numpy as np
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import clova
from answer import get_index, CACHE, _fingerprint
from index_search import BM25, tokenize
from expand import expand
from hybrid import hyde, RRF_K

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


def main():
    fp = _fingerprint()
    emb_all = np.load(os.path.join(CACHE, f'emb_{fp}_docs.npy'))
    keep = json.load(open(os.path.join(CACHE, f'emb_{fp}_docs.idx.json')))
    idx, chunks = get_index()

    sub = [chunks[i] for i in keep]
    emb = emb_all[keep]
    # 정규화 (build 단계에서 전체를 정규화했지만 부분 배열이므로 다시 확인)
    nn = np.linalg.norm(emb, axis=1, keepdims=True)
    nn[nn == 0] = 1
    emb = emb / nn
    bm = BM25([tokenize(c.get('index_text') or c['text']) for c in sub])
    by_file = {}
    for c in sub:
        by_file.setdefault(c['file'], []).append(c)
    print(f'검색 대상: 제도문서 {len(sub)}조각 / 임베딩 {emb.shape}')

    items = [i for i in json.load(io.open(EVAL, encoding='utf-8'))['items']
             if i.get('evidence_file', '').startswith('doc')
             and i.get('trap') != '프롬프트공격']
    golds = {}
    for it in items:
        sn = norm(it.get('evidence_span', ''))
        g = set()
        for c in by_file.get(it['evidence_file'], []):
            if sn and sn in norm(c['text']):
                g.add(id(c))
        for f2, p2 in evidence_locs(it):
            if f2.startswith('KR'):
                continue
            for c in by_file.get(f2, []):
                if p2 is None or c['page'] == p2:
                    g.add(id(c))
        if g:
            golds[it['id']] = g
    items = [it for it in items if it['id'] in golds]
    n = len(items)
    print(f'평가 문항: {n}개 (근거가 제도문서에 있는 것)\n')

    def qvec(text):
        v = np.asarray(clova.embed(text), dtype=np.float32)
        m = np.linalg.norm(v)
        return v / (m if m else 1)

    print('질의 임베딩...')
    with ThreadPoolExecutor(max_workers=2) as ex:
        plain = dict(zip([i['id'] for i in items], ex.map(qvec, [i['question'] for i in items])))
        expd = dict(zip([i['id'] for i in items],
                        ex.map(qvec, [expand(i['question'])[0] for i in items])))

    print('HyDE 가상답변...')
    t0 = time.time()
    hy = {}
    for k, it in enumerate(items, 1):
        hy[it['id']] = hyde(it['question'])
        if k % 5 == 0:
            print(f'   {k}/{n} ({time.time()-t0:.0f}s)', flush=True)
    with ThreadPoolExecutor(max_workers=2) as ex:
        hyv = dict(zip([i['id'] for i in items], ex.map(qvec, [hy[i['id']] for i in items])))

    def bm_rank(q, k=60):
        return [i for i, _ in bm.search(q, k)]

    def dn_rank(v, k=60):
        s = emb @ v
        top = np.argpartition(-s, min(k, len(s) - 1))[:k]
        return list(top[np.argsort(-s[top])])

    def rrf(*rankings_weights):
        sc = {}
        for ranks, w in rankings_weights:
            for r, i in enumerate(ranks):
                sc[i] = sc.get(i, 0) + w / (RRF_K + r + 1)
        return [i for i, _ in sorted(sc.items(), key=lambda x: -x[1])]

    results = []

    def run(label, fn):
        hit = {k: 0 for k in KS}
        full = {k: 0 for k in KS}
        for it in items:
            order = fn(it)[:max(KS)]
            got = [id(sub[i]) for i in order]
            gold = golds[it['id']]
            for k in KS:
                top = set(got[:k])
                if top & gold:
                    hit[k] += 1
                if gold.issubset(top):
                    full[k] += 1
        line = "  ".join(f"R@{k} {hit[k]/n*100:5.1f}" for k in KS)
        print(f'  {label:24} {line}   전체@10 {full[10]/n*100:5.1f}')
        results.append({'label': label, **{f'R@{k}': round(hit[k]/n*100, 1) for k in KS},
                        'full@10': round(full[10]/n*100, 1)})

    print('\n' + '=' * 92)
    run('BM25 (확장) 현행', lambda it: bm_rank(expand(it['question'])[0]))
    run('임베딩 (원문)', lambda it: dn_rank(plain[it['id']]))
    run('임베딩 (확장)', lambda it: dn_rank(expd[it['id']]))
    run('RRF 1:1', lambda it: rrf((bm_rank(expand(it['question'])[0]), 1.0),
                                  (dn_rank(expd[it['id']]), 1.0)))
    run('RRF 2:1 (BM25↑)', lambda it: rrf((bm_rank(expand(it['question'])[0]), 2.0),
                                          (dn_rank(expd[it['id']]), 1.0)))
    run('RRF 1:2 (임베딩↑)', lambda it: rrf((bm_rank(expand(it['question'])[0]), 1.0),
                                          (dn_rank(expd[it['id']]), 2.0)))
    run('HyDE 단독', lambda it: dn_rank(hyv[it['id']]))
    run('RRF + HyDE', lambda it: rrf((bm_rank(expand(it['question'])[0]), 1.0),
                                     (dn_rank(hyv[it['id']]), 1.0)))
    run('RRF + 확장 + HyDE', lambda it: rrf((bm_rank(expand(it['question'])[0]), 1.0),
                                           (dn_rank(expd[it['id']]), 1.0),
                                           (dn_rank(hyv[it['id']]), 1.0)))
    print('=' * 92)

    base = results[0]
    best = max(results, key=lambda r: r['R@10'])
    print(f"\n기준선 BM25  R@10 {base['R@10']}  전체@10 {base['full@10']}")
    print(f"최고 {best['label']}  R@10 {best['R@10']}  전체@10 {best['full@10']}")
    print(f"→ R@10 {best['R@10'] - base['R@10']:+.1f}p")
    json.dump(results, io.open(os.path.join(ROOT, 'eval', '_hybrid_docs.json'), 'w',
                               encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
