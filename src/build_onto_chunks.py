# -*- coding: utf-8 -*-
"""
분류자 두 명(A·B)이 따로 붙인 조각 꼬리표를 합쳐 data/ontology_chunks.json 을 만든다. 펀드 분류는 data/ontology_funds.json 으로.

합치는 규칙(보수적으로) — 둘 다 붙인 꼬리표만 남긴다(교집합). 단계(stages)는 하나도 안 겹치면 둘의 합집합을 쓴다
(모든 조각이 어느 단계엔가는 속해야 경로와 맞춰 볼 수 있다). 둘 중 하나라도 confidence 가 low 면 규칙(R-xx)은 남기지 않는다.
일치율(자카드)을 항목별로 출력한다 — 분류 품질의 지표다.

  python build_onto_chunks.py <labels 폴더>
"""
import collections
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def jacc(a, b):
    a, b = set(a), set(b)
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def main(d):
    vocab = json.load(io.open(os.path.join(d, 'vocab.json'), encoding='utf-8'))
    ok_stage = {s for s, _ in vocab['stages']}
    ok_node = {n for n, _, _ in vocab['nodes']}
    ok_rule = {r for r, _, _ in vocab['rules']}
    out, stats = {}, collections.defaultdict(list)
    missing = []
    for b in (1, 2, 3, 4):
        batch = json.load(io.open(os.path.join(d, f'batch{b}.json'), encoding='utf-8'))
        lab = {}
        for who in ('A', 'B'):
            p = os.path.join(d, f'labels_{b}_{who}.json')
            lab[who] = {x['key']: x for x in json.load(io.open(p, encoding='utf-8'))} if os.path.exists(p) else {}
        for it in batch:
            a, bb = lab['A'].get(it['key']), lab['B'].get(it['key'])
            if not a or not bb:
                missing.append(it['key'])
                continue

            def clean(x, ok):
                return [v for v in dict.fromkeys(x or []) if v in ok]
            sa, sb = clean(a.get('stages'), ok_stage), clean(bb.get('stages'), ok_stage)
            na, nb = clean(a.get('nodes'), ok_node), clean(bb.get('nodes'), ok_node)
            ra, rb = clean(a.get('rules'), ok_rule), clean(bb.get('rules'), ok_rule)
            stats['stages'].append(jacc(sa, sb))
            stats['nodes'].append(jacc(na, nb))
            stats['rules'].append(jacc(ra, rb))
            stages = [s for s in sa if s in sb] or list(dict.fromkeys(sa + sb))
            low = 'low' in (a.get('confidence'), bb.get('confidence'))
            out[it['key']] = {'h': it['h'], 'stages': stages,
                              'nodes': [n for n in na if n in nb],
                              'rules': [] if low else [r for r in ra if r in rb]}
    mean = {k: round(sum(v) / len(v), 3) for k, v in stats.items() if v}
    rule_n = sum(1 for v in out.values() if v['rules'])
    node_n = sum(1 for v in out.values() if v['nodes'])
    meta = {'labeled': len(out), 'missing': len(missing), 'agreement_jaccard': mean,
            'chunks_with_rules': rule_n, 'chunks_with_nodes': node_n,
            'rule': '분류자 두 명의 교집합(단계는 겹침이 없으면 합집합), 한쪽이라도 low 면 규칙 제외'}
    json.dump({'meta': meta, 'chunks': out}, io.open(os.path.join(ROOT, 'data', 'ontology_chunks.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    if missing:
        print('빠진 조각', missing[:10])
    fp = os.path.join(d, 'funds.json')
    if os.path.exists(fp):
        funds = json.load(io.open(fp, encoding='utf-8'))
        dist = collections.Counter(f.get('asset_class') for f in funds)
        risk = collections.Counter(f.get('risk_asset') for f in funds)
        json.dump({'meta': {'n': len(funds), 'asset_class': dist, 'risk_asset': risk,
                            'rule': '펀드 카드(원문 대조 사실)와 퇴직연금 위험자산 문서 기준(doc56 등)'},
                   'funds': funds},
                  io.open(os.path.join(ROOT, 'data', 'ontology_funds.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('펀드', len(funds), dict(dist), dict(risk))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main(sys.argv[1])
