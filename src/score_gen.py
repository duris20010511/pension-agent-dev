# -*- coding: utf-8 -*-
"""
생성 방식 비교 — 초안 수와 '생각' 강도를 바꿔 같은 문항에 답하고 채점한다.

    q3low    초안 3개(생각 low) + 병합(생각 끔)   지금 기본(AGENT_MODE=quality)
    q3none   초안 3개(생각 끔)  + 병합(생각 끔)
    one_med  초안 1개(생각 medium)
    one_high 초안 1개(생각 high)

score_config.py 와 다른 점:
  - 문항 하나에 네 설정을 **이어서** 돌린다(순서는 문항마다 돌려 가며). 설정별로 따로 돌리면
    시간대에 따라 API 응답 속도가 달라 시간 비교가 흔들린다.
  - 응답 시간은 한도 대기를 뺀 **API 시간**으로도 잰다. 테스트 키(분당 6만 토큰)에서는 초안 3개
    설정이 한도 대기를 맞고 초안 1개 설정은 안 맞아서, 벽시계 시간만 보면 설정 차이가 아니라
    한도 차이를 재게 된다. clova.TAG 로 호출마다 꼬리표를 달아 문항·설정별로 가른다.
      gen_sec = 본 스레드 호출 시간 합 + 초안 스레드 중 가장 오래 걸린 것(병렬이므로)
  - 호출 기록 원본(calls)을 행마다 남긴다 — 집계 방식을 나중에 바꿔도 다시 돌릴 필요가 없다.

평가셋 순서는 짝수 번째 50문항 → 홀수 번째 50문항(앞 50만으로도 중간 보고가 되게).
결과는 eval/_gen_<tag>.json 에 (문항, 설정)마다 저장하고, 다시 실행하면 이어서 돈다.

  python score_gen.py --tag g1                    # 100문항 × 4설정
  python score_gen.py --tag smoke --limit 2 --only one_med,one_high
  python score_gen.py --report g1                 # 짝비교 표
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
# 생각 깊은 초안 1개의 출력 상한(생각 + 답변). 사전 점검 2문항에서 high 가 1,601·1,745 토큰,
# medium 이 962·1,055 토큰을 썼다. 상한은 실제 사용과 상관없이 통째로 한도에서 빠지므로
# 8192 대신 4096 으로 잡는다. 4096 에서 잘리면(보고서 '잘림') 그 행만 더 큰 상한으로 다시 잰다.
DEEP_MAXTOK = int(os.environ.get('AGENT_DEEP_MAXTOK', '4096'))

CONFIGS = {
    'q3low':    {'samples': 3, 'draft_effort': 'low', 'merge_effort': 'none', 'draft_maxtok': 1600},
    'q3none':   {'samples': 3, 'draft_effort': 'none', 'merge_effort': 'none', 'draft_maxtok': 1600},
    'one_med':  {'samples': 1, 'draft_effort': 'medium', 'draft_maxtok': DEEP_MAXTOK},
    'one_high': {'samples': 1, 'draft_effort': 'high', 'draft_maxtok': DEEP_MAXTOK},
    # 시연 채널(/chat 빠른 모드)과 같게: 초안 1개(생각 high), 계산 답만 생각 강도를 바꾼다(10-13)
    'chat_calc_none': {'samples': 1, 'draft_effort': 'high', 'draft_maxtok': DEEP_MAXTOK, 'calc_effort': 'none'},
    'chat_calc_low': {'samples': 1, 'draft_effort': 'high', 'draft_maxtok': DEEP_MAXTOK, 'calc_effort': 'low'},
    # 지금 기본값 그대로(인자 없음) — 계산 질문은 초안 1개, 나머지는 AGENT_MODE 프리셋. 본선 기능을 다 붙인 뒤의 회귀 확인용
    'cur':      {},
    # 계산 질문 하위셋 요인 실험(A1 세금 계산 블록 켬·끔 × 초안 1개 high·초안 3개 low+병합) — --ids 로 문항을 고른다
    'a1on_1h':  {'samples': 1, 'draft_effort': 'high', 'draft_maxtok': DEEP_MAXTOK, 'use_taxcalc': True},
    'a1on_q3':  {'samples': 3, 'draft_effort': 'low', 'merge_effort': 'none', 'draft_maxtok': 1600, 'use_taxcalc': True},
    'a1off_1h': {'samples': 1, 'draft_effort': 'high', 'draft_maxtok': DEEP_MAXTOK, 'use_taxcalc': False},
    'a1off_q3': {'samples': 3, 'draft_effort': 'low', 'merge_effort': 'none', 'draft_maxtok': 1600, 'use_taxcalc': False},
}
BASELINE = 'q3low'


def path_of(tag):
    return os.path.join(ROOT, 'eval', f'_gen_{tag}.json')


def ordered_items():
    items = json.load(io.open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
    return items[0::2] + items[1::2]


def timing(calls, main):
    """(한도 대기를 뺀 API 시간, 실제 경로에서 한도 대기로 늘어난 시간).

    병렬 초안은 가장 느린 하나만 센다. 대기를 뺀 경로와 넣은 경로는 가장 느린 초안이 다를 수
    있으므로 따로 구해 차이를 대기로 본다."""
    def path(f):
        own = sum(f(c) for c in calls if c['thread'] == main)
        side = {}
        for c in calls:
            if c['thread'] != main:
                side[c['thread']] = side.get(c['thread'], 0.0) + f(c)
        return own + max(side.values(), default=0.0)
    api = path(lambda c: c['sec'])
    return round(api, 2), round(path(lambda c: c['sec'] + c['wait']) - api, 2)


def run(tag, names, limit, workers, stop_after=0, ids=None):
    import clova
    import answer as A
    from score_answer import judge
    if A.get_hybrid() is None:
        sys.exit('임베딩 캐시가 없어 측정을 멈춥니다.')
    items = ordered_items()[:limit] if limit else ordered_items()
    if ids:
        items = [it for it in items if it['id'] in ids]
    out = path_of(tag)
    rows = {}
    if os.path.exists(out):
        rows = {(r['id'], r['cfg']): r for r in json.load(io.open(out, encoding='utf-8'))['rows']}
    lock = threading.Lock()
    print(f'{tag}: {len(items)}문항 × {names} · 이미 {len(rows)}행 · 생각 깊은 초안 상한 {DEEP_MAXTOK}', flush=True)
    t0 = time.time()

    def save():
        # 임시 파일에 다 쓴 뒤 바꿔 끼운다 — 쓰는 도중 중단돼도 결과 파일이 깨지지 않게.
        # 윈도우에서는 다른 프로그램이 파일을 잠깐 열고 있으면 replace 가 실패하므로 몇 번 다시 시도한다.
        tmp = out + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8') as fh:
            json.dump({'configs': {n: CONFIGS[n] for n in names}, 'order': [it['id'] for it in items],
                       'rows': sorted(rows.values(), key=lambda r: (r['id'], r['cfg']))},
                      fh, ensure_ascii=False, indent=1)
        for i in range(20):
            try:
                os.replace(tmp, out)
                return
            except PermissionError:
                time.sleep(0.5)
        print('   (저장 지연: 결과 파일이 잠겨 있어 다음 저장 때 다시 씁니다)', flush=True)

    def generate_row(it, name):
        cfg = CONFIGS[name]
        ctag = f"{it['id']}|{name}|{time.time():.3f}"   # 재실행해도 꼬리표가 겹치지 않게
        main = threading.get_ident()
        tok = clova.TAG.set(ctag)
        t0 = time.time()
        try:
            r = A.answer(it['question'], it['id'], samples=cfg.get('samples'),
                         draft_effort=cfg.get('draft_effort'), draft_maxtok=cfg.get('draft_maxtok'),
                         merge_effort=cfg.get('merge_effort'), use_taxcalc=cfg.get('use_taxcalc'),
                         calc_effort=cfg.get('calc_effort'))
        finally:
            clova.TAG.reset(tok)
        wall = time.time() - t0
        with clova._usage_lock:
            calls = [dict(c) for c in clova.USAGE if c.get('tag') == ctag]
        gen_sec, path_wait = timing(calls, main)
        c7 = [c for c in calls if str(c['model']).startswith('HCX-007')]
        tidx = {main: 0}
        for c in calls:
            tidx.setdefault(c['thread'], len(tidx))
        row = {'id': it['id'], 'cfg': name, 'trap': it['trap'], 'category': it['category'],
               'n': len(it['answer_checklist']), 'wall': round(wall, 1), 'gen_sec': gen_sec,
               'path_wait': path_wait,
               'charged': sum((c['prompt'] or 0) + (c['max'] or 0) for c in c7),
               'thinking': [c['thinking'] for c in c7], 'stops': [c['stop'] for c in c7],
               'calls': [{k: c.get(k) for k in ('model', 'prompt', 'completion', 'thinking', 'max', 'effort',
                                                'sec', 'wait', 'stop', 't_send')}
                         | {'main': c['thread'] == main, 'th': tidx[c['thread']]}
                         for c in calls],
               # 2 = clova 잠금 대기가 sec 에 섞이던 문제를 고친 뒤의 행. 응답 시간 비교는 이 행만 쓴다.
               'timing_v': 2,
               'ans_len': len(r['answer']), 'answer': r['answer'], 'trace': r['think_trace']}
        return row

    def one(it, name):
        prev = rows.get((it['id'], name))
        if prev and prev.get('answer') and 'ok' not in prev:
            # 채점만 실패했던 행 — 돈 들인 생성은 그대로 두고 채점만 다시 한다
            row = {k: v for k, v in prev.items() if k not in ('judge_error', 'error')}
        else:
            row = generate_row(it, name)
        try:
            j = judge(it['question'], it['answer_checklist'], row['answer'], it.get('trap'), it.get('trap_note', ''))
        except Exception as e:
            j = None
            row['judge_error'] = f'{type(e).__name__}: {e}'
        if j:
            row.update({'ok': sum(1 for x in j.get('results', []) if x.get('ok')),
                        # 항목별 판정(다수결 뒤) — 어느 항목이 빠졌는지 나중에 원인을 가르려고 남긴다
                        'items': [bool(x.get('ok')) for x in j.get('results', [])],
                        'trap_handled': bool(j.get('trap_handled')), 'hallucination': bool(j.get('hallucination')),
                        'passes': j.get('passes', 1)})
        elif 'judge_error' not in row:
            row['judge_error'] = 'judge parse fail'
        return row

    def question(idx, it):
        # 설정 순서를 문항마다 한 칸씩 돌린다 — 앞 설정이 한도를 써서 뒤 설정이 늘 대기하는 일을 막는다
        k = idx % len(names)
        todo = [n for n in names[k:] + names[:k] if 'ok' not in rows.get((it['id'], n), {})]
        for name in todo:
            if stop_after and time.time() - t0 > stop_after * 60:
                return          # 시간이 다 됐다 — 남은 건 다음 실행이 이어 받는다
            try:
                row = one(it, name)
            except Exception as e:
                row = {'id': it['id'], 'cfg': name, 'error': f'{type(e).__name__}: {e}'}
            with lock:
                rows[(it['id'], name)] = row
                save()
            mark = (f"{row.get('ok', '?')}/{row.get('n', '?')}" if 'ok' in row
                    else (row.get('error') or row.get('judge_error') or '')[:60])
            print(f"   {it['id']} {name:8} {mark:>6} api {row.get('gen_sec', 0):5.1f}s "
                  f"벽 {row.get('wall', 0):5.1f}s 차감 {row.get('charged', 0):6,} 생각 {row.get('thinking')}",
                  flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(question, i, it) for i, it in enumerate(items)]
        for i, f in enumerate(as_completed(futs), 1):
            f.result()
            if i % 5 == 0:
                print(f'-- {i}/{len(items)}문항 ({(time.time() - t0) / 60:.0f}분)', flush=True)
    left = sum(1 for it in items for n in names if 'ok' not in rows.get((it['id'], n), {}))
    print(f'끝 ({(time.time() - t0) / 60:.0f}분) · 남은 (문항, 설정) {left}개')


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0.0
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def report(tag, first=None):
    D = json.load(io.open(path_of(tag), encoding='utf-8'))
    order = D['order'][:first] if first else D['order']
    by = {}
    for r in D['rows']:
        if 'ok' in r and r['id'] in order:
            by.setdefault(r['cfg'], {})[r['id']] = r
    names = [n for n in (D.get('configs') or CONFIGS) if n in by]       # 합친 파일은 자기 설정 이름을 쓴다
    ids = sorted(set.intersection(*(set(by[n]) for n in names)), key=order.index) if names else []
    print(f'\n짝비교 {len(ids)}문항 (네 설정 모두 채점된 문항만){" — 앞 " + str(first) if first else ""}')
    bad = [r for r in D['rows'] if 'ok' not in r and r['id'] in order]
    if bad:
        print('  채점 안 된 행: ' + ', '.join(f"{r['id']}/{r['cfg']}" for r in bad))
    if not ids:
        return
    clean = [i for i in ids if all(by[n][i].get('timing_v') == 2 for n in names)]
    print(f'  응답 시간(API 중앙·p90)은 잠금 대기 수정 뒤 행만: {len(clean)}문항')
    print(f"  {'설정':9} {'체크리스트':>8} {'함정':>6} {'할루시':>5} {'API중앙':>7} {'API p90':>7} "
          f"{'벽중앙':>6} {'질문당 차감':>9} {'생각중앙':>7} {'잘림':>4} {'답길이':>5}")
    for n in names:
        R = by[n]
        ok = sum(R[i]['ok'] for i in ids) / max(1, sum(R[i]['n'] for i in ids)) * 100
        tr = [i for i in ids if R[i]['trap'] != '없음']
        th = sum(R[i]['trap_handled'] for i in tr) / max(1, len(tr)) * 100
        hal = sum(R[i]['hallucination'] for i in ids) / max(1, len(ids)) * 100
        api = [R[i]['gen_sec'] for i in clean]
        wall = [R[i]['wall'] for i in ids]
        ch = sum(R[i]['charged'] for i in ids) / max(1, len(ids))
        thk = [t for i in ids for t in R[i]['thinking']]
        cut = sum(s == 'length' for i in ids for s in R[i]['stops'])
        ln = pct([R[i]['ans_len'] for i in ids], 0.5)
        ths = f'{th:5.1f}%' if tr else '     -'
        print(f'  {n:9} {ok:7.1f}% {ths} {hal:4.1f}% {pct(api, .5):6.1f}s {pct(api, .9):6.1f}s '
              f'{pct(wall, .5):5.1f}s {ch:9,.0f} {pct(thk, .5):7.0f} {cut:4} {ln:5}')
    if BASELINE not in by:
        return
    B = by[BASELINE]
    print(f'\n  {BASELINE} 대비 (문항별 체크리스트 비율 차의 평균, 95% CI · 이김/비김/짐)')
    for n in names:
        if n == BASELINE:
            continue
        R = by[n]
        d = [(R[i]['ok'] - B[i]['ok']) / B[i]['n'] * 100 for i in ids]
        m = sum(d) / len(d)
        sd = math.sqrt(sum((x - m) ** 2 for x in d) / max(1, len(d) - 1))
        w = sum(x > 0 for x in d)
        l = sum(x < 0 for x in d)
        print(f'    {n:9} {m:+5.1f}pp ±{1.96 * sd / math.sqrt(len(d)):.1f}   {w}/{len(d) - w - l}/{l}')
    print('\n  유형별 체크리스트')
    cats = sorted({B[i]['category'] for i in ids})
    print(f"    {'유형':6} {'문항':>3} " + ' '.join(f'{n:>8}' for n in names))
    for c in cats:
        cs = [i for i in ids if B[i]['category'] == c]
        vals = [sum(by[n][i]['ok'] for i in cs) / sum(by[n][i]['n'] for i in cs) * 100 for n in names]
        print(f'    {c:6} {len(cs):3} ' + ' '.join(f'{v:7.1f}%' for v in vals))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag')
    ap.add_argument('--only', default=','.join(CONFIGS))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--report')
    ap.add_argument('--ids', default='', help='쉼표로 고른 문항만(예: Q-009,Q-010)')
    ap.add_argument('--first', type=int, default=0, help='보고서를 앞 N문항으로 제한')
    ap.add_argument('--stop-after', type=float, default=0,
                    help='N분이 지나면 새 (문항, 설정)을 시작하지 않고 끝낸다 — 명령 시간 제한이 있는 환경에서 나눠 돌리기용')
    a = ap.parse_args()
    if a.report:
        report(a.report, a.first or None)
    else:
        run(a.tag, a.only.split(','), a.limit, a.workers, a.stop_after,
            set(x.strip() for x in a.ids.split(',') if x.strip()) or None)
