# -*- coding: utf-8 -*-
"""
대화 기억(O5) 측정.

  1) 재작성 — 이어지는 질문 판정(memory.is_followup)과 재작성(followup.rewrite)이 기대 질문과 같은 뜻인가.
     eval/dialogs.json 의 대화마다 턴을 차례로 넣는다(앞 턴의 기억은 우리 재작성 결과로 쌓는다 — 실제 운영과 같게).
       - 판정 정확도: is_followup 이 기대 표시(is_followup)와 맞는가
       - 조건 일치: 기대 질문과 재작성 질문에서 읽은 조건(나이·금액·계좌·소득)이 같은가 (memory.slots, 결정적)
       - 뜻 일치: HCX-005 가 두 질문이 같은 것을 묻는지 판정(1회)
     재작성은 HCX-DASH-002, 판정은 HCX-005 라 생성(HCX-007) 한도를 쓰지 않는다.

  2) 끝까지 — --e2e 면 시연 대화 몇 개를 answer(history=...) 로 끝까지 돌려 답변·세금 계산·기억을 남긴다(HCX-007).

  python score_dialog.py                # 재작성 측정 → eval/_dialog_rewrite.json
  python score_dialog.py --e2e          # 시연 대화 → eval/_dialog_e2e.json
"""
import os
import sys
import io
import json
import time
import argparse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JUDGE = """두 질문이 고객에게서 '같은 것'을 묻는지 판정한다. 표현이 달라도 조건(나이·금액·계좌·소득·상품)과 묻는 내용이 같으면 같다.
조건이 하나라도 다르거나 빠졌거나, 없던 조건이 붙었으면 다르다.
JSON 만 출력: {"same": true, "why": "짧게"}"""


def slot_key(q):
    import memory
    s = memory.slots(q)
    return {k: v for k, v in s.items() if k != 'products'}, set(s.get('products', []))


def judge_same(a, b):
    import clova
    for _ in range(2):
        try:
            out = clova.chat([{'role': 'system', 'content': JUDGE},
                              {'role': 'user', 'content': f'[질문 A]\n{a}\n\n[질문 B]\n{b}'}],
                             temperature=0.0, max_tokens=200, model='HCX-005')
            i, j = out.find('{'), out.rfind('}')
            return json.loads(out[i:j + 1])
        except Exception:
            continue
    return None


def run_rewrite(path, workers, mode='detect'):
    import memory
    import followup
    D = json.load(io.open(path, encoding='utf-8'))['dialogues']

    def one(d):
        hist, rows = [], []
        for k, t in enumerate(d['turns']):
            q = t['user']
            fu, why = memory.is_followup(q, hist, mode=mode)
            if fu:
                sa, how = followup.rewrite(q, hist)
            else:
                sa, how = q, ''
            exp = t.get('expected_standalone') or q
            row = {'id': d['id'], 'kind': d['kind'], 'turn': k, 'q': q, 'expected': exp, 'got': sa,
                   'fu_expected': bool(t.get('is_followup')), 'fu_got': fu, 'why': why, 'how': how}
            if k > 0:
                ke, pe = slot_key(exp)
                kg, pg = slot_key(sa)
                row['slots_ok'] = ke == kg and pe == pg
                row['slots'] = {'expected': [ke, sorted(pe)], 'got': [kg, sorted(pg)]}
                j = judge_same(exp, sa) if sa.strip() != exp.strip() else {'same': True, 'why': '동일'}
                row['same'] = None if j is None else bool(j.get('same'))
                row['judge_why'] = (j or {}).get('why', '')
            rows.append(row)
            hist.append({'q': q, 'standalone': sa, 'answer': '', 'slots': memory.slots(sa)})
        return rows

    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = [r for rs in ex.map(one, D) for r in rs]
    out = os.path.join(ROOT, 'eval', f'_dialog_rewrite_{mode}.json')
    json.dump({'rows': rows}, io.open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    later = [r for r in rows if r['turn'] > 0]
    fu_acc = sum(r['fu_expected'] == r['fu_got'] for r in later) / max(1, len(later))
    same = [r for r in later if r.get('same') is not None]
    hows = {}
    for r in later:
        if r['how']:
            hows[r['how']] = hows.get(r['how'], 0) + 1
    print(f'[{mode}] 재작성 방식: {hows}')
    print(f'이어지는 턴 {len(later)}개 · 판정 정확도 {fu_acc:.1%} · 조건 일치 {sum(r["slots_ok"] for r in later) / max(1, len(later)):.1%}'
          f' · 뜻 일치 {sum(r["same"] for r in same) / max(1, len(same)):.1%} ({len(same)}개 판정)')
    kinds = sorted({r['kind'] for r in later})
    for k in kinds:
        rs = [r for r in later if r['kind'] == k]
        ss = [r for r in rs if r.get('same') is not None]
        print(f'  {k:8} {len(rs):3}턴  판정 {sum(r["fu_expected"] == r["fu_got"] for r in rs) / len(rs):5.0%}'
              f'  조건 {sum(r["slots_ok"] for r in rs) / len(rs):5.0%}  뜻 {sum(r["same"] for r in ss) / max(1, len(ss)):5.0%}')
    print('\n  틀린 예:')
    for r in [r for r in later if not r.get('same') or r['fu_expected'] != r['fu_got']][:15]:
        print(f"   {r['id']}#{r['turn']} [{r['kind']}] {r['q']}\n      기대 {r['expected']}\n      결과 {r['got']}  (판정 {r['fu_got']}/{r['fu_expected']}, {r.get('judge_why', '')})")


DEMO = [
    ('세금 이어 묻기', ['62살인데 IRP에서 연 1,800만 원 받으면 세금은 얼마예요?',
                    '그럼 1,500만 원으로 줄이면요?',
                    '70살부터는요?']),
    ('세액공제와 ISA', ['작년 총급여가 5,200만원인데 연금저축만 있어요. 얼마까지 넣어야 세액공제 최대로 받아요?',
                     '그럼 IRP도 만들어서 300만 원 더 넣으면요?',
                     '다음 달 ISA 만기 3천만원은 어디로 옮기는 게 나아요?']),
    ('상품 이어 묻기', ['미래에셋 전략배분TDF 2045 펀드 어떤 상품이에요?',
                     '그거 위험등급이랑 총보수는요?']),
    # 발표 8쪽 장면 2 — 되묻기 → 정보 보충 → 계산 → 나눠 받기(다음 행동 버튼 문장)
    ('퇴직금 섞인 IRP', ['만 62세예요. IRP에 퇴직금이랑 공제받은 돈이 섞였는데, 올해 1,800만 원 빼면 세금이 어떻게 돼요?',
                     '평가액은 1억이고 퇴직금이 6,000만 원, 나머지는 공제받은 돈이에요. 퇴직소득세 실효세율은 4%, 올해 퇴직해서 처음 받아요.',
                     '올해는 연금수령한도까지만 받고 나머지는 내년에 받으면 세금이 어떻게 달라져요?']),
]


def run_e2e(workers):
    import answer as A
    import memory
    S = memory.Store()

    def one(item):
        name, qs = item
        sid = f'e2e-{name}'
        out = []
        for q in qs:
            t0 = time.time()
            r = A.answer(q, sid, history=S.history(sid))
            S.add(sid, q, r.get('standalone') or q, r['answer'], bool(r.get('injection')), r.get('needs'))
            out.append({'q': q, 'standalone': r.get('standalone'), 'sec': round(time.time() - t0, 1),
                        'trace': r['think_trace'], 'answer': r['answer']})
            print(f'[{name}] {q}\n   → {r.get("standalone")}\n   {[l for l in r["think_trace"].split(chr(10)) if l.startswith(("세금 계산", "대화 기억", "질문 재작성"))]}',
                  flush=True)
        return {'name': name, 'turns': out, 'memory': S.view(sid)}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(one, DEMO))
    path = os.path.join(ROOT, 'eval', '_dialog_e2e.json')
    json.dump(res, io.open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'저장: {path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--set', default=os.path.join(ROOT, 'eval', 'dialogs.json'))
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--e2e', action='store_true')
    ap.add_argument('--mode', default='detect', choices=['detect', 'always'])
    a = ap.parse_args()
    if a.e2e:
        run_e2e(a.workers)
    else:
        run_rewrite(a.set, a.workers, a.mode)
