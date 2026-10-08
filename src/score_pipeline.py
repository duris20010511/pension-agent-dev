# -*- coding: utf-8 -*-
"""
전체 단계 점수판.

① 자르기  근거 커버율 / 표 헤더 동반율      (score_chunking.py)
② 색인    정답 조각을 자기 텍스트로 검색했을 때 1위로 나오는가
③ 질의이해 유형 분류 정확도 / 역질문 재현율·오탐
④ 찾기    Recall@k
⑤ 답      HyperCLOVA X 필요 — 키 없어 미측정
⑥ 가드    공격 탐지율 / 단정추천 유도 탐지율
"""
import os, sys, json, re, io, random
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chunker import build_all
from index_search import Index
from understand_guard import classify, needs_clarification, guard
from enrich import add_titles, mark_boilerplate
from expand import expand

ROOT = r"C:\Users\장선우\Desktop\미래에셋"
EX = os.path.join(ROOT, 'extracted')
EVAL = os.path.join(ROOT, 'eval', 'evalset.json')
random.seed(20260821)


def norm(s):
    return re.sub(r'\s+', '', s or '')


def evidence_locs(it):
    locs = [(it.get('evidence_file', ''), it.get('evidence_page') or None)]
    for part in re.split(r'[,\n]', it.get('extra_evidence', '') or ''):
        part = part.strip()
        m = re.match(r'(\S+?\.txt)\s*[:：]\s*(\d+)', part)
        if m:
            locs.append((m.group(1), int(m.group(2))))
        elif part.endswith('.txt'):
            locs.append((part, None))
    return locs


def gold_chunks(it, chunks_by_file):
    """정답 조각 = 스팬을 담은 조각 + 지정 근거 위치의 조각"""
    sn = norm(it.get('evidence_span', ''))
    gold = set()
    for c in chunks_by_file.get(it.get('evidence_file', ''), []):
        if sn and sn in norm(c['text']):
            gold.add(id(c))
    for f2, p2 in evidence_locs(it):
        for c in chunks_by_file.get(f2, []):
            if p2 is None or c['page'] == p2:
                gold.add(id(c))
    return gold


def main():
    items = json.load(io.open(EVAL, encoding='utf-8'))['items']
    answerable = [i for i in items if i.get('evidence_file') != '(해당없음)'
                  and i.get('trap') != '프롬프트공격']

    print("자르기 + 색인 구축 중...")
    chunks = build_all(EX, mode='structured', size=2400, overlap=300)
    chunks = add_titles(chunks, EX)
    chunks, _ = mark_boilerplate(chunks)
    by_file = {}
    for c in chunks:
        by_file.setdefault(c['file'], []).append(c)
    idx = Index(chunks)
    idx.chunks = chunks
    print(f"  조각 {len(chunks):,}개 색인 완료\n")

    report = {}

    # ---------------------------------------------------------- ② 색인
    # 정답 조각 텍스트 일부를 그대로 질의로 넣어 1위로 나오는지 (순수 색인 품질)
    probe, hit1 = 0, 0
    sample = random.sample(chunks, 300)
    for c in sample:
        body = re.sub(r'^\[.*?\]$', '', c['text'], flags=re.M).strip()
        if len(body) < 120:
            continue
        q = body[:150]
        probe += 1
        res = idx.search(q, topk=1)
        if res and res[0][0] is c:
            hit1 += 1
    idx_score = hit1 / probe * 100 if probe else 0
    report['②색인_자기검색1위'] = round(idx_score, 1)
    print(f"② 색인   자기검색 1위율   {idx_score:5.1f}점  ({hit1}/{probe})")

    # ---------------------------------------------------------- ③ 질의이해
    cat_ok = sum(1 for it in items if classify(it['question']) == it['category'])
    cat_score = cat_ok / len(items) * 100

    need = [it for it in items if it['trap'] == '정보부족-역질문필요']
    nonneed = [it for it in items if it['trap'] not in ('정보부족-역질문필요', '프롬프트공격')]
    rec = sum(1 for it in need if needs_clarification(it['question'])[0]) / len(need) * 100
    fp = sum(1 for it in nonneed if needs_clarification(it['question'])[0]) / len(nonneed) * 100
    report['③질의이해_분류'] = round(cat_score, 1)
    report['③질의이해_역질문재현'] = round(rec, 1)
    report['③질의이해_역질문오탐'] = round(fp, 1)
    print(f"③ 질의이해 유형 분류      {cat_score:5.1f}점  ({cat_ok}/{len(items)})")
    print(f"          역질문 재현율   {rec:5.1f}점  ({len(need)}문항)")
    print(f"          역질문 오탐률   {fp:5.1f}%   (낮을수록 좋음)")

    # ---------------------------------------------------------- ④ 찾기
    ks = (1, 3, 5, 10, 20)
    hits = {k: 0 for k in ks}
    full = {k: 0 for k in ks}
    misses = []
    for it in answerable:
        gold = gold_chunks(it, by_file)
        if not gold:
            continue
        res = idx.search(expand(it['question'])[0], topk=max(ks))
        got = [id(c) for c, _ in res]
        for k in ks:
            top = set(got[:k])
            if top & gold:
                hits[k] += 1
            if gold and gold.issubset(top):
                full[k] += 1
        if not (set(got[:10]) & gold):
            misses.append(it['id'])
    n = len(answerable)
    for k in ks:
        report[f'④찾기_Recall@{k}'] = round(hits[k] / n * 100, 1)
    report['④찾기_전체근거@10'] = round(full[10] / n * 100, 1)
    print(f"④ 찾기   " + "  ".join(f"R@{k} {hits[k]/n*100:.1f}" for k in ks))
    print(f"          전체근거 포함@10 {full[10]/n*100:5.1f}점")
    if misses:
        print(f"          @10 실패: {', '.join(misses)}")

    # ---------------------------------------------------------- ⑥ 가드
    atks = [it for it in items if it['trap'] == '프롬프트공격']
    pushes = [it for it in items if it['trap'] == '단정추천유도']
    normals = [it for it in items if it['trap'] in ('없음', '대상혼동')]
    a_rec = sum(1 for it in atks if guard(it['question'])[0]) / len(atks) * 100
    p_rec = sum(1 for it in pushes if guard(it['question'])[1]) / len(pushes) * 100
    g_fp = sum(1 for it in normals if guard(it['question'])[0]) / len(normals) * 100
    report['⑥가드_공격탐지'] = round(a_rec, 1)
    report['⑥가드_단정유도탐지'] = round(p_rec, 1)
    report['⑥가드_오탐'] = round(g_fp, 1)
    print(f"⑥ 가드   공격 탐지율     {a_rec:5.1f}점  ({len(atks)}문항)")
    print(f"          단정유도 탐지   {p_rec:5.1f}점  ({len(pushes)}문항)")
    print(f"          정상질의 오차단 {g_fp:5.1f}%   (낮을수록 좋음)")

    print(f"\n⑤ 답     HyperCLOVA X 키 없음 → 미측정")
    report['⑤답'] = 'BLOCKED: HyperCLOVA X 키 필요'

    json.dump(report, io.open(os.path.join(ROOT, 'eval', '_pipeline_score.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    return report


if __name__ == '__main__':
    main()
