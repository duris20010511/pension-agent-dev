# -*- coding: utf-8 -*-
"""
응답 시간 단축 측정 — 같은 초안 위에서 병합 방식만 바꿔 짝지어 비교한다.

본선 1차 멘토링에서 "답변은 깔끔한데 시간이 길다"는 지적을 받았다.
배포 서버에서 순차 32~109초(평균 50~65초)였다.

짧은 질문 하나로 재보니 시간의 대부분이 모델의 '생각'이었다.
  HCX-007 thinking.effort  none 0.8초 · low 15.4초
현행은 초안 3개(low) 동시 생성 → 병합(low) 이라 '생각'이 두 번 이어진다.

비교하는 방식 (문항마다 초안을 한 번만 뽑아 여러 방식에 같이 쓴다)
  C0  초안3(low) + 병합(low)        현행
  C1  초안3(low) + 병합(none)       병합만 생각을 끈다
  C2  초안3(low), 병합 생략          합의가 가장 큰 초안을 그대로 쓴다
  C4  초안1(low)                    자기일관성 없이
  C5  초안1(none)

처음엔 C3(초안3 none + 병합 none)도 쟀는데 뺐다. API 한도가 병목이었다 —
HCX-007 은 테스트 앱 키 기준 분당 60회·6만 토큰이고(응답 헤더 x-ratelimit-*),
초안 한 번이 약 8,700토큰이다. 초안을 6개씩 뽑으면 한 문항에 한도를 다 쓴다.
채점기 HCX-005 는 모델별로 따로 세는 한도라 생성과 다투지 않는다(실측).

C0·C1·C2 는 같은 초안을 쓰므로 차이가 순수하게 병합 방식에서 나온다
(초안 표집의 운이 빠진다). 채점은 score_answer.judge (3회 다수결) 그대로.

  python score_speed.py                     # 품질 — 100문항, 워커 3
  python score_speed.py --timing --limit 12 # 시간 — 순차 실행으로 깨끗하게
  python score_speed.py --report            # 저장된 결과로 요약만
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

import answer as A
from score_answer import judge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(ROOT, 'eval', 'evalset.json')

VARIANTS = {
    'C0': '초안3(low)+병합(low)  현행',
    'C1': '초안3(low)+병합(none)',
    'C2': '초안3(low) 병합 생략',
    'C4': '초안1(low)',
    'C5': '초안1(none)',
}


def _timed(fn, *a, **k):
    t0 = time.time()
    out = fn(*a, **k)
    return out, time.time() - t0


def drafts_of(msgs, n, effort):
    """초안 n개를 동시에. (정리된 초안 목록, 벽시계 시간, 호출별 시간)"""
    t0 = time.time()
    per = []

    def one():
        c, dt = _timed(A.generate, msgs, None, effort)
        per.append(round(dt, 1))
        return c[0]

    outs = []
    with ThreadPoolExecutor(max_workers=n) as ex:
        for f in [ex.submit(one) for _ in range(n)]:
            try:
                outs.append(f.result())
            except Exception:
                outs.append('')
    wall = time.time() - t0
    ds = [A.sanitize_answer(o) for o in outs if o and o.strip()]
    return [d for d in ds if d and len(d) > 50], wall, per


def merged(q, ctx, drafts, effort, hits=None):
    if len(drafts) < 2:          # 운영 코드도 초안이 하나면 병합하지 않는다
        return (drafts[0] if drafts else ''), 0.0
    return _timed(A.merge_drafts, q, ctx, drafts, effort=effort, hits=hits)


def run_one(it, timing=False, do_judge=True):
    q = it['question']
    P = A.prepare(q)
    if P['blocked']:
        return {'id': it['id'], 'blocked': True}
    msgs, ctx, hits = P['msgs'], P['ctx'], P['cite_hits']
    rec = {'id': it['id'], 'trap': it.get('trap'), 'n': len(it['answer_checklist'])}

    if timing:
        # 단일 호출을 따로 잰다 — 동시 호출 안에서의 시간과 다를 수 있어서
        (_, rec['t_single_low']) = _timed(A.generate, msgs, None, 'low')
        (_, rec['t_single_none']) = _timed(A.generate, msgs, None, 'none')

    d_low, rec['t_draft_low'], rec['per_low'] = drafts_of(msgs, 3, 'low')
    base_low, rec['agree_low'] = A.pick_base_scored(d_low) if d_low else ('', 0.0)
    m0, rec['t_merge_low'] = merged(q, ctx, d_low, 'low', hits)
    m1, rec['t_merge_none'] = merged(q, ctx, d_low, 'none', hits)
    if 't_single_none' in rec:
        one_none = ''
    else:
        (c, _), rec['t_single_none'] = _timed(A.generate, msgs, None, 'none')
        one_none = A.sanitize_answer(c or '')
    raw = {'C0': m0, 'C1': m1, 'C2': base_low,
           'C4': d_low[0] if d_low else '', 'C5': one_none}
    rec['len_drafts_low'] = [len(d) for d in d_low]
    if timing or not do_judge:
        rec['answers'] = {k: A.finalize(v, hits, []) for k, v in raw.items()}
        return rec

    finals = {k: A.finalize(v, hits, []) for k, v in raw.items()}

    def _j(k):
        j = judge(q, it['answer_checklist'], finals[k], it.get('trap'), it.get('trap_note', ''))
        if not j:
            return k, None
        ok = sum(1 for x in j.get('results', []) if x.get('ok'))
        return k, {'ok': ok, 'trap_handled': bool(j.get('trap_handled')),
                   'hallucination': bool(j.get('hallucination')), 'len': len(finals[k]),
                   'answer': finals[k]}

    with ThreadPoolExecutor(max_workers=len(finals)) as ex:
        rec['v'] = dict(f.result() for f in [ex.submit(_j, k) for k in finals])
    return rec


# ---------------------------------------------------------------- 요약
def _mean_ci(xs):
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else 0.0), 0.0
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, 1.96 * sd / math.sqrt(n)


def _med(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else float('nan')


def report(rows):
    rows = [r for r in rows if not r.get('blocked')]
    J = [r for r in rows if r.get('v') and all(r['v'].get(k) for k in VARIANTS)]
    if J:
        print(f'\n=== 품질 (채점 완료 {len(J)}문항, 같은 문항 짝비교) ===')
        print(f"  {'방식':4} {'체크리스트':>9} {'C0 대비(문항평균, 95%CI)':>26} {'함정':>7} {'할루시':>6} {'길이':>6}  설명")
        traps = [r for r in J if r.get('trap') and r['trap'] != '없음']
        for k, desc in VARIANTS.items():
            ok = sum(r['v'][k]['ok'] for r in J)
            n = sum(r['n'] for r in J)
            diffs = [(r['v'][k]['ok'] - r['v']['C0']['ok']) / r['n'] * 100 for r in J]
            m, ci = _mean_ci(diffs)
            th = sum(r['v'][k]['trap_handled'] for r in traps) / max(1, len(traps)) * 100
            hal = sum(r['v'][k]['hallucination'] for r in J) / len(J) * 100
            ln = sum(r['v'][k]['len'] for r in J) / len(J)
            print(f"  {k:4} {ok / n * 100:8.1f}% {m:+11.1f}pp ±{ci:4.1f}        {th:6.1f}% {hal:5.1f}% {ln:6.0f}  {desc}")

        print('\n  병합 생략 문턱 — 초안 겹침 ≥ τ 이면 C2(생략), 아니면 C0/C1')
        for tau in (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
            for alt in ('C0', 'C1'):
                pick = [('C2' if r['agree_low'] >= tau else alt) for r in J]
                ok = sum(r['v'][p]['ok'] for r, p in zip(J, pick))
                n = sum(r['n'] for r in J)
                th = sum(r['v'][p]['trap_handled'] for r, p in zip(J, pick)
                         if r.get('trap') and r['trap'] != '없음') / max(1, len(traps)) * 100
                skip = sum(p == 'C2' for p in pick) / len(J) * 100
                print(f'   τ={tau:.2f} 나머지={alt}  생략 {skip:5.1f}%  체크리스트 {ok / n * 100:5.1f}%  함정 {th:5.1f}%')

    T = [r for r in rows if 't_draft_low' in r]
    if T:
        print(f'\n=== 시간 (중앙값, {len(T)}문항) ===')
        for key, name in (('t_single_low', '단일 호출 low'), ('t_single_none', '단일 호출 none'),
                          ('t_draft_low', '초안3 동시 low'),
                          ('t_merge_low', '병합 low'), ('t_merge_none', '병합 none')):
            v = [r.get(key) for r in T if r.get(key) is not None]
            if v:
                print(f'   {name:22} {_med(v):5.1f}s   (평균 {sum(v) / len(v):5.1f}s, 최대 {max(v):5.1f}s)')
        est = {
            'C0': [r['t_draft_low'] + r['t_merge_low'] for r in T],
            'C1': [r['t_draft_low'] + r['t_merge_none'] for r in T],
            'C2': [r['t_draft_low'] for r in T],
        }
        if all('t_single_low' in r for r in T):
            est['C4'] = [r['t_single_low'] for r in T]
        if all('t_single_none' in r for r in T):
            est['C5'] = [r['t_single_none'] for r in T]
        print('   방식별 생성 시간 추정 (검색·후처리 제외):')
        for k, v in est.items():
            print(f'     {k} {_med(v):5.1f}s  (평균 {sum(v) / len(v):5.1f}s)  {VARIANTS[k]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--timing', action='store_true', help='순차 실행으로 시간만 잰다(채점 없음)')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=int(os.environ.get('EVAL_WORKERS', '3')))
    ap.add_argument('--stride', type=int, default=1, help='N문항마다 하나 (유형이 고루 섞이게)')
    ap.add_argument('--offset', type=int, default=0)
    ap.add_argument('--tag', default='')
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()

    tag = a.tag or ('timing' if a.timing else 'quality')
    out = os.path.join(ROOT, 'eval', f'_speed_{tag}.json')
    done = {}
    if os.path.exists(out):
        done = {r['id']: r for r in json.load(io.open(out, encoding='utf-8'))}
    if a.report:
        report(list(done.values()))
        return

    items = json.load(io.open(EVAL, encoding='utf-8'))['items']
    if a.timing:
        # 유형이 고루 섞이게 앞에서부터가 아니라 띄엄띄엄 고른다
        step = max(1, len(items) // (a.limit or 12))
        items = items[::step][:a.limit or 12]
    else:
        items = items[a.offset::a.stride]
        if a.limit:
            items = items[:a.limit]
    todo = [it for it in items if it['id'] not in done]
    print(f'{tag}: {len(items)}문항 중 {len(todo)}문항 실행 (저장 {out})')
    if A.get_hybrid() is None:
        sys.exit('임베딩 캐시가 없어 측정을 멈춥니다 — BM25 단독 결과는 운영 설정과 다릅니다.')

    lock = threading.Lock()
    t0 = time.time()
    workers = 1 if a.timing else a.workers
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_one, it, a.timing): it for it in todo}
        for i, f in enumerate(as_completed(futs), 1):
            it = futs[f]
            try:
                r = f.result()
            except Exception as e:
                r = {'id': it['id'], 'error': f'{type(e).__name__}: {e}'}
                print(f"   {it['id']} 오류 {r['error'][:120]}")
            with lock:
                done[r['id']] = r
                json.dump(sorted(done.values(), key=lambda x: x['id']),
                          io.open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print(f'   {i}/{len(todo)} {r["id"]} ({time.time() - t0:.0f}s)', flush=True)
    report(list(done.values()))


if __name__ == '__main__':
    main()
