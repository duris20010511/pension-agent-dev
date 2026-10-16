# -*- coding: utf-8 -*-
"""시연 채널 되묻기 조기 반환(10-16) — 두 확인(금액 읽기 이중 확인 → 규칙 조건 대조)이 되묻기로 정해지면
초안·병합을 기다리지 않고 되묻기를 내보내는지.  python -X utf8 src/test_early_ask.py

API 를 부르지 않는다: 생성은 3초 걸리는 고정 문장, 병합은 1.5초 걸리는 첫 초안, 두 확인은 흉내 함수,
검색은 낱말 검색만(use_dense=False), CLOVA 주소는 없는 주소.
  1) 규칙 조건 대조가 0.5초에 '다름' → 초안(3초)을 기다리지 않고 되묻기, 생각 과정에 '기다리지 않음'
  2) 금액 확인이 '다름' → 금액 확인 되묻기(금액 확인이 먼저)
  3) 둘 다 통과 → 예전처럼 초안 3개 + 병합까지 기다려 계산 답
  4) 확인이 없는 대회 규격 경로(clarify_template 없음) → 예전과 같음
  5) 병합 중에 '다름'이 나와도 병합을 기다리지 않음
"""
import os
import sys
import tempfile
import time

os.environ['AGENT_LOG_DIR'] = tempfile.mkdtemp(prefix='early_ask_')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import clova  # noqa: E402

_real_sleep = time.sleep
clova.time.sleep = lambda s: _real_sleep(min(s, 0.3))
clova.BASE = 'https://clova-offline.invalid'
import answer  # noqa: E402
import crosscheck  # noqa: E402
import onto_check  # noqa: E402

MAIN = '총급여 5,000만 원이고 연금저축 600 넣었어요. 12월에 IRP 더 넣으면 얼마나 돌려받아요?'
FIXED = '**결론**: 12월에 IRP에 300만 원을 더 넣으면 세액공제가 늘어납니다.\n\n**근거**\n- [1] 계산값 그대로입니다. ' * 2
CHAT = {'samples': 3, 'draft_effort': 'none', 'calc_effort': 'none', 'clarify_template': True, 'use_dense': False}
ASK = '제가 질문을 이렇게 읽었는데 맞나요? (시험)'
OC_FLAG = {'flag': True, 'ask': ASK, 'sec': 0.5, 'consulted': {'rules': ['R-01'], 'attrs': []},
           'diffs': [{'attr': 'attr.months', 'label': '올해 달 수', 'rule': 'R-01', 'engine': '올해 12달',
                      'question': '올해 8달', 'quote': '5월부터'}]}
OC_PASS = {'flag': False, 'diffs': [], 'sec': 0.5, 'consulted': {'rules': ['R-01'], 'attrs': []}}
XC_FLAG = {'message': '틀린 금액으로 계산하지 않으려고 (시험)', 'sec': 0.3, 'diffs': [(6000000, '600만', '60만', None)]}


def main():
    fail, n = [], 0

    def ok(cond, msg):
        nonlocal n
        n += 1
        if not cond:
            fail.append(msg)

    def slow_gen(msgs, maxtok=None, effort=None, thinking=True):
        _real_sleep(3.0)
        return FIXED, ''
    answer.generate = slow_gen
    merges = []

    def slow_merge(question, ctx, drafts, effort=None, hits=None):
        merges.append(1)
        _real_sleep(1.5)
        return drafts[0]
    answer.merge_drafts = slow_merge

    def set_checks(xc, oc, xc_s=0.2, oc_s=0.5):
        def fx(q, calc):
            _real_sleep(xc_s)
            return xc
        def fo(q, calc):
            _real_sleep(oc_s)
            return oc
        crosscheck.check = fx
        onto_check.check = fo

    # 색인을 처음 읽는 데 15초쯤 걸린다 — 시간을 재기 전에 한 번 데운다(확인 없는 경로)
    answer.answer(MAIN, samples=1, use_dense=False)

    # 1) 규칙 조건 대조 '다름' → 초안을 기다리지 않음
    set_checks(None, OC_FLAG)
    t = time.time()
    r = answer.answer(MAIN, **CHAT)
    el = time.time() - t
    ok(el < 2.5, f'[1] 초안(3초)을 기다림: {el:.1f}초')
    ok(r.get('action') == '되묻기' and r.get('answer') == ASK, f"[1] 되묻기가 아님: {r.get('action')} {str(r.get('answer'))[:40]}")
    ok('기다리지 않음' in (r.get('think_trace') or ''), '[1] 생각 과정에 조기 반환 줄이 없음')
    ok('규칙 조건 대조: 규칙 R-01' in (r.get('think_trace') or '') and '다름' in r['think_trace'], '[1] 규칙 조건 대조 줄이 없음')
    ok(r.get('calc') is None and r.get('sections', {}).get('result') == ASK, '[1] 계산 숫자·칸이 남음')
    ok(r.get('standalone') and r.get('plan'), '[1] 기억할 질문·단계 칸이 빠짐')

    # 2) 금액 확인 '다름' → 금액 확인 되묻기
    set_checks(XC_FLAG, OC_PASS)
    t = time.time()
    r = answer.answer(MAIN, **CHAT)
    el = time.time() - t
    ok(el < 2.5, f'[2] 초안을 기다림: {el:.1f}초')
    ok(r.get('action') == '되묻기' and r.get('answer') == XC_FLAG['message'], f"[2] 금액 확인 되묻기가 아님: {str(r.get('answer'))[:40]}")

    # 3) 둘 다 통과 → 초안 3개 + 병합 끝까지
    set_checks(None, OC_PASS)
    merges.clear()
    t = time.time()
    r = answer.answer(MAIN, **CHAT)
    el = time.time() - t
    ok(el >= 4.4, f'[3] 통과인데 초안·병합을 다 기다리지 않음: {el:.1f}초')
    ok(r.get('action') == '계산 → 설명' and merges, f"[3] 계산 답·병합이 아님: {r.get('action')} 병합 {len(merges)}")
    ok('자기일관성: 초안 3개' in (r.get('think_trace') or '') and '기다리지 않음' not in r['think_trace'], '[3] 병합 줄이 없거나 조기 반환이 섞임')
    ok('규칙 조건 대조: 규칙 R-01' in r['think_trace'] and '통과' in r['think_trace'], '[3] 규칙 조건 대조 통과 줄이 없음')

    # 4) 대회 규격 경로 — 확인을 걸지 않으므로 예전처럼
    set_checks(None, OC_FLAG)
    r = answer.answer(MAIN, samples=3, use_dense=False)
    ok(r.get('action') != '되묻기' and '기다리지 않음' not in (r.get('think_trace') or ''), '[4] 대회 규격 경로가 바뀜')

    # 5) 병합 중 '다름' — 초안(3초) 뒤 병합(1.5초)을 기다리지 않음
    set_checks(None, OC_FLAG, oc_s=3.4)
    merges.clear()
    t = time.time()
    r = answer.answer(MAIN, **CHAT)
    el = time.time() - t
    ok(r.get('action') == '되묻기' and el < 4.4, f'[5] 병합을 기다림: {el:.1f}초 {r.get("action")}')

    _real_sleep(3.5)                       # 버린 생성 스레드가 끝나게
    print(f'{n - len(fail)}/{n} 통과')
    for f in fail:
        print('  실패', f)
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
