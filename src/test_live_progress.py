# -*- coding: utf-8 -*-
"""시연 화면 실시간 단계(10-15) — /chat 의 request_id·GET /chat/progress, answer() 의 progress 콜백.

API 를 부르지 않는다: 생성(answer.generate)을 고정 문장으로 바꾸고, 검색은 낱말 검색만(use_dense=False), CLOVA 주소는 없는 주소.
확인하는 것:
  1) 콜백을 넘기지 않으면 답·생각 과정·단계가 넘긴 때와 글자 그대로 같다(대회 규격·측정 경로 그대로)
  2) 생성 전에 단계·규칙 계산값·생각 강도를 알리고, 답 글자는 알리지 않는다
  3) 서버: 진행 기록은 답을 만드는 동안만 있고, 끝나면 지운다. request_id 가 없거나 형식이 틀리면 콜백을 넘기지 않는다
"""
import asyncio
import os
import sys
import tempfile
import time

os.environ['AGENT_LOG_DIR'] = tempfile.mkdtemp(prefix='live_progress_')   # 운영 로그(logs/)에 시험 기록이 섞이지 않게
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import clova  # noqa: E402

_real_sleep = time.sleep
clova.time.sleep = lambda s: _real_sleep(min(s, 0.3))
clova.BASE = 'https://clova-offline.invalid'
import answer  # noqa: E402
import server  # noqa: E402

MAIN = '총급여 5,000만 원이고 연금저축 600 넣었어요. 12월에 IRP 더 넣으면 얼마나 돌려받아요?'
CANCEL = '55세 전에 연금저축 빼면 전부 16.5%예요? 2,000만 원 해지하면 330만 원 내는 거죠?'
FIXED = '**결론**: 12월에 IRP에 300만 원을 더 넣으면 세액공제가 늘어납니다.\n\n**근거**\n- [1] 계산'
FAST = {'samples': 1, 'draft_effort': 'low', 'draft_maxtok': 4096, 'calc_effort': 'none',
        'clarify_template': True, 'use_dense': False}


def main():
    fail, n = [], 0

    def ok(cond, msg):
        nonlocal n
        n += 1
        if not cond:
            fail.append(msg)

    answer.CHAT_XCHECK = False                  # 이중 확인·판독 기준표는 HCX-005 를 부른다 — 이 시험에서는 끈다
    answer.CHAT_ONTO_CHECK = False
    efforts = []
    answer.generate = lambda msgs, maxtok=None, effort=None, thinking=True: (efforts.append(effort), (FIXED, ''))[1]

    # 1) 콜백 없음 vs 있음 — 생성 경로(계산 → 설명)와 정해진 답 경로(계산 + 되묻기)
    for q in (MAIN, CANCEL):
        evs = []
        a = answer.answer(q, **FAST)
        b = answer.answer(q, progress=evs.append, **FAST)
        ok(a == b, f'[같음] 콜백을 넘기면 응답이 달라짐: {q[:20]}')
        ok(evs and evs[0]['now'].startswith('안전 관문'), f'[순서] 첫 알림이 준비 단계가 아님: {q[:20]} {evs[:1]}')
        ok(all(set(e) <= {'now', 'steps', 'calc'} for e in evs), f'[칸] 알림에 다른 칸이 있음: {q[:20]}')
        ok(not any(FIXED[:10] in str(e) or '57만' in e.get('now', '') for e in evs), f'[답 글자] 알림에 답 글자가 섞임: {q[:20]}')
        if q == MAIN:
            gen = [e for e in evs if e['now'].startswith('AI가 설명')]
            ok(len(gen) == 1, f'[생성] 생성 알림이 하나가 아님: {[e["now"] for e in evs]}')
            if gen:
                g = gen[0]
                ok('생각 none' in g['now'], f"[생성] 계산 답 생각 강도(none)가 안 보임: {g['now']}")
                st = [s['step'] for s in g.get('steps') or []]
                ok(st[:2] == ['안전 관문', '고객 상태'] and '다음 행동' in st and '규칙 계산' in st and '숫자 대조' not in st,
                   f'[생성] 생성 전 단계가 맞지 않음: {st}')
                figs = (g.get('calc') or {}).get('figures') or []
                ok(any('148만 5천' in f for f in figs) and (g.get('calc') or {}).get('rule_ids'),
                   f'[생성] 규칙 계산값이 생성 전에 없음: {figs}')
                ok(st == [s['step'] for s in b['plan']][:len(st)], '[생성] 생성 전 단계가 최종 단계의 앞부분과 다름')
            ok(evs[-1]['now'].startswith('답 속 숫자'), f"[대조] 마지막 알림이 숫자 대조가 아님: {evs[-1]['now']}")
        else:
            ok(not any(e['now'].startswith('AI가 설명') for e in evs), '[정해진 답] 모델을 안 부르는데 생성 알림이 나감')
    ok(efforts and set(efforts) == {'none'}, f'[생성] 계산 답 생각 강도가 none 이 아님: {efforts}')

    # 2) 콜백이 실패해도 답은 나온다
    def bad(ev):
        raise RuntimeError('화면 오류')
    r = answer.answer(MAIN, progress=bad, **FAST)
    ok(r.get('answer') and r.get('action'), '[실패] 콜백 오류로 답이 막힘')
    ok(getattr(answer._PROG, 'cb', None) is None, '[정리] 답이 끝난 뒤에도 콜백이 남음')

    # 3) 서버 — 답을 만드는 동안만 진행 기록, 끝나면 지움, 답 글자 없음
    async def srv():
        seen, got_kw = [], []

        def fake(q, *a, **k):
            got_kw.append('progress' in k)
            cb = k.get('progress')
            if cb:
                cb({'now': '안전 관문 · 규칙 계산 · 문서 검색 중'})
                cb({'now': 'AI가 설명을 쓰는 중(HCX-007 · 생각 low)', 'steps': [{'step': '안전 관문', 'detail': '통과'}],
                    'calc': {'rule_ids': ['R-01'], 'figures': ['49만 5천 원']}})
            _real_sleep(1.2)
            return {'answer': '모델 답 57만 6천 원', 'action': '계산 → 설명'}
        real = server.answer
        server.answer = fake
        clova._NET.update(ok=time.time(), fail=0.0)
        try:
            rid = 'r' * 20
            task = asyncio.ensure_future(server.chat({'question': MAIN, 'mode': 'fast', 'request_id': rid}))
            for _ in range(8):
                await asyncio.sleep(0.15)
                seen.append(await server.chat_progress(rid))
            r = await task
            after = await server.chat_progress(rid)
            live = [s for s in seen if s.get('found')]
            ok(live and live[-1]['now'].startswith('AI가 설명') and live[-1]['calc']['figures'] == ['49만 5천 원'],
               f'[서버] 진행 기록이 보이지 않음: {seen[-1:]}')
            ok(all('answer' not in s and '57만 6천' not in str(s) for s in seen), '[서버] 진행 기록에 답 글자가 섞임')
            ok(r['answer'] == '모델 답 57만 6천 원' and not after.get('found') and rid not in server._PROGRESS,
               f'[서버] 끝난 뒤에도 진행 기록이 남음: {after}')
            await server.chat({'question': MAIN, 'mode': 'fast'})
            await server.chat({'question': MAIN, 'mode': 'fast', 'request_id': 'short'})
            await server.chat({'question': MAIN, 'mode': 'fast', 'request_id': '../../' + 'x' * 20})
            ok(got_kw == [True, False, False, False], f'[서버] request_id 없거나 틀린데 콜백을 넘김: {got_kw}')
            ok((await server.chat_progress('<script>')) == {'found': False}, '[서버] 형식이 틀린 id 에 기록을 돌려줌')
            # 끝나지 않은 기록이 쌓여도 상한을 넘지 않는다
            for k in range(server.PROGRESS_MAX + 5):
                server._progress_sink(f'{k:020d}', time.time() + k)
            ok(len(server._PROGRESS) == server.PROGRESS_MAX and f'{0:020d}' not in server._PROGRESS,
               f'[서버] 진행 기록 상한이 안 지켜짐: {len(server._PROGRESS)}')
            server._PROGRESS.clear()
        finally:
            server.answer = real
    asyncio.run(srv())

    for f in fail:
        print('  ' + f)
    print(f'{n - len(fail)}/{n} 통과')
    sys.exit(1 if fail else 0)


if __name__ == '__main__':
    main()
