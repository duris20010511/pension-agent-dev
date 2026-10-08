# -*- coding: utf-8 -*-
"""
설정 하나로 평가셋에 답하고 채점한다 — 설정끼리 같은 문항으로 짝지어 비교하기 위한 도구.

설정은 환경변수로 준다(색인·근거 수 등은 모듈을 불러올 때 정해지므로 설정마다 따로 실행한다).

    AGENT_CHUNKS=v1 AGENT_TOPK=8  python score_config.py --tag v1k8  --stride 2
    AGENT_CHUNKS=v3 AGENT_TOPK=10 python score_config.py --tag v3k10 --stride 2
    python score_config.py --compare v1k8 v3k10

문항마다 체크리스트·함정·할루시네이션(채점 3회 다수결)과 함께 CLOVA 분당 토큰 한도에서
차감된 양(입력 + 출력 상한)과 걸린 시간을 남긴다.
"""
import os
import sys
import io
import json
import time
import math
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def path_of(tag):
    return os.path.join(ROOT, 'eval', f'_cfg_{tag}.json')


def run(tag, stride, offset, workers):
    import clova
    import answer as A
    from score_answer import judge
    if A.get_hybrid() is None:
        sys.exit('임베딩 캐시가 없어 측정을 멈춥니다.')
    items = json.load(io.open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
    items = items[offset::stride]
    out = path_of(tag)
    done = {}
    if os.path.exists(out):
        done = {r['id']: r for r in json.load(io.open(out, encoding='utf-8'))['rows']}
    todo = [it for it in items if it['id'] not in done]
    cfg = {'chunks': A.CHUNK_MODE, 'topk': A.DEFAULT_TOPK, 'mode': A.AGENT_MODE,
           'draft_max': A.DRAFT_MAXTOK, 'merge_max': A.MERGE_MAXTOK, 'merge_cited_only': A.MERGE_CITED_ONLY}
    print(f'{tag}: {cfg} · {len(items)}문항 중 {len(todo)}개 실행', flush=True)
    lock = threading.Lock()

    def one(it):
        t0 = time.time()
        r = A.answer(it['question'], it['id'])
        sec = time.time() - t0
        # 토큰 차감량은 문항별이 아니라 실행 전체 합계를 문항 수로 나눠 남긴다(아래 run 끝부분) —
        # 워커가 여럿이면 호출 기록이 섞여 문항별로 가르기 어렵다.
        j = judge(it['question'], it['answer_checklist'], r['answer'], it.get('trap'), it.get('trap_note', ''))
        res = {'id': it['id'], 'trap': it['trap'], 'category': it['category'], 'n': len(it['answer_checklist']),
               'sec': round(sec, 1), 'ctx_chars': len(r['retrieved_context']),
               'answer': r['answer'], 'trace': r['think_trace']}
        if j:
            res.update({'ok': sum(1 for x in j.get('results', []) if x.get('ok')),
                        'trap_handled': bool(j.get('trap_handled')), 'hallucination': bool(j.get('hallucination'))})
        else:
            res['error'] = 'judge parse fail'
        return res

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, it): it for it in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                r = f.result()
            except Exception as e:
                it = futs[f]
                r = {'id': it['id'], 'error': f'{type(e).__name__}: {e}'}
            with lock:
                done[r['id']] = r
                json.dump({'cfg': cfg, 'rows': sorted(done.values(), key=lambda x: x['id'])},
                          io.open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print(f'   {i}/{len(todo)} {r["id"]} ({time.time() - t0:.0f}s)', flush=True)
    # 토큰 — 이 실행에서 HCX-007 호출이 차감된 총량을 문항 수로 나눈다
    chat = [u for u in clova.USAGE if u['model'] == clova.CHAT_MODEL]
    charged = sum((u['prompt'] or 0) + (u['max'] or 0) for u in chat)
    data = json.load(io.open(out, encoding='utf-8'))
    data['tokens'] = {'charged_total': charged, 'questions': len(todo),
                      'charged_per_q': round(charged / max(1, len(todo)))}
    json.dump(data, io.open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"질문당 차감 {data['tokens']['charged_per_q']:,}토큰 (HCX-007, 이 실행 {len(todo)}문항 기준)")


def compare(a, b):
    A_ = json.load(io.open(path_of(a), encoding='utf-8'))
    B_ = json.load(io.open(path_of(b), encoding='utf-8'))
    ra = {r['id']: r for r in A_['rows'] if 'ok' in r}
    rb = {r['id']: r for r in B_['rows'] if 'ok' in r}
    ids = sorted(set(ra) & set(rb))

    def summ(R, ids_):
        ok = sum(R[i]['ok'] for i in ids_)
        n = sum(R[i]['n'] for i in ids_)
        tr = [i for i in ids_ if R[i]['trap'] != '없음']
        th = sum(R[i]['trap_handled'] for i in tr) / max(1, len(tr)) * 100
        hal = sum(R[i]['hallucination'] for i in ids_) / max(1, len(ids_)) * 100
        sec = sorted(R[i]['sec'] for i in ids_)
        ctx = sum(R[i]['ctx_chars'] for i in ids_) / max(1, len(ids_))
        return ok / max(1, n) * 100, th, hal, sec[len(sec) // 2] if sec else 0, ctx

    print(f'\n짝비교 {len(ids)}문항 — {a} vs {b}')
    print(f"  {'설정':10} {'체크리스트':>9} {'함정':>7} {'할루시':>6} {'시간중앙':>8} {'근거글자':>8} {'질문당 차감':>10}")
    for tag, R, D in ((a, ra, A_), (b, rb, B_)):
        c, th, hal, sec, ctx = summ(R, ids)
        tok = (D.get('tokens') or {}).get('charged_per_q', 0)
        print(f'  {tag:10} {c:8.1f}% {th:6.1f}% {hal:5.1f}% {sec:7.1f}s {ctx:8,.0f} {tok:10,}')
    diffs = [(rb[i]['ok'] - ra[i]['ok']) / ra[i]['n'] * 100 for i in ids]
    m = sum(diffs) / len(diffs)
    sd = math.sqrt(sum((d - m) ** 2 for d in diffs) / max(1, len(diffs) - 1))
    print(f'  {b} − {a}: 문항평균 {m:+.1f}pp (95% CI ±{1.96 * sd / math.sqrt(len(diffs)):.1f})')
    for cat in sorted({ra[i]['category'] for i in ids}):
        cs = [i for i in ids if ra[i]['category'] == cat]
        da = sum(ra[i]['ok'] for i in cs) / sum(ra[i]['n'] for i in cs) * 100
        db = sum(rb[i]['ok'] for i in cs) / sum(rb[i]['n'] for i in cs) * 100
        print(f'    {cat:6} ({len(cs):2}문항) {da:5.1f}% → {db:5.1f}%')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag')
    ap.add_argument('--stride', type=int, default=1)
    ap.add_argument('--offset', type=int, default=0)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--compare', nargs=2)
    a = ap.parse_args()
    if a.compare:
        compare(*a.compare)
    else:
        run(a.tag, a.stride, a.offset, a.workers)
