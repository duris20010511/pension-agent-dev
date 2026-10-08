# -*- coding: utf-8 -*-
"""
대상 독자 정책(doc_policy.py)이 정답 근거를 잃게 하지 않는지 — 검색 단계만 결정적으로 잰다.

정책 켜기/끄기를 같은 질의 임베딩으로 비교한다. 생성·채점이 없어 LLM 변동이 끼지 않는다.
  근거회수@k : 정답 근거 파일(evidence_file + extra_evidence) 중 상위 k 안에 든 비율
  전체근거@k : 정답 근거 파일이 전부 상위 k 안에 든 문항 비율
  정책 대상 조각 : 정책을 끄면 상위 k 에 들어오던 사무담당자 절차·공제회 조각 수
"""
import os
import re
import sys
import io
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import numpy as np
import clova
import answer as A
import doc_policy
from expand import expand

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K = A.DEFAULT_TOPK


def evidence_files(it):
    fs = set(re.findall(r'(doc\d+\.txt|KR[0-9A-Z]+\.txt)',
                        f"{it.get('evidence_file', '')} {it.get('extra_evidence', '')}"))
    return fs


def main():
    items = json.load(io.open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
    H = A.get_hybrid()
    if H is None:
        sys.exit('임베딩 캐시가 없어 측정을 멈춥니다 — BM25 단독 결과는 운영 설정과 다릅니다.')
    wb = float(os.environ.get('AGENT_W_BM', '1.0'))
    wd = float(os.environ.get('AGENT_W_DENSE', '3.0'))
    rows = []
    for it in items:
        q = it['question']
        ev = evidence_files(it)
        eq, _ = expand(q)
        v = np.asarray(clova.embed(eq), dtype=np.float32)
        v = v / (np.linalg.norm(v) or 1)
        raw = H.search(eq, topk=K * 3, w_bm=wb, w_dense=wd, qvec=v)
        off = [c for c, _ in raw[:K]]
        on = [c for c, _ in raw if doc_policy.allowed(c, q)][:K]
        r = {'id': it['id'], 'trap': it['trap'], 'ev': sorted(ev),
             'off': sorted({c['file'] for c in off} & ev), 'on': sorted({c['file'] for c in on} & ev),
             'blocked': [f"{c['file']}#{c['idx']}" for c in off if not doc_policy.allowed(c, q)]}
        rows.append(r)

    def summ(key):
        E = [r for r in rows if r['ev']]
        rec = sum(len(r[key]) for r in E) / sum(len(r['ev']) for r in E) * 100
        full = sum(len(r[key]) == len(r['ev']) for r in E) / len(E) * 100
        return rec, full, len(E)

    for key, name in (('off', '정책 끄기'), ('on', '정책 켜기')):
        rec, full, n = summ(key)
        print(f'{name}  근거회수@{K} {rec:5.1f}%  전체근거@{K} {full:5.1f}%  ({n}문항)')
    lost = [r for r in rows if set(r['off']) - set(r['on'])]
    gained = [r for r in rows if set(r['on']) - set(r['off'])]
    hit = [r for r in rows if r['blocked']]
    print(f'\n정책 때문에 근거를 잃은 문항 {len(lost)}개: ' + ', '.join(f"{r['id']}({'/'.join(set(r['off']) - set(r['on']))})" for r in lost))
    print(f'정책 덕에 근거가 새로 들어온 문항 {len(gained)}개: ' + ', '.join(f"{r['id']}({'/'.join(set(r['on']) - set(r['off']))})" for r in gained))
    print(f'정책을 끄면 상위 {K} 에 사무담당자 절차·공제회 조각이 들어오던 문항 {len(hit)}개:')
    for r in hit:
        print(f"   {r['id']} [{r['trap']}] {', '.join(r['blocked'])}  정답근거 {r['ev']}")
    json.dump(rows, io.open(os.path.join(ROOT, 'eval', '_policy_retrieval.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
