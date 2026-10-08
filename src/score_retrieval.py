# -*- coding: utf-8 -*-
"""④ 찾기 — 보강 전후 비교."""
import os, sys, json, re, io
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chunker import build_all
from enrich import add_titles, mark_boilerplate
from index_search import BM25, tokenize
from expand import expand

ROOT = r"C:\Users\장선우\Desktop\미래에셋"
EX = os.path.join(ROOT, 'extracted')
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


class Idx:
    def __init__(self, chunks, field='text', penalize_boiler=False, expand_q=False):
        self.chunks = chunks
        self.pen = penalize_boiler
        self.expand_q = expand_q
        self.bm = BM25([tokenize(c.get(field) or c['text']) for c in chunks])

    def __repr__(self):
        return f'<Idx {len(self.chunks)}>'

    def search(self, q, topk=20):
        if self.expand_q:
            q = expand(q)[0]
        res = self.bm.search(q, topk * 3)
        out = []
        for i, s in res:
            c = self.chunks[i]
            if self.pen and c.get('boiler'):
                s *= 0.5
            out.append((c, s))
        out.sort(key=lambda x: -x[1])
        return out[:topk]


def run(idx, items, by_file, label):
    hits = {k: 0 for k in KS}
    full = {k: 0 for k in KS}
    miss = []
    n = 0
    for it in items:
        sn = norm(it.get('evidence_span', ''))
        gold = set()
        for c in by_file.get(it.get('evidence_file', ''), []):
            if sn and sn in norm(c['text']):
                gold.add(id(c))
        for f2, p2 in evidence_locs(it):
            for c in by_file.get(f2, []):
                if p2 is None or c['page'] == p2:
                    gold.add(id(c))
        if not gold:
            continue
        n += 1
        got = [id(c) for c, _ in idx.search(it['question'], max(KS))]
        for k in KS:
            top = set(got[:k])
            if top & gold:
                hits[k] += 1
            if gold.issubset(top):
                full[k] += 1
        if not (set(got[:10]) & gold):
            miss.append(it['id'])
    print(f"\n=== {label} ===")
    print("   " + "  ".join(f"R@{k} {hits[k]/n*100:5.1f}" for k in KS))
    print(f"   전체근거@10 {full[10]/n*100:5.1f}   전체근거@20 {full[20]/n*100:5.1f}")
    if miss:
        print(f"   @10 실패({len(miss)}): {', '.join(miss)}")
    return {'label': label, **{f'R@{k}': round(hits[k]/n*100, 1) for k in KS},
            'full@10': round(full[10]/n*100, 1), 'full@20': round(full[20]/n*100, 1),
            'miss': miss}


def main():
    items = [i for i in json.load(io.open(EVAL, encoding='utf-8'))['items']
             if i.get('evidence_file') != '(해당없음)' and i.get('trap') != '프롬프트공격']
    chunks = build_all(EX, mode='structured', size=2400, overlap=300)
    chunks = add_titles(chunks, EX)
    chunks, nb = mark_boilerplate(chunks)
    by_file = {}
    for c in chunks:
        by_file.setdefault(c['file'], []).append(c)
    print(f"조각 {len(chunks):,} · 보일러플레이트 {nb}")

    res = []
    res.append(run(Idx(chunks, 'text'), items, by_file, "보강 전 (본문만)"))
    res.append(run(Idx(chunks, 'index_text'), items, by_file, "+ 문서 제목 부착"))
    res.append(run(Idx(chunks, 'text', expand_q=True), items, by_file, "+ 질의 확장 (제목 없이)"))
    res.append(run(Idx(chunks, 'index_text', expand_q=True), items, by_file, "+ 질의 확장 + 제목"))

    json.dump(res, io.open(os.path.join(ROOT, 'eval', '_retrieval_score.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print("\n" + "=" * 70)
    print(f"  {'R@1':>6}{'R@3':>7}{'R@5':>7}{'R@10':>7}{'전체@10':>9}  방식")
    for r in res:
        print(f"  {r['R@1']:6.1f} {r['R@3']:6.1f} {r['R@5']:6.1f} {r['R@10']:6.1f} {r['full@10']:8.1f}  {r['label']}")


if __name__ == '__main__':
    main()
