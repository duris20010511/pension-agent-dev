# -*- coding: utf-8 -*-
"""
⑤ 답 측정.

체크리스트 항목 단위로 채점한다. 통과/실패 이분법이 아니라 "6개 중 4개 충족".

두 조건을 같이 잰다 — 이래야 생성 문제인지 검색 문제인지 갈린다.
  retrieved : 실제 파이프라인 (④ 찾기 결과를 근거로 씀)
  oracle    : 정답 근거를 직접 넣어줌 (검색을 완벽하다고 가정)
oracle 이 높은데 retrieved 가 낮으면 → 검색 문제
둘 다 낮으면 → 생성(프롬프트) 문제
"""
import os, sys, json, io, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import clova
from answer import answer, get_index

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(ROOT, 'eval', 'evalset.json')
OUTD = os.path.join(ROOT, 'eval')

JUDGE_SYS = """당신은 연금 상담 답변을 채점하는 엄격한 평가자입니다.
채점 항목마다 답변이 그 내용을 담고 있는지 판정합니다.

판정 기준:
- 충족(true): 항목의 핵심 내용이 답변에 실제로 담겨 있다. 표현이 달라도 내용이 같으면 충족.
- 미충족(false): 항목 내용이 없거나, 있어도 수치·조건이 틀렸다.

**수치가 틀리면 무조건 미충족입니다.** 한도액·세율·기간·연령이 다르면 관대하게 봐주지 마십시오.
답변이 길다고 후하게 주지 마십시오. 없는 것은 없는 것입니다.

반드시 JSON 만 출력하십시오. 설명 금지."""


JUDGE_PASSES = int(os.environ.get('JUDGE_PASSES', '3'))

# ---------------------------------------------------------------------------
# 채점 프롬프트는 건드리지 말 것.
#
# 속도를 올리려고 "why" 를 빼봤더니 채점기가 검증을 그만두고 도장만 찍었다.
# 같은 답변(Q-053) 에 대해:
#     why 있음(현행)  2/8      why 제거   8/8      인용 강제  4/8
#     Q-011 은        6/6                 6/6                1/6
# 실물을 보면 Q-053 답변에 위험등급·보수·존속기간·예금자보호가 아예 없다.
# 8/8 은 명백한 오채점이다. why 를 쓰게 하는 것이 실제로 확인을 시킨다.
#
# 채점 프롬프트를 바꾸면 그때까지 쌓은 비교값이 전부 무효가 된다.
# 느리면 프롬프트가 아니라 **병렬도**로 해결한다 (아래 EVAL_WORKERS).
# ---------------------------------------------------------------------------


def judge(question, checklist, ans, trap, trap_note):
    """채점 N회 후 항목별 다수결. 기본 3회.

    같은 답변을 3번 재채점해봤더니 99문항 중 29문항에서 판정이 흔들렸고
    전체 충족률이 73.17 / 73.17 / 74.50% 로 3.33pp 범위에 퍼졌다.
    (원본런은 76.50% 였다 — 단발 채점은 이만큼 운이 섞인다.)

    프롬프트 변형끼리의 차이가 3pp 안팎인데 채점 노이즈가 3.33pp 면
    단발 채점으로는 어느 쪽이 나은지 판정할 수 없다. 항목 단위 다수결로
    흔들림을 눌러야 A/B 가 의미를 갖는다. 채점기는 HCX-005 라 3회로 늘려도
    전체 실행 시간에서 차지하는 몫이 작다.
    """
    if JUDGE_PASSES <= 1:
        return _judge_once(question, checklist, ans, trap, trap_note)
    votes = [_judge_once(question, checklist, ans, trap, trap_note)
             for _ in range(JUDGE_PASSES)]
    votes = [v for v in votes if v]
    if not votes:
        return None
    need = len(votes) / 2.0
    res = []
    for i in range(len(checklist)):
        yes = sum(1 for v in votes
                  for x in v.get('results', []) if x.get('n') == i + 1 and x.get('ok'))
        res.append({'n': i + 1, 'ok': yes > need, 'why': f'{yes}/{len(votes)} 표'})
    return {'results': res,
            'trap_handled': sum(bool(v.get('trap_handled')) for v in votes) > need,
            'hallucination': sum(bool(v.get('hallucination')) for v in votes) > need,
            'passes': len(votes)}


def _judge_once(question, checklist, ans, trap, trap_note):
    items = '\n'.join(f'{i+1}. {c}' for i, c in enumerate(checklist))
    extra = ''
    if trap and trap != '없음':
        extra = f'\n\n[추가 확인] 이 질문은 "{trap}" 유형입니다.\n{trap_note}\n' \
                f'답변이 이 점을 제대로 처리했는지 trap_handled 로 판정하십시오.'
    prompt = f"""[고객 질문]
{question}

[채점 항목]
{items}

[채점할 답변]
{ans}
{extra}

다음 JSON 형식으로만 출력:
{{"results": [{{"n": 1, "ok": true, "why": "짧은 근거"}}, ...], "trap_handled": true, "hallucination": false}}

hallucination: 답변에 근거 없이 지어낸 구체적 수치나 사실이 있으면 true."""
    # 채점기는 HCX-005 로 고정한다. 채점에 추론 모델을 쓸 이유가 없고(느리다),
    # 피채점 모델과 같은 모델로 채점하면 자기 답에 후해질 위험도 있다.
    prev = clova.CHAT_MODEL
    clova.CHAT_MODEL = os.environ.get('JUDGE_MODEL', 'HCX-005')
    try:
        out = ''
        for attempt in (0, 1):
            # 1차는 그대로, 2차는 여유를 더 주고 "why 를 짧게" 를 덧붙인다.
            # 채점기가 잘리면 그 문항이 통째로 빠져 런마다 분모가 달라진다 —
            # prior2 측정에서 5문항이 이렇게 날아갔고 prior 런은 1문항이었다.
            # 분모가 다른 두 런은 비교가 안 되므로 파싱은 반드시 살려야 한다.
            p2 = prompt if attempt == 0 else prompt + chr(10) + chr(10) + 'why 는 10자 이내로 짧게 쓰십시오. JSON 만 출력하십시오.'
            out = clova.chat([{'role': 'system', 'content': JUDGE_SYS},
                              {'role': 'user', 'content': p2}],
                             temperature=0.0, max_tokens=2500)
            got = _parse_judge(out, len(checklist))
            if got:
                return got
    finally:
        clova.CHAT_MODEL = prev
    return None


def _parse_judge(out, n_items):
    """채점기 응답에서 결과를 뽑는다. 잘린 JSON도 항목 단위로 건져낸다."""
    m = re.search(r'\{.*\}', out, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d.get('results'), list) and d['results']:
                return d
        except json.JSONDecodeError:
            pass
    # 응답이 잘려 바깥 괄호가 닫히지 않은 경우: 개별 판정만 정규식으로 회수한다.
    res, seen = [], set()
    for mm in re.finditer(r'"n"\s*:\s*(\d+)\s*,\s*"ok"\s*:\s*(true|false)', out):
        k = int(mm.group(1))
        if k not in seen:
            seen.add(k)
            res.append({'n': k, 'ok': mm.group(2) == 'true', 'why': '(부분회수)'})
    if len(res) < n_items:
        return None            # 전 항목을 못 건지면 점수가 왜곡된다 — 실패 처리
    th = re.search(r'"trap_handled"\s*:\s*(true|false)', out)
    hl = re.search(r'"hallucination"\s*:\s*(true|false)', out)
    return {'results': res,
            'trap_handled': (th.group(1) == 'true') if th else True,
            'hallucination': (hl.group(1) == 'true') if hl else False}


def build_oracle(it, by_file):
    """정답 근거를 직접 조립한다."""
    parts = []
    sn = re.sub(r'\s+', '', it.get('evidence_span', ''))
    for c in by_file.get(it.get('evidence_file', ''), []):
        if sn and sn in re.sub(r'\s+', '', c['text']):
            parts.append(f"[문서1] {c['source']}\n{c['text']}")
            break
    if not parts and it.get('evidence_span'):
        parts.append(f"[문서1] {it['evidence_file']}\n{it['evidence_span']}")
    return '\n\n'.join(parts)


def run_one(it, mode, model, by_file):
    try:
        use_rr = mode.endswith('+rr')
        base = mode.replace('+rr', '')
        oracle = build_oracle(it, by_file) if base == 'oracle' else None
        r = answer(it['question'], it['id'], model=model, oracle_context=oracle,
                   use_rerank=use_rr, prompt_mode=os.environ.get('AGENT_PROMPT'))
        j = judge(it['question'], it['answer_checklist'], r['answer'],
                  it.get('trap'), it.get('trap_note', ''))
        if not j:
            return {'id': it['id'], 'error': 'judge parse fail', 'answer': r['answer'][:200]}
        res = j.get('results', [])
        ok = sum(1 for x in res if x.get('ok'))
        return {'id': it['id'], 'trap': it.get('trap'), 'n': len(it['answer_checklist']),
                'ok': ok, 'trap_handled': j.get('trap_handled'),
                'hallucination': j.get('hallucination'),
                'answer': r['answer'], 'think_trace': r['think_trace']}
    except Exception as e:
        return {'id': it['id'], 'error': f'{type(e).__name__}: {e}'}


def summarize(rows, label):
    good = [r for r in rows if 'error' not in r]
    err = [r for r in rows if 'error' in r]
    tot_n = sum(r['n'] for r in good)
    tot_ok = sum(r['ok'] for r in good)
    rate = tot_ok / tot_n * 100 if tot_n else 0
    hal = sum(1 for r in good if r.get('hallucination')) / len(good) * 100 if good else 0
    traps = [r for r in good if r.get('trap') and r['trap'] != '없음']
    th = sum(1 for r in traps if r.get('trap_handled')) / len(traps) * 100 if traps else 0
    print(f"\n=== {label} ===")
    print(f"  체크리스트 충족률  {rate:5.1f}%  ({tot_ok}/{tot_n} 항목)")
    print(f"  함정 대응률        {th:5.1f}%  ({len(traps)}문항)")
    print(f"  할루시네이션       {hal:5.1f}%  (낮을수록 좋음)")
    if err:
        print(f"  오류 {len(err)}건: {[e['id'] for e in err][:6]}")
    by_trap = {}
    for r in traps:
        by_trap.setdefault(r['trap'], []).append(bool(r.get('trap_handled')))
    for k, v in sorted(by_trap.items()):
        print(f"     {k:22} {sum(v)}/{len(v)}")
    return {'label': label, 'checklist': round(rate, 1), 'trap': round(th, 1),
            'hallucination': round(hal, 1), 'errors': len(err)}


def main():
    items = json.load(io.open(EVAL, encoding='utf-8'))['items']
    idx, chunks = get_index()
    by_file = {}
    for c in chunks:
        by_file.setdefault(c['file'], []).append(c)
    print(f"조각 {len(chunks):,} 색인 완료")

    variants = []
    for arg in sys.argv[1:] or ['retrieved:HCX-005', 'oracle:HCX-005']:
        mode, model = arg.split(':')
        variants.append((mode, model))

    summary = []
    for mode, model in variants:
        label = f"{model} · {mode} · {os.environ.get('AGENT_PROMPT','base')}"
        target = items if mode.startswith('retrieved') else [
            i for i in items if i.get('evidence_file') != '(해당없음)']
        print(f"\n{label} — {len(target)}문항 실행 중...")
        rows = []
        t0 = time.time()
        # 병렬도. 한 문항에 호출이 5회(임베딩1+생성1+채점3) 들고, 그 대부분이
        # 응답 대기다. 분당 60회 한도에서 100문항이면 500회 = 최소 8.3분인데
        # 직렬 실행은 4.4시간이 걸렸다 — 한도가 아니라 지연이 병목이다.
        # 4워커면 한도(≈240회/분 요구 < 60회/분 한도는 clova.py 가 알아서 막는다)를
        # 넘지 않으면서 대기 시간을 겹칠 수 있다.
        with ThreadPoolExecutor(max_workers=int(os.environ.get('EVAL_WORKERS', '4'))) as ex:
            futs = {ex.submit(run_one, it, mode, model, by_file): it for it in target}
            for i, f in enumerate(as_completed(futs), 1):
                rows.append(f.result())
                if i % 10 == 0:
                    print(f"   {i}/{len(target)} ({time.time()-t0:.0f}s)")
        rows.sort(key=lambda r: r['id'])
        tag = os.environ.get('AGENT_PROMPT', 'base')
        json.dump(rows, io.open(os.path.join(OUTD, f'_answer_{model}_{mode}_{tag}.json'), 'w',
                                encoding='utf-8'), ensure_ascii=False, indent=1)
        summary.append(summarize(rows, label))

    print("\n" + "=" * 68)
    print(f"  {'체크리스트':>10}{'함정대응':>10}{'할루시':>9}  방식")
    for s in summary:
        print(f"  {s['checklist']:9.1f}%{s['trap']:9.1f}%{s['hallucination']:8.1f}%  {s['label']}")
    json.dump(summary, io.open(os.path.join(OUTD, '_answer_summary.json'), 'w',
                               encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
