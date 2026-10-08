# -*- coding: utf-8 -*-
"""
조각 전체를 임베딩해 캐시에 저장한다.

이 스크립트는 한 번 갈아엎었다. 처음 판의 문제:
  1) print 에 flush 가 없어 진행이 안 보였다 (33분간 로그 0줄)
  2) 중간 저장이 없어 끊기면 처음부터
  3) 벤치마크를 60자 더미로 해서 17건/초로 계산했는데
     실제 조각은 중앙값 1,331자라 훨씬 느렸다

그래서 지금 판은:
  - 진행 로그 즉시 출력 (flush=True)
  - CKPT_EVERY 개마다 중간 저장, 재실행하면 이어서
  - 실제 조각으로 워밍업 측정 후 남은 시간을 추정해 알려줌
"""
import os, sys, time, json
import numpy as np
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import clova
from answer import get_index, CACHE, _fingerprint

MAX_CHARS = 4000       # bge-m3 는 8,192 토큰. 한글 여유 있게.
WORKERS = 2            # 분당 60회 한도라 병렬화 효과 없음. 스레드는 지연 흡수용
CKPT_EVERY = 100
ONLY = os.environ.get('EMB_ONLY', '')   # 'docs' 면 제도문서만 (소규모 검증용)


def log(*a):
    print(*a, flush=True)


def text_of(c):
    title = c.get('title') or ''
    body = c['text']
    if c.get('section'):                       # v2 — 절 경로를 함께 임베딩한다
        body = f"{c['section']}\n{body}"
    return (f'{title}\n{body}' if title else body)[:MAX_CHARS]


def reusable_vectors():
    """다른 지문의 완성된 캐시에서 같은 텍스트의 벡터를 가져온다.
    v2 는 투자설명서 조각이 v1 과 글자 그대로 같아 6,520개를 다시 받을 필요가 없다
    (870만 자 — 임베딩 한도로 2시간이 넘는다)."""
    import glob
    import pickle
    found = {}
    for p in glob.glob(os.path.join(CACHE, 'index_*.pkl')):
        fp = os.path.basename(p)[len('index_'):-len('.pkl')]
        e = os.path.join(CACHE, f'emb_{fp}.npy')
        if not os.path.exists(e):
            continue
        cs = pickle.load(open(p, 'rb'))
        arr = np.load(e)
        if len(cs) != len(arr):
            continue
        for c, v in zip(cs, arr):
            found.setdefault(text_of(c), v)
    return found


def embed_one(args):
    i, t = args
    for attempt in range(4):
        try:
            return i, clova.embed(t)
        except Exception:
            if attempt == 3:
                return i, None
            time.sleep(1.5 * (attempt + 1))


def main():
    idx, chunks = get_index()
    fp = _fingerprint()
    out = os.path.join(CACHE, f'emb_{fp}.npy')
    ckpt = os.path.join(CACHE, f'emb_{fp}.partial.npy')
    meta = os.path.join(CACHE, f'emb_{fp}.partial.json')

    if os.path.exists(out):
        a = np.load(out)
        log(f'이미 완료됨: {out} {a.shape}')
        return

    if ONLY == 'docs':
        keep = [i for i, c in enumerate(chunks) if not c['file'].startswith('KR')]
        out = os.path.join(CACHE, f'emb_{fp}_docs.npy')
        ckpt = os.path.join(CACHE, f'emb_{fp}_docs.partial.npy')
        meta = os.path.join(CACHE, f'emb_{fp}_docs.partial.json')
        log(f'제도문서만: {len(keep):,}조각 (전체 {len(chunks):,} 중)')
        json.dump(keep, open(os.path.join(CACHE, f'emb_{fp}_docs.idx.json'), 'w'))
    else:
        keep = list(range(len(chunks)))

    texts = [text_of(c) for c in chunks]
    n = len(chunks)
    lens = sorted(len(t) for t in texts)
    log(f'조각 {n:,}개 · 텍스트 길이 중앙 {lens[n//2]}자 / P90 {lens[int(n*0.9)]}자')
    log(f'한도 분당 {clova.RATE_PER_MIN:.0f}회 → 간격 {clova.MIN_INTERVAL:.2f}s')

    # 이어하기
    done = set()
    arr = np.zeros((n, 1024), dtype=np.float32)
    if os.path.exists(ckpt) and os.path.exists(meta):
        arr = np.load(ckpt)
        done = set(json.load(open(meta, encoding='utf-8'))['done'])
        log(f'체크포인트에서 이어서: {len(done):,}/{n:,} 완료됨')

    reuse = reusable_vectors()
    hit = 0
    for i in keep:
        if i not in done and texts[i] in reuse:
            arr[i] = reuse[texts[i]]
            done.add(i)
            hit += 1
    if hit:
        log(f'다른 캐시에서 재사용 {hit:,}개')
    todo = [(i, texts[i]) for i in keep if i not in done]
    if not todo:
        log('할 일 없음 → 최종 저장')
    else:
        log(f'남은 {len(todo):,}개 · 워커 {WORKERS}\n')

    t0 = time.time()
    fails = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for k, (i, v) in enumerate(ex.map(embed_one, todo), 1):
            if v is None:
                fails.append(i)
            else:
                arr[i] = v
                done.add(i)
            if k % 50 == 0 or k == len(todo):
                el = time.time() - t0
                rate = k / el
                left = (len(todo) - k) / rate if rate else 0
                log(f'  {k:,}/{len(todo):,}  {el:5.0f}s  {rate:4.1f}/s  '
                    f'남은 {left/60:4.1f}분  실패 {len(fails)}')
            if k % CKPT_EVERY == 0:
                np.save(ckpt, arr)
                json.dump({'done': sorted(done)}, open(meta, 'w', encoding='utf-8'))

    # 정규화 후 최종 저장 (코사인 = 내적)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1
    np.save(out, arr / norms)
    for p in (ckpt, meta):
        if os.path.exists(p):
            os.remove(p)
    log(f'\n완료 {(time.time()-t0)/60:.1f}분 · {arr.shape} · 실패 {len(fails)}건 → {out}')
    if fails:
        log(f'  실패 인덱스 일부: {fails[:10]}')


if __name__ == '__main__':
    main()
