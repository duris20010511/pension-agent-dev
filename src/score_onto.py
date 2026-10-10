# -*- coding: utf-8 -*-
"""온톨로지 순위 조정(onto_link.rerank) 켜고 끈 검색 정답률 짝비교 — score_hybrid.py 와 같은 정답 근거·같은 하이브리드(1:3).

운영 경로와 같게: 하이브리드 후보 30개 → 문서 정책 필터 → (켬) 온톨로지 순위 조정 → 상위 20개로 R@k.
결과는 eval/_onto_retrieval.json.

  python score_onto.py
"""
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import clova
import doc_policy
import onto_link
from answer import get_index, CACHE, _fingerprint
from expand import expand
from hybrid import Hybrid
from score_hybrid import EVAL, ROOT, KS, gold_of, report


def main():
    items = [i for i in json.load(io.open(EVAL, encoding='utf-8'))['items']
             if i.get('evidence_file') != '(해당없음)' and i.get('trap') != '프롬프트공격']
    idx, chunks = get_index()
    by_file = {}
    for c in chunks:
        by_file.setdefault(c['file'], []).append(c)
    H = Hybrid(chunks, idx.bm25, os.path.join(CACHE, f'emb_{_fingerprint()}.npy'))
    golds = {it['id']: gold_of(it, by_file) for it in items}
    items = [it for it in items if golds[it['id']]]
    n = len(items)
    print(f'정답 근거가 있는 문항 {n}개 · 조각 꼬리표 {len(onto_link.chunk_table())}개')

    def qv(text):
        v = np.asarray(clova.embed(text), dtype=np.float32)
        nn = np.linalg.norm(v)
        return v / (nn if nn else 1)
    with ThreadPoolExecutor(max_workers=2) as ex:
        expd = dict(zip([i['id'] for i in items], ex.map(qv, [expand(i['question'])[0] for i in items])))

    def base(it):
        hits = H.search(expand(it['question'])[0], 30, w_bm=1.0, w_dense=3.0, qvec=expd[it['id']])
        return [h for h in hits if doc_policy.allowed(h[0], it['question'])]

    results, per = [], {}
    for label, on in (('하이브리드 1:3 (지금)', False), ('하이브리드 1:3 + 온톨로지', True)):
        rows = []
        for it in items:
            hits = base(it)
            if on:
                hits, note = onto_link.rerank(hits, it['question'])
                per.setdefault(it['id'], {})['note'] = note
            got = [id(c) for c, _ in hits[:20]]
            rows.append((got, golds[it['id']]))
            g = golds[it['id']]
            first = next((k for k, x in enumerate(got, 1) if x in g), None)
            per.setdefault(it['id'], {})['on' if on else 'off'] = first
        results.append(report(label, rows, n))
    active = [q for q, v in per.items() if v.get('note')]
    better = [q for q, v in per.items() if v.get('on') and (v.get('off') is None or v['on'] < v['off'])]
    worse = [q for q, v in per.items() if v.get('off') and (v.get('on') is None or v['on'] > v['off'])]
    print(f'\n순위 조정이 작동한 문항 {len(active)}개 · 첫 정답 근거가 앞당겨짐 {len(better)} · 밀림 {len(worse)}')
    print('  앞당겨짐:', ', '.join(f"{q}({per[q]['off']}→{per[q]['on']})" for q in better[:15]))
    print('  밀림:', ', '.join(f"{q}({per[q]['off']}→{per[q]['on']})" for q in worse[:15]))
    json.dump({'results': results, 'per': per, 'n': n},
              io.open(os.path.join(ROOT, 'eval', '_onto_retrieval.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
