# -*- coding: utf-8 -*-
"""
질문 하나가 CLOVA 분당 토큰 한도에서 얼마나 차감되는지 잰다.

차감량 = 입력 토큰 + 설정한 출력 상한(실제 출력이 아니다 — 실측으로 확인).
테스트 앱 키는 HCX-007 분당 6만 토큰이라, 질문당 차감량이 곧 '1분에 몇 문항'을 정한다.

    AGENT_MAXTOK=2048 AGENT_MERGE_MAXTOK=4096 AGENT_MERGE_CITED_ONLY=0 python score_tokens.py   # 예전
    python score_tokens.py                                                                     # 지금
"""
import os
import sys
import io
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import clova
import answer as A

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    n = int(os.environ.get('N', '6'))
    items = json.load(io.open(os.path.join(ROOT, 'eval', 'evalset.json'), encoding='utf-8'))['items']
    sel = [it for it in items[1::16] if it['trap'] != '프롬프트공격'][:n]
    if A.get_hybrid() is None:
        sys.exit('임베딩 캐시 없음')
    per_q = []
    for it in sel:
        k0 = len(clova.USAGE)
        t0 = time.time()
        r = A.answer(it['question'], it['id'])
        calls = clova.USAGE[k0:]
        charged = sum((u['prompt'] or 0) + (u['max'] or 0) for u in calls if u['model'] == clova.CHAT_MODEL)
        used = sum((u['prompt'] or 0) + (u['completion'] or 0) for u in calls if u['model'] == clova.CHAT_MODEL)
        per_q.append((it['id'], charged, used, len(calls), time.time() - t0, len(r['answer'])))
        print(f"  {it['id']}: 차감 {charged:,} · 실사용 {used:,} · 호출 {len(calls)} · {time.time() - t0:.0f}초 · 답 {len(r['answer'])}자", flush=True)
    c = sum(x[1] for x in per_q) / len(per_q)
    u = sum(x[2] for x in per_q) / len(per_q)
    print(f"\n질문당 평균 차감 {c:,.0f}토큰 (실사용 {u:,.0f}) → 분당 6만 토큰이면 {60000 / c:.1f}문항/분")
    print(f"설정: 색인 {A.CHUNK_MODE} · 근거 {A.DEFAULT_TOPK}개 · 초안 상한 {A.DRAFT_MAXTOK} · 병합 상한 {A.MERGE_MAXTOK} · "
          f"병합 근거 {'인용분만' if A.MERGE_CITED_ONLY else '전체'}")


if __name__ == '__main__':
    main()
