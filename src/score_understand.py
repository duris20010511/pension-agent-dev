# -*- coding: utf-8 -*-
"""③ 질의이해(LLM) + ④ 전체근거 — 규칙 기반 대비 측정."""
import os, sys, json, io, re, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

from answer import get_index
from expand import expand
from understand_guard import classify, needs_clarification
from understand_llm import analyze, multi_search

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


def main():
    items = json.load(io.open(EVAL, encoding='utf-8'))['items']
    ans_items = [i for i in items
                 if i.get('evidence_file') != '(해당없음)' and i.get('trap') != '프롬프트공격']
    idx, chunks = get_index()
    by_file = {}
    for c in chunks:
        by_file.setdefault(c['file'], []).append(c)
    print(f"조각 {len(chunks):,}\n")

    # LLM 분석 (질의당 1회 호출)
    print(f"LLM 질의 분석 — {len(items)}문항")
    t0 = time.time()
    ana = {}
    for i, it in enumerate(items, 1):
        ana[it['id']] = analyze(it['question'])
        if i % 10 == 0:
            print(f"   {i}/{len(items)} ({time.time()-t0:.0f}s)")
    fb = sum(1 for a in ana.values() if a['source'] != 'llm')
    print(f"   완료 {time.time()-t0:.0f}s · fallback {fb}건\n")

    # ------------------------------------------------ ③ 분류·역질문
    print("=" * 66)
    print("③ 질의이해")
    print("=" * 66)
    for label, fn_cat, fn_clar in (
            ('규칙', lambda it: classify(it['question']),
             lambda it: needs_clarification(it['question'])[0]),
            ('LLM ', lambda it: ana[it['id']]['category'],
             lambda it: ana[it['id']]['need_clarification'])):
        ok = sum(1 for it in items if fn_cat(it) == it['category'])
        need = [it for it in items if it['trap'] == '정보부족-역질문필요']
        non = [it for it in items if it['trap'] not in ('정보부족-역질문필요', '프롬프트공격')]
        rec = sum(1 for it in need if fn_clar(it)) / len(need) * 100
        fp = sum(1 for it in non if fn_clar(it)) / len(non) * 100
        print(f"  {label}  분류 {ok/len(items)*100:5.1f}%   "
              f"역질문 재현 {rec:5.1f}%   오탐 {fp:5.1f}%")

    # ------------------------------------------------ ④ 검색
    print()
    print("=" * 66)
    print("④ 찾기 — 단일 질의 vs 하위질의 분해")
    print("=" * 66)

    def run(label, searcher):
        hit = {k: 0 for k in KS}
        full = {k: 0 for k in KS}
        for it in ans_items:
            gold = gold_of(it, by_file)
            if not gold:
                continue
            got = [id(c) for c, _ in searcher(it)]
            for k in KS:
                top = set(got[:k])
                if top & gold:
                    hit[k] += 1
                if gold.issubset(top):
                    full[k] += 1
        n = len(ans_items)
        print(f"  {label}")
        print("     " + "  ".join(f"R@{k} {hit[k]/n*100:5.1f}" for k in KS)
              + f"   전체근거@10 {full[10]/n*100:5.1f}  @20 {full[20]/n*100:5.1f}")
        return {'label': label, **{f'R@{k}': round(hit[k]/n*100, 1) for k in KS},
                'full@10': round(full[10]/n*100, 1), 'full@20': round(full[20]/n*100, 1)}

    res = []
    res.append(run('단일 질의 (현행)',
                   lambda it: idx.search(expand(it['question'])[0], topk=20)))
    res.append(run('하위질의 분해 (LLM)',
                   lambda it: multi_search(idx, ana[it['id']]['subqueries'], expand,
                                           topk=20, per_query=8)))

    nsub = [len(a['subqueries']) for a in ana.values()]
    print(f"\n  하위질의 개수: 평균 {sum(nsub)/len(nsub):.1f} "
          f"(1개 {nsub.count(1)}문항 / 2개 {nsub.count(2)} / 3개+ {sum(1 for x in nsub if x>=3)})")

    json.dump({'analysis': ana, 'retrieval': res},
              io.open(os.path.join(ROOT, 'eval', '_understand_score.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
