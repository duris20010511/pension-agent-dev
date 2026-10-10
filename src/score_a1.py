# -*- coding: utf-8 -*-
"""
A1(세금 규칙 계산) 짝비교 — 켠 쪽과 끈 쪽을 같은 질문에 번갈아 돌린다.

  켬  : 세금 계산 블록([문서1]) + 분할 과세 오독 초안 거르기 + 남은 오독 고치기 (answer(use_taxcalc=True))
  끔  : 셋 다 없음(예선·이전 동작)

질문
  평가셋  — A1 이 발동하는 세금 계산 7문항(Q-009·010·012·013·015·026·039)과,
            계산은 없지만 저장 답변에서 같은 오독이 나왔던 3문항(Q-025·035·050). 채점은 체크리스트 3회 다수결.
  시연형  — 평가셋에 없는 세금 계산 질문 6개. 체크리스트가 없으므로 결정적으로 본다:
            ① 문서 규칙으로 손계산한 핵심 금액이 답에 있는가 ② 분할 과세 오독이 남았는가.
            기대 금액은 taxcalc 가 아니라 아래에 직접 적었다(계산기 자신을 정답으로 삼지 않게).

  python score_a1.py --runs 2          # → eval/_a1_ab.json
  python score_a1.py --report
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
OUT = os.path.join(ROOT, 'eval', os.environ.get('AGENT_A1_OUT', '_a1_ab.json'))
EVAL_IDS = ['Q-009', 'Q-010', 'Q-012', 'Q-013', 'Q-015', 'Q-026', 'Q-039', 'Q-025', 'Q-035', 'Q-050']

# (질문, 답에 있어야 할 금액 — 문서 규칙으로 손계산)
DEMO = [
    ('62살, IRP에서 연 1,800만 원 받으면 세금은?', ['297만']),                       # 1,800 × 16.5% (전액)
    ('62살인데 IRP에서 1년에 1,800만원씩 받으면 세금 얼마나 나와요?', ['297만']),
    ('65세인데 연금저축에서 매달 150만원씩 받으면 세금은?', ['297만']),                # 월 150 × 12 = 1,800
    ('총급여 6천만원인데 IRP에 매달 50만원씩 넣으면 세액공제 얼마예요?', ['79만 2천']),  # 600 × 13.2%
    ('65살이고 연금수령 3년차예요. 연금저축 평가액이 3억 있는데 1억을 한 번에 빼면 세금은?',
     ['4,500만']),                                                                  # 3억 ÷ 8 × 120% = 4,500만 한도
    ('ISA 만기 3천만원을 연금저축펀드로 옮기면 그 해 세액공제 한도가 1,200만원까지 늘어나나요?', ['900만']),  # 600 + 300
    # 본선 발표 시연 장면(8쪽·7쪽) — doc39·doc1·doc41 규칙으로 손계산
    ('만 62세예요. IRP 평가액 1억 원 중 퇴직금이 6,000만 원, 나머지는 세액공제 받은 돈과 수익이에요. '
     '퇴직소득세 실효세율은 4%고 올해 퇴직해서 처음 받아요. 올해 1,800만 원 빼면 세금이 얼마예요?', ['57만 6천']),
     # 한도 1억 ÷ 10 × 120% = 1,200만 → 1,200 × 4% × 70% + 600 × 4% = 33.6 + 24
    ('50살인데 연금저축 2,000만 원 해지하면 세금 얼마예요? 세액공제 안 받은 돈이 500만 원 있어요.', ['247만 5천']),
     # (2,000 − 500) × 16.5%
    ('총급여 5,000만 원이에요. 연금저축 600만 원 + IRP 300만 원으로 넣는 것과 연금저축에만 900만 원 넣는 것, '
     '세액공제 환급이 각각 얼마예요?', ['148만 5천', '99만']),
    ('총급여 5,000만 원이고 올해 연금저축에 600만 원 넣었어요. 12월인데 IRP에 더 넣으면 얼마나 더 돌려받아요?', ['49만 5천']),
]
ARMS = ('on', 'off')


def norm(s):
    return (s or '').replace(' ', '')


def has_amount(answer, want):
    """답에 그 금액이 있는가 — 표기만 다른 같은 값('576,000원' = '57만 6천 원' = '57.6만 원')도 맞게 센다."""
    import taxcalc
    if norm(want) in norm(answer):
        return True
    w = [v for v, _, _ in taxcalc.money_spans(want if '원' in want else want + ' 원')]
    if not w:
        return False
    return any(abs(v - w[0]) < 0.05 for v, _, _ in taxcalc.money_spans(answer or ''))


def run(runs, workers):
    import answer as A
    import taxcalc
    from score_answer import judge
    items = json.load(io.open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
    ev = {it['id']: it for it in items if it['id'] in EVAL_IDS}
    jobs = []
    for r in range(runs):
        for k, qid in enumerate(EVAL_IDS):
            jobs.append(('eval', qid, ev[qid]['question'], r, k))
        for k, (q, _) in enumerate(DEMO):
            jobs.append(('demo', f'D-{k + 1:02d}', q, r, k))
    rows = {}
    if os.path.exists(OUT):
        rows = {(x['id'], x['arm'], x['run']): x for x in json.load(io.open(OUT, encoding='utf-8'))['rows']}
    lock = threading.Lock()

    def save():
        tmp = OUT + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8') as fh:
            json.dump({'rows': sorted(rows.values(), key=lambda x: (x['id'], x['run'], x['arm']))}, fh,
                      ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)

    def one(job):
        kind, qid, q, r, k = job
        arms = ARMS if (k + r) % 2 == 0 else ARMS[::-1]      # 순서를 번갈아 — 한도 대기 치우침 방지
        for arm in arms:
            if (qid, arm, r) in rows and 'error' not in rows[(qid, arm, r)]:
                continue
            t0 = time.time()
            try:
                out = A.answer(q, qid, use_taxcalc=(arm == 'on'))
                row = {'id': qid, 'kind': kind, 'arm': arm, 'run': r, 'sec': round(time.time() - t0, 1),
                       'answer': out['answer'], 'trace': out['think_trace'],
                       'misread': taxcalc.misread(out['answer']) or ''}
                if kind == 'eval':
                    it = ev[qid]
                    j = judge(q, it['answer_checklist'], out['answer'], it.get('trap'), it.get('trap_note', ''))
                    if j:
                        row.update({'ok': sum(1 for x in j.get('results', []) if x.get('ok')),
                                    'n': len(it['answer_checklist']), 'trap': it['trap'],
                                    'trap_handled': bool(j.get('trap_handled')),
                                    'hallucination': bool(j.get('hallucination'))})
                else:
                    want = dict(DEMO)[q]
                    row['hit'] = all(has_amount(out['answer'], w) for w in want)
            except Exception as e:
                row = {'id': qid, 'kind': kind, 'arm': arm, 'run': r, 'error': f'{type(e).__name__}: {e}'}
            with lock:
                rows[(qid, arm, r)] = row
                save()
            print(f"   {qid} r{r} {arm:3} " + (f"{row.get('ok')}/{row.get('n')}" if 'ok' in row else
                                              f"금액 {'O' if row.get('hit') else 'X'}" if 'hit' in row else row.get('error', '')[:50])
                  + f" 오독 {'있음' if row.get('misread') else '-'} {row.get('sec', 0)}s", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(one, j) for j in jobs]):
            f.result()
    report()


def report():
    D = json.load(io.open(OUT, encoding='utf-8'))['rows']
    ev = [x for x in D if x['kind'] == 'eval' and 'ok' in x]
    dm = [x for x in D if x['kind'] == 'demo' and 'hit' in x]
    print('\nA1 짝비교')
    for arm in ARMS:
        e = [x for x in ev if x['arm'] == arm]
        d = [x for x in dm if x['arm'] == arm]
        ok = sum(x['ok'] for x in e) / max(1, sum(x['n'] for x in e)) * 100
        tr = [x for x in e if x['trap'] != '없음']
        mis = sum(bool(x['misread']) for x in e + d)
        print(f"  {arm:3} 평가셋 {len(e)}회 체크리스트 {ok:.1f}% · 함정 {sum(x['trap_handled'] for x in tr)}/{len(tr)} · "
              f"할루시 {sum(x['hallucination'] for x in e)} · 시연형 금액 맞음 {sum(x['hit'] for x in d)}/{len(d)} · "
              f"분할 과세 오독 {mis}/{len(e) + len(d)} · 시간 중앙 {sorted(x['sec'] for x in e + d)[len(e + d) // 2] if e + d else 0}s")
    # 문항·회차 짝: 켬 − 끔
    pairs = {}
    for x in ev:
        pairs.setdefault((x['id'], x['run']), {})[x['arm']] = x
    d = [(p['on']['ok'] - p['off']['ok']) / p['on']['n'] * 100 for p in pairs.values() if 'on' in p and 'off' in p]
    if d:
        m = sum(d) / len(d)
        sd = math.sqrt(sum((v - m) ** 2 for v in d) / max(1, len(d) - 1))
        print(f'  켬 − 끔 (평가셋, 문항·회차 짝 {len(d)}개): {m:+.1f}pp (95% CI ±{1.96 * sd / math.sqrt(len(d)):.1f})')
    print('\n  문항별 (켬 / 끔 체크리스트, 회차 합)')
    for qid in EVAL_IDS:
        on = [x for x in ev if x['id'] == qid and x['arm'] == 'on']
        off = [x for x in ev if x['id'] == qid and x['arm'] == 'off']
        if on and off:
            print(f"    {qid}  {sum(x['ok'] for x in on)}/{sum(x['n'] for x in on)}  {sum(x['ok'] for x in off)}/{sum(x['n'] for x in off)}"
                  f"  오독 켬 {sum(bool(x['misread']) for x in on)} 끔 {sum(bool(x['misread']) for x in off)}")
    for k, (q, want) in enumerate(DEMO, 1):
        on = [x for x in dm if x['id'] == f'D-{k:02d}' and x['arm'] == 'on']
        off = [x for x in dm if x['id'] == f'D-{k:02d}' and x['arm'] == 'off']
        print(f"    D-{k:02d} 금액 켬 {sum(x['hit'] for x in on)}/{len(on)} 끔 {sum(x['hit'] for x in off)}/{len(off)}  {q[:40]}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=2)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    if a.report:
        report()
    else:
        run(a.runs, a.workers)
