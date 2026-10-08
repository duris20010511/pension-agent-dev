# -*- coding: utf-8 -*-
"""
규칙 수정(질의 확장·가드·되묻기·주의사항) 전후를 같은 문항으로 짝지어 비교한다.

팀 검토(현서_1차)로 고친 규칙이 실제 답변을 좋게 했는지 확인한다.
  old : 예선 제출본(git HEAD)의 expand.py · understand_guard.py
  new : 작업본
규칙만 갈아 끼우고 나머지(검색·생성·병합·후처리)는 같은 코드로 돈다.
규칙 함수는 answer 모듈의 전역 이름이라 바꿔 끼우는 동안 두 설정을 동시에 돌릴 수 없다.
그래서 설정별로 차례로 돌리고(old→new→old→new) 문항만 병렬로 돌린다.

  python score_rules_ab.py                 # 규칙 판정이 바뀌는 문항만 자동으로 골라 2회씩
  python score_rules_ab.py --report
"""
import os
import sys
import io
import json
import types
import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import answer as A
import expand as new_expand
import understand_guard as new_guard
from score_answer import judge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'eval', '_rules_ab.json')


def _load_head(name):
    src = subprocess.run(['git', '-C', ROOT, 'show', f'HEAD:src/{name}.py'],
                         capture_output=True, check=True).stdout.decode('utf-8')
    m = types.ModuleType(f'{name}_head')
    exec(compile(src, f'HEAD:{name}.py', 'exec'), m.__dict__)
    return m


OLD = {'expand': _load_head('expand'), 'guard': _load_head('understand_guard')}


def use(arm):
    e, g = (OLD['expand'], OLD['guard']) if arm == 'old' else (new_expand, new_guard)
    A.expand = e.expand
    A.guard = g.guard
    A.needs_clarification = g.needs_clarification


def decisions(q, arm):
    use(arm)
    return (A.guard(q), A.needs_clarification(q), A.expand(q)[1])


def changed_items(items):
    out = []
    for it in items:
        if decisions(it['question'], 'old') != decisions(it['question'], 'new'):
            out.append(it)
    use('new')
    return out


def run_one(it):
    r = A.answer(it['question'], it['id'])
    j = judge(it['question'], it['answer_checklist'], r['answer'], it.get('trap'), it.get('trap_note', ''))
    if not j:
        return {'error': 'judge parse fail'}
    return {'ok': sum(1 for x in j.get('results', []) if x.get('ok')), 'n': len(it['answer_checklist']),
            'trap_handled': bool(j.get('trap_handled')), 'hallucination': bool(j.get('hallucination')),
            'answer': r['answer'], 'trace': r['think_trace']}


def report(data):
    rows = data['rows']
    print(f"\n규칙 전후 비교 — {len(rows)}문항 × {data['runs']}회 (같은 문항 짝비교)")
    print(f"  {'문항':6} {'함정':10} {'old 체크':>9} {'new 체크':>9} {'old 함정':>8} {'new 함정':>8}  바뀐 판정")
    tot = {'old': [0, 0, 0, 0], 'new': [0, 0, 0, 0]}
    for r in rows:
        line = []
        for arm in ('old', 'new'):
            rs = [x for x in r[arm] if 'error' not in x]
            ok = sum(x['ok'] for x in rs)
            n = sum(x['n'] for x in rs)
            th = sum(x['trap_handled'] for x in rs)
            tot[arm][0] += ok
            tot[arm][1] += n
            if r['trap'] != '없음':
                tot[arm][2] += th
                tot[arm][3] += len(rs)
            line.append((ok, n, th, len(rs)))
        (o_ok, o_n, o_th, o_k), (n_ok, n_n, n_th, n_k) = line
        print(f"  {r['id']:6} {r['trap'][:10]:10} {o_ok:>4}/{o_n:<4} {n_ok:>4}/{n_n:<4} {o_th:>4}/{o_k:<3} {n_th:>4}/{n_k:<3}  {r['what']}")
    for arm in ('old', 'new'):
        ok, n, th, k = tot[arm]
        print(f"  {arm}: 체크리스트 {ok / max(1, n) * 100:5.1f}%  함정 {th / max(1, k) * 100:5.1f}%")
    leak = [(r['id'], arm) for r in rows for arm in ('old', 'new')
            for x in r[arm] if '사무담당자' in (x.get('answer') or '')]
    print(f"  답변에 '사무담당자' 가 나온 경우: {leak or '없음'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=2)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    if a.report:
        report(json.load(io.open(OUT, encoding='utf-8')))
        return
    if A.get_hybrid() is None:
        sys.exit('임베딩 캐시가 없어 측정을 멈춥니다 — BM25 단독 결과는 운영 설정과 다릅니다.')
    items = json.load(io.open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
    target = changed_items(items)
    rows = []
    for it in target:
        o, n = decisions(it['question'], 'old'), decisions(it['question'], 'new')
        what = []
        if o[0] != n[0]:
            what.append(f"가드 {o[0][1]}→{n[0][1]}")
        if o[1] != n[1]:
            what.append(f"되묻기 {o[1][0]}→{n[1][0]}")
        if o[2] != n[2]:
            what.append(f"확장 -{sorted(set(o[2]) - set(n[2]))} +{sorted(set(n[2]) - set(o[2]))}")
        rows.append({'id': it['id'], 'trap': it['trap'], 'what': '; '.join(what), 'old': [], 'new': [], '_it': it})
    use('new')
    print(f'규칙 판정이 바뀌는 문항 {len(rows)}개: ' + ', '.join(r['id'] for r in rows))
    for k in range(a.runs):
        for arm in ('old', 'new'):
            use(arm)
            with ThreadPoolExecutor(max_workers=a.workers) as ex:
                res = list(ex.map(lambda r: run_one(r['_it']), rows))
            for r, x in zip(rows, res):
                r[arm].append(x)
            print(f'  {k + 1}회차 {arm} 완료', flush=True)
            data = {'runs': k + 1, 'rows': [{kk: v for kk, v in r.items() if kk != '_it'} for r in rows]}
            json.dump(data, io.open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    use('new')
    report(data)


if __name__ == '__main__':
    main()
