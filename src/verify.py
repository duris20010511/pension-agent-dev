# -*- coding: utf-8 -*-
"""
답 속 숫자 대조 — 답변에 나온 금액·비율이 질문·규칙 계산·근거 문서에 실제로 있는지 확인한다.

왜 — 세금 숫자는 틀리면 바로 민원이다. 규칙 계산(taxcalc)을 근거로 넣어도 모델이 다른 숫자를 쓸 수 있다
(시연 질문에서 연금수령한도를 '1,500만 원'으로 바꿔 쓴 답이 나왔다). 그래서 내보내기 전에 숫자를 하나씩 대조한다.

판정
  확인됨   질문·계산 블록·근거 문서에 같은 값이 있거나, 그런 값끼리의 곱(금액 × 비율)·합·차로 나온다
  미확인   어디에서도 나오지 않는다 — 생각 과정에 'n/m 일치'로 남기고 화면에 표시한다
  어긋남   규칙 계산이 낸 핵심 금액(합계 세금·환급액)이 답에 없고, 답에는 세금·환급 문맥의 다른 금액이 있다
           → 답을 내보내지 않고 계산값 요약 + 상담 연결 안내로 바꾼다(AGENT_VERIFY=handoff, 기본)
           → AGENT_VERIFY=note 면 답 끝에 '계산 확인' 줄만 붙인다

이 모듈은 API 를 부르지 않는다.
"""
import os
import re

import taxcalc

MODE = os.environ.get('AGENT_VERIFY', 'handoff')        # handoff | note | off
_PCT = re.compile(r'(\d+(?:\.\d+)?)\s*%')
_TAXY = re.compile(r'세금|세액|환급|돌려\s*받|공제액|합계|총\s*\S{0,4}세|납부|내야|낼\s|떼|소득세|과세|냅니다|내요|내게|부과')
_HYPO = re.compile(r'예를\s*들어|예컨대|예시|예:|이라면|라면|가정하면|경우에는|만약')
_TOL_ABS = 0.5          # 만 원 — 5천 원 안쪽 반올림 차이는 같은 값
_TOL_REL = 0.006


def _same(a, b):
    return abs(a - b) <= max(_TOL_ABS, _TOL_REL * max(abs(a), abs(b)))


def money_values(text):
    """[(만원 값, 시작, 끝, 원문)] — taxcalc 의 금액 읽기를 그대로 쓴다(억·천·만·원 표기)."""
    out = []
    for v, s, e in taxcalc.money_spans(text or ''):
        out.append((v, s, e, text[s:e].strip()))
    return out


def pct_values(text):
    return [(float(m.group(1)), m.start(), m.end(), m.group(0)) for m in _PCT.finditer(text or '')]


_SENT_END = re.compile(r'[\n!?]|(?<!\d)\.|\.(?!\d)')


def _sentence(text, i):
    ends = [m.start() for m in _SENT_END.finditer(text or '')]
    a = max([e for e in ends if e < i], default=-1) + 1
    b = min([e for e in ends if e >= i], default=len(text or ''))
    return (text or '')[a:b].strip()


def _support_sets(sources):
    money, pcts = [], []
    for t in sources:
        money += [v for v, *_ in money_values(t)]
        pcts += [p for p, *_ in pct_values(t)]
    # 근거에 늘 나오는 제도 상수는 문서에 있지만 조각이 잘려 빠질 수 있어 함께 둔다(R-01·R-08·연 납입한도)
    money += [600, 900, 1500, 1800, 300, 5500, 4500, 1200]
    pcts += [16.5, 13.2, 5.5, 4.4, 3.3, 70, 60, 50, 30, 40, 120]
    money = sorted(set(round(v, 4) for v in money))[:400]
    pcts = sorted(set(pcts))[:60]
    return money, pcts


def _derived(money, pcts):
    """확인된 값에서 나올 수 있는 값들(정렬) — 같음·금액×비율·합·차·금액×비율×감면비율."""
    import bisect  # noqa: F401 (아래 _hit 에서 씀)
    out = set(money)
    for m in money:
        for p in pcts:
            out.add(round(m * p / 100, 4))
            if p <= 20:
                for r in (0.5, 0.6, 0.7):            # 퇴직소득세 × 감면 후 비율(R-09)
                    out.add(round(m * p / 100 * r, 4))
    n = len(money)
    for i in range(n):
        for j in range(i, n):
            out.add(round(money[i] + money[j], 4))
            out.add(round(abs(money[i] - money[j]), 4))
    return sorted(out)


def _hit(v, derived):
    import bisect
    lo = bisect.bisect_left(derived, v - max(_TOL_ABS, _TOL_REL * abs(v)))
    return lo < len(derived) and derived[lo] <= v + max(_TOL_ABS, _TOL_REL * abs(v))


_BREAK = re.compile(r'\n[ \t]*(?:-{3,}|#{1,4}[ \t])')
# '결론' 소제목만 — '요약 표'는 예시 금액(IRP 100만 원 넣으면 …)을 늘어놓는 자리라 넣지 않는다(10-12 실측 답에서 오판)
_LEAD_HEAD = re.compile(r'^[ \t]*(?:#{1,4}[ \t]*|\*\*)[ \t]*(?:핵심[ \t]*)?결론[^\n]{0,8}$', re.M)


def _lead_ranges(body):
    """결론 자리 — 답 첫머리(첫 구분선·소제목 전, 최대 400자)와 '결론·요약' 소제목 아래 단락."""
    m = _BREAK.search(body)
    out = [(0, min(m.start() if m else len(body), 400))]
    for h in _LEAD_HEAD.finditer(body):
        nxt = _BREAK.search(body, h.end())
        out.append((h.start(), nxt.start() if nxt else len(body)))
    return out


def check(answer, question='', calc=None, context='', extra=(), lead_strict=False):
    """→ {'checked','matched','unmatched':[{'text','value','kind','sentence'}],'contradiction':bool,'action','key_figures'}"""
    calc_text = '\n'.join(b['text'] for b in (calc or {}).get('blocks', []))
    money, pcts = _support_sets([question or '', calc_text, context or ''] + list(extra))
    derived = _derived(money, pcts)
    # 근거 목록(답 끝 '**근거**' 이하)과 각주 번호는 대조하지 않는다
    body = re.split(r'\n\*\*(?:근거|참고 자료)\*\*', answer or '')[0]
    items, unmatched = [], []
    for v, s, e, raw in money_values(body):
        if v < 1 and '원' not in raw:
            continue
        items.append(('금액', v, s, raw))
    for p, s, e, raw in pct_values(body):
        items.append(('비율', p, s, raw))
    matched = 0
    # 비율도 퇴직소득세 감면 계산(R-09)에서 나오는 값은 인정 — '10% × 70% = 7%', '감면 30% → 70%'
    pct_ok = set(pcts)
    for p in pcts:
        if p in (30, 40, 50):
            pct_ok.add(100 - p)
        if p <= 20:
            for r in (50, 60, 70):
                pct_ok.add(round(p * r / 100, 4))
    for kind, v, s, raw in items:
        ok = _hit(v, derived) if kind == '금액' else any(abs(v - p) < 0.01 for p in pct_ok)
        if ok:
            matched += 1
        else:
            unmatched.append({'text': raw, 'value': v, 'kind': kind, 'sentence': _sentence(body, s)[:120]})
    # 규칙 계산과 어긋남 — 계산이 낸 핵심 금액이 답에 하나도 없고, 세금·환급 문장에 계산·질문에 없는 금액이 있다.
    # 곱·합으로 '만들 수 있는' 값이어도 계산과 다르면 어긋난 것이다(1,800만 원 × 4% = 72만 원처럼 감면을 빼먹은 계산).
    keys = [b['key_figure'] for b in (calc or {}).get('blocks', []) if b.get('numbers') and b.get('key_figure')]
    key_vals = [v for k in keys for v, *_ in money_values(k)]
    ans_vals = [it[1] for it in items if it[0] == '금액']
    has_key = not key_vals or any(_same(a, k) for a in ans_vals for k in key_vals)
    stated = [v for v, *_ in money_values(calc_text + '\n' + (question or ''))]
    taxy_bad = []
    for kind, v, s, raw in items:
        sent = _sentence(body, s)
        if kind != '금액' or not _TAXY.search(sent) or _HYPO.search(sent):
            continue
        if not any(_same(v, x) for x in stated) and v not in (600, 900, 1500, 1800, 300, 5500, 4500):
            taxy_bad.append(raw)
    # 블록이 참고로 실은 다른 나이 구간 세액(alt_figures)만 쓰고 핵심 금액이 없으면 고객 구간을 잘못 고른 답이다 —
    # 블록에 있는 값이라 '확인됨'이 되지만, 어긋남 판정에서는 계산에 없는 값처럼 본다
    alts = [v for b in (calc or {}).get('blocks', []) if b.get('numbers')
            for f in b.get('alt_figures', []) for v, *_ in money_values(f)]
    if alts and key_vals and not has_key:
        for kind, v, s, raw in items:
            sent = _sentence(body, s)
            if kind == '금액' and _TAXY.search(sent) and not _HYPO.search(sent) and any(_same(v, a) for a in alts) \
                    and raw not in taxy_bad:
                taxy_bad.append(raw)
    contradiction = bool(key_vals) and not has_key and bool(taxy_bad)
    # 핵심 금액은 맞게 썼지만 세금 문장에 계산에 없는 금액이 따로 있다 — 모델이 블록을 무시하고 다시 계산한 값
    # (시연: 나눠 받기를 '내년 600만 × 4% × 60% = 14.4만, 합계 48만'으로 다시 계산). 답은 두고 끝에 계산값을 밝힌다.
    # 다만 질문·블록·앞 대화의 금액에 세율(16.5·13.2·5.5·4.4·3.3%)을 곱한 값은 쪼개 보인 계산이다 — 계좌별 공제액
    # (연금저축 400만 × 16.5% = 66만 + IRP 500만 × 16.5% = 82.5만)에 '계산 확인'이 붙었다(대화 C05·C18 실측).
    # 이 허용은 핵심 금액이 답에 있을 때(충돌)만 — 핵심 금액이 없으면(어긋남) 예전처럼 엄격하다(잘못된 세율 적용을 잡아야 한다)
    base = stated + [v for t in extra for v, *_ in money_values(t)]
    rates = {p for p, *_ in pct_values(calc_text)} & {16.5, 13.2, 5.5, 4.4, 3.3} or {16.5, 13.2, 5.5, 4.4, 3.3}
    split_ok = {round(m * r / 100, 4) for m in base for r in rates}
    taxy_new = [raw for raw in taxy_bad
                if not any(_same(v, x) for v, *_ in money_values(raw) for x in split_ok)]
    conflict = bool(key_vals) and has_key and bool(taxy_new)
    if conflict and lead_strict:
        # 결론에 쓴 금액이 다르면 답의 주장 자체가 틀린 것이다 — 끝에 덧붙이는 것으로는 부족하다
        ranges = _lead_ranges(body)
        if any(raw in taxy_new and any(a <= s < b for a, b in ranges) for kind, v, s, raw in items):
            contradiction, conflict, taxy_bad = True, False, taxy_new
    # 블록이 '질문한 방식'(나눠 받기 등)의 값을 따로 밝혔는데 답에 그 값이 없다 — 다른 방식의 합계를 질문한 방식의 답으로 쓴
    # 것이다(10-13 시연 3턴: '한꺼번에든 나눠서든 모두 57만 6천 원'). 쓴 숫자는 모두 블록에 있어 위 판정은 '통과'였다. 시연 채널만.
    asked = [v for b in (calc or {}).get('blocks', []) if b.get('numbers') and b.get('key_figure')
             and '질문한 방식' in b.get('text', '') for v, *_ in money_values(b['key_figure'])]
    if lead_strict and asked and not contradiction and not any(_same(a, k) for a in ans_vals for k in asked):
        contradiction, conflict, taxy_bad = True, False, []
        unmatched.append({'text': taxcalc.won(asked[0]), 'value': None, 'kind': '금액', 'sentence': '질문한 방식의 계산값이 답에 없음'})
    if contradiction or conflict:
        for raw in (taxy_bad if contradiction else taxy_new):
            if not any(u['text'] == raw for u in unmatched):
                unmatched.append({'text': raw, 'value': None, 'kind': '금액', 'sentence': '규칙 계산에 없는 세금 금액'})
    if contradiction:
        action = '계산값으로 대체 · 상담 연결' if MODE == 'handoff' else '계산 확인 덧붙임' if MODE == 'note' else '기록만'
    elif conflict:
        action = '계산 확인 덧붙임' if MODE != 'off' else '기록만'
    elif unmatched:
        action = '주의(미확인 숫자 표시)'
    else:
        action = '통과'
    return {'checked': len(items), 'matched': matched, 'unmatched': unmatched, 'contradiction': contradiction,
            'conflict': conflict, 'action': action, 'key_figures': keys,
            'figures': [f for b in (calc or {}).get('blocks', []) for f in b.get('figures', [])]}


# 문서 밖(모델의 옛 지식)에서 새는 옛 제도 값 — 현행 값으로 바꾼다. 받은 문서는 모두 현행 600·900만 원이다(doc41).
_OUTDATED = [
    (re.compile(r'((?:연금저축|세액\s*공제)[^.\n]{0,25}?(?:최대|한도|연간?)\s*)400\s*만\s*원'), r'\g<1>600만 원'),
    (re.compile(r'((?:IRP|합산|연금저축\s*포함)[^.\n]{0,25}?(?:최대|한도|연간?)\s*)700\s*만\s*원'), r'\g<1>900만 원'),
    # '세액공제 혜택(연간 납입액 400만원 한도' — 금액 바로 뒤에 '한도'가 올 때만(10-13 외부 시험)
    (re.compile(r'((?:연금저축|세액\s*공제)[^.\n]{0,30}?)400\s*만\s*원(?=\s*(?:까지\s*)?한도)'), r'\g<1>600만 원'),
    # '(연금저축 400만 원 + IRP 500만 원 한도 내)'(10-13 외부 시험 43번) — 옛 400 + 500 = 900 → 지금은 연금저축 600, 합산 900
    (re.compile(r'연금저축\s*400\s*만\s*원?\s*\+\s*IRP\s*500\s*만\s*원?'), '연금저축 600만 원 + IRP 300만 원'),
    # '연 1,500만 원 넘으면 종합과세'만 쓰고 16.5% 분리과세 선택을 빼면 틀린 말이 된다(doc38 '종합과세 또는 16.5% 세율로 분리과세')
    # 사이 글에 '3.3~5.5%' 같은 소수점은 허용하고, '초과분만'(분할 과세 오독 — taxcalc.repair 몫)은 건드리지 않는다
    (re.compile(r'(1[,.]?500\s*만\s*원?(?:[^.\n]|(?<=\d)\.(?=\d)){0,40}?(?:초과(?!\s*(?:분|하는\s*(?:금액|부분)))|넘(?:으면|을\s*때|는\s*경우))'
                r'(?:[^.\n]|(?<=\d)\.(?=\d)){0,12}?)종합\s*과세(?![^.\n]{0,25}(?:16\.5|분리\s*과세|선택))'),
     r'\g<1>종합과세(또는 16.5% 분리과세 선택)'),
]


# 나이 한 살과 연금소득세율을 짝지은 문장 — '65세라면 4.4%'(10-13 외부 시험, 65세는 5.5%). 숫자 대조는 4.4%가 문서에
# 있는 값이라 통과시켰다. 나이 한 살(범위 아님) 뒤 짧은 구간에 5.5·4.4·3.3% 하나가 오면 R-07 표(doc38·doc39)로 맞춘다.
# '55세 이상 70세 미만 5.5%'처럼 범위의 끝나이, 종신연금(문서 표의 별도 칸)·부득이 아닌 다른 세목 문장은 건드리지 않는다.
_AGE_RATE = re.compile(r'(?<![\d~∼\-–])(?P<age>[5-9]\d)\s*세(?!\s*(?:미만|이상|이하|초과|부터|까지|~|∼|-|–|·|,|와|과|및|또는|에서))'
                       r'(?P<mid>[^.\n%~∼]{0,24}?)(?P<rate>5\.5|4\.4|3\.3)\s*%')


def fix_age_rate(answer):
    """(고친 답, 고친 곳 수) — 나이 한 살에 붙은 연금소득세율이 R-07 표와 다르면 바로잡는다."""
    n = 0

    def sub(m):
        nonlocal n
        age, mid = int(m.group('age')), m.group('mid')
        if age < 55 or re.search(r'종신|\d\s*세|퇴직|기타소득|분리과세|16\.5', mid):
            return m.group(0)
        r = taxcalc.age_rate(age)
        want = f'{r:g}' if r else None
        if not want or m.group('rate') == want:
            return m.group(0)
        n += 1
        return m.group(0)[:m.start('rate') - m.start(0)] + want + m.group(0)[m.end('rate') - m.start(0):]
    return _AGE_RATE.sub(sub, answer or ''), n


# 나이 구간별 세율 나열('55세 미만: 5.5% · 70세 미만: 4.4% · 80세 미만: 3.3%') — 10-14 실측: DC 연금 수령 세금 답에서 구간을 한 칸씩
# 밀어 적었다(R-07: 55~69세 5.5%, 70~79세 4.4%, 80세 이상 3.3%). 한 줄에 둘 이상 나열된 구간 중 하나라도 틀리면 나열 전체를 표대로 바꾼다
_BAND = re.compile(r'(?:만\s*)?(?P<a>[4-9]\d)\s*세\s*(?:(?:~|∼|-|–)\s*(?:만\s*)?(?P<b>[5-9]\d)\s*세\s*)?(?P<q>미만|이상|이하|초과)?\s*'
                   r'[:：]?\s*(?:\*\*)?(?P<rate>5\.5|4\.4|3\.3)\s*%(?:\*\*)?')
_BAND_OK = '만 55~69세 **5.5%** · 70~79세 **4.4%** · 80세 이상 **3.3%**'


def _band_ok(m):
    a, b, q, rate = int(m.group('a')), m.group('b'), m.group('q'), float(m.group('rate'))
    if b:
        ages = [a, int(b)]
    elif q == '미만':
        ages = [a - 1]
    elif q == '초과':
        ages = [a + 1]
    else:
        ages = [a]
    return all(taxcalc.age_rate(x) == rate for x in ages)


def fix_rate_bands(answer):
    """(고친 답, 고친 곳 수)."""
    out, n = [], 0
    for line in (answer or '').split('\n'):
        ms = list(_BAND.finditer(line))
        runs, cur = [], []
        for m in ms:          # 붙어 있는 나열끼리 묶는다(사이는 '·', ',', '/', '|' 같은 구분자만)
            if cur and not re.fullmatch(r'[\s·,/|;]*(?:및|그리고)?[\s·,/|;]*', line[cur[-1].end():m.start()]):
                runs.append(cur)
                cur = []
            cur.append(m)
        if cur:
            runs.append(cur)
        for run in reversed(runs):
            if len(run) >= 2 and not all(_band_ok(m) for m in run):
                line = line[:run[0].start()] + _BAND_OK + line[run[-1].end():]
                n += 1
        out.append(line)
    return '\n'.join(out), n


# 1,500만 원을 넘으면 '초과분만' 따로 과세한다고 쓴 답 — 10-14 m.Talk 비교 M11·M12: '1,500만 원 초과분(500만 원)만 추가 세율',
# '1,600만 원이면 초과분(100만 원)은 별도 과세'. doc38 '연간 1,500만원 초과 수령 시 전액 … 종합과세 또는 16.5% 분리과세'.
# 문장을 고쳐 쓰기는 어려워 끝에 바로잡는 줄을 단다. '연금수령한도 초과분'(퇴직소득세 그대로)은 다른 규칙이라 건드리지 않는다
_OVER1500 = re.compile(r'1,?500\s*만')
# '그 부분만'도 초과분만 따로 과세한다는 말이다('초과분이 없으면 3~5%예요. 있으면 그 부분만 16.5%예요' — 10차 검토 1023).
# 이 말은 초과분이 아닌 것도 가리킬 수 있어 뒤에 16.5%·종합과세·분리과세·기타소득세가 올 때만 본다(fix_over1500)
_PART_REF = re.compile(r'(?:(?<![가-힣])그\s*(?:부분|만큼)|(?:넘은|넘긴|초과된|초과하는)\s*(?:부분|만큼)|(?:넘는|초과한)\s*만큼)'
                       r'(?:에\s*(?:대해서?|한해)\s*|에\s*)?만')
_PART_ONLY = re.compile(r'초과분|초과\s*(?:금액|부분)(?:에\s*한해|만)|넘는\s*(?:부분|금액)만|초과한\s*(?:부분|금액)만|' + _PART_REF.pattern)
_PART_TAX = re.compile(r'16\.5\s*%|종합\s*과세|분리\s*과세|기타\s*소득')
# '그 부분만 빼고·그 부분만 제외하고·그만큼만 뺀'은 '그'가 앞에서 말한 다른 돈(원금·과세제외금액)을 가리키면 그 돈을 덜어 낸다는
# 말이지 과세 범위 말이 아니다('비과세 원금은 그 부분만 빼고 16.5% 분리과세' — 11차 검토 2009~2012). 다만 '넘은·넘긴·초과하는 부분'은
# 늘 초과분이고, 1,500만 바로 뒤라 가리킬 다른 돈이 없는 '그 부분'('1,500만 원을 넘으면 그 부분만 빼서 16.5%')도 초과분을 떼어
# 과세한다는 말이다 — 동사만 보면 이 오답을 놓친다(11차 재검토 B, _part_other)
_PART_EXCEPT = re.compile(r'[\s*]*(?:을|를)?[\s*]*(?:빼|뺀|뺄|뺍|제외|차감)')
# 1,500만 원을 다른 표기로 쓴 말 — '1천5백만 원'·'천오백만 원'·'15,000,000원'. '1,500만'만 읽으니 이 표기로 다시 부른 기준을
# 다른 수로 봐 바로잡기가 빠졌다(10차 검토 1020). 가르기 전에 '1,500만'으로 맞춰 읽는다 — 답 글자는 그대로 두고 줄만 단다.
# '천5백만·1천오백만'은 여기서 넓히지 않는다 — 넓히니 '1억 천5백만'(1억 1,500만 원)·'평가액 천5백만 원'까지 1,500만 기준으로 읽어
# 맞는 한도 설명에 줄이 붙었다(11차 재검토 D). 제외 마디 뒤 '천5백만 원을 넘으면'은 _REUSE_REF 가 1,500만으로 본다
_ALT_1500 = re.compile(r'(?<![\d,.])1\s*천\s*(?:5\s*백|500)\s*만|(?<![가-힣\d])(?:일\s*)?천\s*오\s*백\s*만|(?<![\d,.])15,?000,?000\s*원')


def _norm_1500(a):
    return _ALT_1500.sub(lambda m: '1,500만 원' if m.group().endswith('원') else '1,500만', a)
OVER1500_NOTE = ('※ 바로잡기: 세액공제 받은 돈·운용수익에서 받은 연금이 1년에 1,500만 원을 넘으면, 넘은 부분만이 아니라 그 연금 전액이 '
                 '종합과세 또는 16.5% 분리과세 대상이에요(받은 문서 「연금소득 종합과세 안내」).')
# 연금수령한도 규칙은 정말 '초과분만' 따로 과세한다(doc39 — 한도 넘는 돈은 연금외수령: 세액공제 재원·운용수익 16.5% 기타소득세,
# 퇴직금 재원 퇴직소득세 그대로). 한도 계산값이 마침 1,500만 원(1억 ÷ (11 − 3) × 120%)이면 근처 '1,500만'만 보고 맞는 한도 설명에
# 바로잡기 줄이 붙었다 — 10-14 결함 28. 그래서 '초과분'이 어느 규칙 이야기인지 그 문장 안에서 가른다.
# 가르는 기준은 '한도'라는 낱말이 아니라 초과분을 나누는 기준이 무엇이냐다 — '분리과세 한도·연금소득 한도 1,500만 원'은
# 1,500만 원 규칙, '연금수령한도(1,500만 원)'·'한도를 넘는'은 한도 규칙(2차 검토 110~112: 낱말로 가르니 앞쪽이 빠졌다)
# '선택적 과세'도 같은 말이다(저장된 답 '초과분에 대해 선택적 과세가 적용됩니다')
_COMP_WORDS = re.compile(r'종합\s*과세|분리\s*과세|선택\s*적?\s*과세|과세\s*방식')      # 1,500만 원 규칙의 말
# 한도 규칙에만 나오는 말(doc39). '기타소득'은 넣지 않는다 — '1,500만 원을 넘으면 초과분은 16.5% 기타소득세'처럼
# 1,500만 원 규칙을 기타소득세로 섞어 쓴 모델 오답이 있어 이 말로 한도 규칙이라 단정하면 그 오답을 놓친다(2차 검토 113·115)
_LIMIT_ONLY = re.compile(r'연금\s*외\s*수령|퇴직\s*소득')
_OUT_RECEIPT = re.compile(r'연금\s*외\s*수령')
# 초과분을 나누는 기준(lim 한도·n 1,500만)과 그 앞에서 기준을 부르는 말(r 1,500만 원 규칙·l 연금수령분). '연금소득세'는 세금 이름이라 뺀다.
# '저율과세 한도'·'사적연금 한도 1,500만 원'도 1,500만 원 규칙을 부른 말이다(doc38 '사적연금소득을 1년에 1,500만원 초과',
# doc20 '연금소득 저율과세' — 3차 검토 240)
# '사적연금소득'은 한 말로 잡는다 — '사적연금'만 잡으면 남은 '소득이 '가 끼어 '사적연금소득이 1,500만'·'사적연금소득 한도'를
# 이름 없는 1,500만·연금수령한도로 봤다(꼬리 분리과세가 먼저 걸러 가려져 있었다, 5차 검토 424 고침 중 확인)
_RULE_NAME = r'사적\s*연금\s*소득(?!\s*세)|연금\s*소득(?!\s*세)|사적\s*연금(?!\s*소득\s*세)|종합\s*과세|분리\s*과세|선택\s*과세|저율\s*과세'
_EXCESS_BASIS = re.compile(r'(?P<r>' + _RULE_NAME + r')'
                           r'|(?P<l>연금\s*수령\s*분)|(?P<lim>한도)|(?P<n>1,?500\s*만)')
_GAP_LIM = re.compile(r'[\s*]*원?[\s*]*(?:합계액?|합산)?[\s*]*(?:의|이라는|라는)?[\s*]*')      # '분리과세 한도'·'1,500만 원의 한도' 사이
# 1,500만 원 규칙 말과 '한도' 사이에 꾸미는 말이 한두 개 끼어도 같은 기준이다('분리과세 가능 한도'·'연금소득 연간 한도'·'종합과세 기준 한도'
# — 2차 고침은 바로 붙은 것만 봐 3차 검토 240 을 놓쳤다). 조사로 끝난 말·'수령'이 낀 말이 오면 끊는다('분리과세를 고르면 한도'·
# '분리과세 대상 연금수령한도')
# '인출·올해·계좌'가 낀 한도도 연금수령한도다 — doc37 '매년 세금 혜택을 받을 수 있는 인출 한도', doc2 '올해 한도 1,200만원',
# doc22 '해당계좌의 연금수령한도'(4차 검토 317: '사적연금 인출 한도·사적연금 계좌 한도 … 초과분 16.5% 기타소득세'를 1,500만 원 기준으로 봤다)
_GAP_QUAL = re.compile(r'(?:[\s*]+(?![^\s*]*(?:수령|인출|올해|계좌))[가-힣]{1,4}(?<![은는이가을를에로와과도만면고며서다요]))'
                       r'{0,2}[\s*]*(?:의|이라는|라는)?[\s*(]*')
# '연금수령한도(연 1,500만'·'한도는 **1,500만'·'한도 안 1,500만'·'연금소득이 1,500만' 사이 — 앞말이 부르는 값이다. 말이 끼면 그냥 1,500만.
# 표 칸(|)은 넘지 않는다 — 저장된 답에 1,500만 원 규칙을 '| 연금 수령 한도 내 | 1,500만원 |'으로 잘못 이름 붙인 표가 있다
# '연금소득 기준 1,500만'·'연금소득 합계가 1,500만'·'사적연금소득이 1년에 1,500만'·'연금소득(사적연금)이 1,500만'처럼 꾸밈말이 끼어도
# 앞말이 부르는 값이다 — 끊으면 이름 없는 1,500만이 돼 한도가 1,500만인 calc 에서 연금수령한도로 봤다(6차 검토 517~524 와 같은 원인)
_GAP_1500 = re.compile(r'[\s*`(:=)]*(?:안|내|이내)?[\s*`(:=]*(?:(?:기준|합계|합산|총액)\s*(?:액|인|이|은|는)?)?[\s*`(:=]*'
                       r'(?:은|는|이|가|도)?[\s*`(:=]*(?:연간|연|매년|1\s*년에|한\s*해에?)?[\s*`(]*')
# 답이 연금수령한도 값을 1,500만 원이라고 밝혔는가 — '연금수령한도는 1,500만 원'·'연금수령한도인/액/약 1,500만'·
# '| 연금수령한도 | 1,500만원 |'·'× 120% = 1,500만 원'·'1,500만 원(연금수령한도)'. 밝힌 답에서는 이름 없이 쓴 1,500만이 그 한도다
# (3차 검토 231~239·241: 2차 고침이 이름 없는 1,500만을 모두 1,500만 원 기준으로 봐 맞는 한도 답에 줄이 붙었다).
# '연금 한도 내 | 1,500만원'처럼 '수령한도'라 부르지 않았거나 '한도 초과 … 1,500만'처럼 사이에 다른 말이 끼면 밝힌 것이 아니다
# (2차 115 — 1,500만 원 규칙을 한도라 잘못 이름 붙인 표)
# '연간 연금수령 한도는 1,500만 원'·'연금 수령 한도: 연 1,500만원'·'연금수령한도(1,500만원/연)'·'(최대 1,500만원) 내'처럼
# 해마다 같은 기준으로 쓰거나, 같은 문장에서 넘으면 종합과세·분리과세라고 한 것은 1,500만 원 규칙을 한도라 잘못 부른 말이다
# (저장된 답 _a1_ab·_answer_*_cons 등 30곳) — 계산한 한도 값을 밝힌 것으로 보지 않는다. 쉼표·가운뎃점이 끼면 나열이라 끊는다
_DECL_LIMIT = re.compile(r'수령\s*한도(?:\s*금?액)?(?P<g>[^\d\n.?!,·;]{0,12}?)\**\s*(?:약\s*)?1,?500\s*만'
                         r'(?P<t>\s*원?\s*[)*\]]*\s*(?:/\s*(?:연|년)|까지|이내|이하|미만|내))?'
                         r'|(?:120\s*%|1\.2)[^\n]{0,30}?[=≈]\s*\**\s*(?:약\s*)?1,?500\s*만'
                         r'|1,?500\s*만\s*원?\s*(?:은|는|이|가)?\s*[(\[]?\s*\**\s*(?:올해\s*)?(?:연금\s*)?수령\s*한도(?!\s*(?:초과|를?\s*넘|안|내|이내|까지))')
_DECL_BREAK = re.compile(r'초과|넘|안|내|이내|까지|이하|미만|이상|최대|중|외|밖|별개|상관|관계|달리|일반|연금\s*소득|종합\s*과세|분리\s*과세|'
                         r'연간|매년|(?<![가-힣])연(?![가-힣])')


def _declares_limit_1500(a):
    a = a or ''
    for m in _DECL_LIMIT.finditer(a):
        g = m.group('g')
        if g is None:
            return True                       # 120% 계산식이 낸 값
        if _DECL_BREAK.search(g) or m.group('t') or re.search(r'연간|매년', a[max(0, m.start() - 8):m.start()]) or \
                _COMP_WORDS.search(_sentence(a, m.end())):
            continue
        return True
    return False


def _basis_kinds(s, declared=False):
    """s 안의 기준 말을 차례로 [(끝 위치, 종류, 갈래, 처음 갈래)] — 종류 R 1,500만 원 규칙·L 한도 규칙·B 그냥 1,500만(이름 없이 1,500만 원으로 나눔).
    declared: 답이 연금수령한도를 1,500만 원이라고 밝혔다 — 이름 없는 1,500만은 B 가 아니라 그 한도(L)다.
    처음 갈래: 앞말에서 종류를 물려받았으면 그 사슬의 첫 말 갈래 — 'r'이면 연금소득·사적연금소득처럼 1,500만 원 규칙 말로 부른 기준이고,
    'n'이면 '1,500만 원 한도'처럼 이름 없는 1,500만을 한도라 부른 것이다(6차 검토 517~524에서 둘을 가른다)."""
    out = []
    for t in _EXCESS_BASIS.finditer(s):
        prev = out[-1] if out else None
        org = t.lastgroup
        if t.group('r'):
            k = 'R'
        elif t.group('l'):
            k = 'L'
        elif t.group('lim'):
            # 바로 앞이 1,500만 원 규칙 말이나 1,500만이면 그 기준을 한도라 부른 것('분리과세 한도'·'1,500만 원 한도'), 아니면 연금수령한도
            k = 'R' if prev and (prev[2] == 'r' and _GAP_QUAL.fullmatch(s, prev[0], t.start()) or
                                 prev[1] in 'RB' and _GAP_LIM.fullmatch(s, prev[0], t.start())) else 'L'
            if k == 'R':
                org = prev[3]
        elif re.search(r'120\s*%', s[:t.start()]):
            k = 'L'           # 한도 계산식(평가액 ÷ (11 − 연차) × 120%)이 낸 값
        elif prev and _GAP_1500.fullmatch(s, prev[0], t.start()):
            k, org = prev[1], prev[3]
        elif any(x[2] == 'n' and x[1] == 'R' for x in out):
            # 같은 문장에서 앞서 연금소득 등으로 부른 1,500만을 이름 없이 다시 쓴 것이다('연금소득이 1,500만 원 이하라 …,
            # 1,500만 원 이상이면 그 부분만 16.5%' — 10차 검토 1023: 한도가 1,500만인 calc 에서 뒤의 것을 연금수령한도로 봤다)
            k, org = 'R', next(x[3] for x in reversed(out) if x[2] == 'n' and x[1] == 'R')
        else:
            k = 'L' if declared else 'B'
        out.append((t.end(), k, t.lastgroup, org))
    return out


def _sent_head(a, i, back=80):
    # 문장 끝은 위의 _SENT_END(16.5%·82.5만의 점은 끝이 아님)를 그대로 쓴다. 1차 수정이 같은 이름으로 다시 정의해
    # 숫자 대조의 _sentence 까지 HEAD 와 다르게 잘랐다
    head = a[max(0, i - back):i]
    cuts = [x.end() for x in _SENT_END.finditer(head)]
    return head[cuts[-1]:] if cuts else head


def _excess_tail(a, m):
    """'초과분' 뒤 같은 문장(최대 80자) — 초과분에 어떤 세금이 붙는다고 했는지 본다."""
    tail = a[m.end():m.end() + 80]
    e = _SENT_END.search(tail)
    return tail[:e.start()] if e else tail


# 기타소득세·연금외수령·퇴직소득세에 붙은 '분리과세'·'과세 방식'은 과세 방식 선택이 아니라 한도 규칙 말이다 — 기타소득은 금액 제한 없이
# 무조건 분리과세(doc21 33행, doc2 30행), 연금외 수령 시 기타소득으로 분리과세(doc36 24행), doc22 '연금수령한도 外 → 기타소득세16.5%(분리과세)',
# 퇴직금 재원도 분리과세(doc39 35행). 그래서 꼬리에 이 세금이 있으면 종합과세나 '선택'을 말할 때만 1,500만 원 규칙으로 본다.
# 4차 고침은 '기타소득세 16.5%(분리과세)' 괄호 꼴만 빼 '한도 초과분 500만 원은 16.5% 기타소득세로 분리과세돼요'에 줄을 달았다(5차 검토 424)
_LIMIT_TAX = re.compile(r'기타\s*소득|연금\s*외\s*수령|퇴직\s*소득')
_CHOICE_WORDS = re.compile(r'종합\s*과세|선택|고르|골라')


def _tail_comp(tail):
    """초과분 뒤에서 종합과세·분리과세(1,500만 원 규칙의 과세 방식)를 말하는가."""
    if _LIMIT_TAX.search(tail):
        return bool(_CHOICE_WORDS.search(tail))
    return bool(_COMP_WORDS.search(tail))


# 기준이 아니라 초과분 금액으로 쓴 1,500만 — '1,500만 원이 초과분이에요'·'1,500만 원가량이 초과분'(뒤), '2,700만 원 − 1,200만 원 = 1,500만 원'·
# '초과분은 1,500만 원이에요'(앞). 한도 1,200만·인출 2,700만이면 초과액이 마침 1,500만 원이라, 앞 문장에서 이렇게 밝힌 뒤 다음 문장에서
# '이 초과분은 16.5% 기타소득세'로 받으면 앞 1,500만을 이름 없는 기준(B)으로 봐 맞는 한도 답에 줄이 붙었다(6차 검토 526·527 A10a·d·e)
_AMT_AFTER = re.compile(r'[\s*]*원?[\s*]*(?:가량|정도|쯤|남짓)?[\s*]*(?:은|는|이|가|\()[\s*]*(?:한도[\s*]*)?초과\s*(?:분|액|금액|부분)')
_AMT_BEFORE = re.compile(r'(?:(?:원|만)[\s*]*[−\-–][^=≈\n]{0,24}[=≈]|초과\s*(?:분|액|금액)\s*(?:은|는|이|가|:)?)[\s*(]*(?:약[\s*]*)?$')
# 기준 뒤에 '…과는 별개·상관없이·달리'가 붙으면 그 1,500만으로 나눈 초과분이 아니다('연 1,500만 원 종합과세 기준과는 별개로 초과분은 연금외수령')
_APART_AFTER = re.compile(r'[\s*]*원?[\s*]*(?:(?:종합\s*과세|분리\s*과세|저율\s*과세|과세|기준|한도|규칙|요건|선)[\s*]*){0,2}'
                          r'(?:과|와)(?:는|도)?[\s*,]*(?:별개|별도|상관\s*없|관계\s*없|무관|달리|다르|다른)')
# 이름 없는 1,500만을 나누는 기준으로 썼는가('1,500만 원을 넘는·초과분·까지·기준을 넘으면') — '연금 1,500만 원 중 초과분'처럼 쪼개는 금액이면 아니다.
# '웃돌면·상회하면'도 '넘으면'과 같은 말이다(10차 검토 1023 '1,500만 원을 웃돌면 그 부분만')
_B_THRESH = re.compile(r'[\s*]*원?[\s*]*(?:의[\s*]*)?(?:(?:한도|기준|선)[\s*]*)?(?:을|를|이|가|은|는|으로)?[\s*]*'
                       r'(?:넘|초과|웃돌|웃도|상회|까지|이하|이내|미만|이상|기준)')
# 인출액·빼는 돈이 1,500만을 넘는다는 말 — 꼬리가 연금외수령·퇴직소득이면 한도 규칙 설명으로 둔다('인출액이 1,500만 원을 넘으면 초과분은
# 연금외수령'). 꼬리가 그냥 16.5%·기타소득세면 이 주어도 1,500만 원 규칙 말로 흔히 쓰여(저장된 답 '인출 금액이 연간 1,500만원을 초과하면
# 종합과세 또는 16.5%' 18곳) 그대로 1,500만 원 규칙으로 본다
_WITHDRAW_SUBJ = re.compile(r'(?:인출\s*(?:액|금액)|(?:인출하|빼|꺼내)는\s*(?:돈|금액))[\s*]*(?:이|가|은|는)?[\s*]*'
                            r'(?:연간|연|매년|1\s*년에|한\s*해에?)?[\s*]*$')
# 1,500만 원 판단에서 무엇이 빠지는지 말한 1,500만 — '퇴직금은 사적연금소득 1,500만 원 판단에 포함되지 않고'·'연금외수령분은
# 1,500만 원에 합산되지 않고'·'1,500만 원을 판단할 때 퇴직금은 빼고'(doc38 10행 '이 1,500만원을 판단할 때 … 퇴직금은 포함되지
# 않는다'). 이 1,500만은 초과분을 나눈 기준이 아니라서 뒤 '초과분은 퇴직소득세·연금외수령'은 한도 초과분 이야기다(7차 검토 622~629:
# 6차 고침이 이름 붙인 1,500만을 넘·초과 없이도 기준으로 봐 이런 맞는 문장에 줄이 붙었다).
# 제외 말은 잇거나 끝맺는 꼴만 본다 — '1,500만 원에 포함되지 않는 초과분'·'들어가지 않으면'은 1,500만을 넘는 부분을 부른 말이다.
# 1,500만 바로 뒤가 '에'나 판단 말('판단·계산·기준·따질 때')이어야 한다 — '1,500만 원을 빼면'·'1,500만 원은 과세에서 빠지고'는
# 1,500만 자체를 빼는 말이다. 사이에 '넘·초과·까지·안에'가 낀 말('1,500만 원을 넘으면 저율과세에서 빠지고')도 제외 말이 아니다
_EXCL_WORD = re.compile(r'(?:(?:포함|합산|산입|반영)\s*(?:되|하|시키)?|들어가|넣|합치)지\s*(?:는|도)?\s*않'
                        r'(?:고|으며|으니|으므로|으나|아|았|습|음|기|는다|지만|죠|거든)'
                        r'|(?:포함|합산|산입|반영)\s*(?:이|은|도)?\s*안\s*(?:되(?:고|며|니|어|요)|돼|됩)|안\s*들어가(?:고|며|니|요|서)|안\s*들어갑'
                        r'|빼(?:고|며|서|요|죠)|뺍니다|뺀다|빠지(?:고|며|니|므로|죠|지만)|빠져(?:요|서)|빠집니다|빠진다'
                        r'|제외\s*(?:되|하)(?:고|며|니|므로|어|여|지만)|제외\s*(?:돼|해)|제외\s*(?:됩|합)|제외\s*(?:된|한)다|불포함')
# '1,500만 원에 이르면·달하면·해당하면'도 기준에 닿는 조건이다 — 그 뒤 '퇴직금은 빼고'는 판단에서 빠지는 말이 아니다(8차 검토 729)
_EXCL_STOP = re.compile(r'넘|초과|이상|이하|미만|까지|이내|내에|안에|안으로|남|이면|라면|되면'
                        r'|이르[면는]|이를\s*(?:때|경우)|달하[면는]|달할\s*(?:때|경우)|해당하[면는]|해당할\s*(?:때|경우)')
_EXCL_HEAD = re.compile(r'[\s*]*원?[\s*]*(?:에|(?:을|를|은|는|의)?[\s*]*(?:판단|계산|기준|합산|산정|요건|따지|따질|셀\s*때|볼\s*때))')
# 제외 말 뒤 초과분 앞에서 '넘으면·초과하면·남는·이보다 많은·그 이상·웃도는'으로 다시 기준을 부르면 그 1,500만은 여전히 나누는 기준이다
# ('1,500만 원에서 퇴직금은 빼고 보는데, 넘으면 초과분만 16.5%', 8차 검토 730 '이보다 많은 초과분')
_EXCL_REUSE = re.compile(r'넘[으는은어게기]|초과\s*(?:하|한|해|되|된|시)|남[는은아]|나머지|보다\s*(?:많|큰|더)|이상|웃도|웃돌|상회')
# 그 말이 무엇을 넘는지(비교 대상)는 바로 앞말로 본다 — 연금 답에 흔한 '10년 이상·55세 이상·5년 이상 가입·한도를 넘으면'은
# 1,500만이 아니라 그 앞 수·이름을 넘는 말이다(9차 검토 813~815: 맨 '이상'을 다시 부른 말로 봐 맞는 한도 설명에 줄이 붙었다).
# 대명사('그 이상·이를 웃도는·이보다 많은·그 금액을 넘는')나 1,500만·'기준'이 앞이면 1,500만을 다시 부른 것이다.
# '기준치·기준선·기준점'(과세 기준선·판단 기준치)도 '기준'이다 — 1차 고침이 이를 다른 이름+을/를로 넘겼다(11차 재검토 A).
# '천5백만·1천오백만 원을'도 1,500만이다(_ALT_1500 은 넓히지 않고 여기서만 본다 — 이름+을/를로 넘기면 고치기 전에 잡던 것을 놓친다)
_REUSE_REF = re.compile(r'(?:(?<![가-힣])(?:그|이)(?:것|거)?|(?<![가-힣])(?:그|이|해당)\s+(?:금액|기준|선|액수)|기준(?:\s*금?액|[치선점값])?'
                        r'|(?:1,?500|천\s*오백|(?<![\d가-힣])천\s*5\s*백)\s*만[\s*]*원?(?:[\s*]*선)?)'
                        r'[\s*]*(?:을|를|보다|에|이|가|은|는)?[\s*]*$')
# 수(10년·55세·5년·1,200만 원·평가액의 120%)가 앞이면 그 수를 넘는 말이다
_REUSE_QTY = re.compile(r'(?<![\d,])(\d[\d,.]*)[\s*]*(?:만[\s*]*원?|억[\s*]*원?|천[\s*]*원?|원|%|[가-힣]{1,2})?[\s*]*'
                        r'(?:을|를|이|가|은|는|도|보다)?[\s*]*$')
# 지시어가 이끄는 비교 대상('위 금액·이 값·그 상한·이 기준치·그 기준선·이 수준·앞의 금액·위에서 말한 금액·그만큼')은 앞에서 말한 1,500만이다
# (10차 검토 1019: 9차 고침이 '그 금액·그 선'만 알고 나머지는 이름+을/를로 봐 '위 금액을 넘으면 초과분만 16.5%'를 넘겼다).
# '그 한도'만은 앞에 한도라 부른 말이 있으면 그 한도를 가리킨다(_reuse_1500)
_REUSE_DEMO = re.compile(r'(?:(?<![가-힣])(?:그|이|위|앞|앞의|(?:앞서|(?:위|앞)에서)\s*(?:말한|든|본|언급한)|해당|상기)[\s*]+(?P<n>[가-힣]{1,4}?)'
                         r'|(?<![가-힣])(?:그|이)만큼)[\s*]*(?:을|를|보다|에|이|가|은|는)?[\s*]*$')
# 지시어 없이 이름+을/를('수령 가능 금액을·연금 수령 상한을·정해진 수령 범위를 넘으면')은 그 이름을 넘는 말이다 — 연금수령한도를 달리
# 부른 말이 많아, 10차 고침처럼 이를 1,500만으로 보면 맞는 한도 설명에 줄이 붙었다(11차 검토 2000~2008). 1,500만을 다시 부른 것으로
# 보는 것은 지시어(_REUSE_DEMO)·'기준'·1,500만(_REUSE_REF)과 1,500만 원 규칙 이름('연금소득을'·'분리과세 한도를')뿐이다.
# '한도를'은 그 한도가 연금수령한도면 한도 규칙 말이다(_reuse_1500)
_REUSE_OBJ = re.compile(r'[가-힣][\s*]*(?:을|를)[\s*]*$')
# 1,500만 원 규칙 이름에 꾸밈말이 한두 개 붙은 것('분리과세 상한을·연금소득 합산액을·분리과세 가능 금액을·사적연금 수령액 합계를')과
# 합산·합계·총액으로 부른 것('연간 합산액을·수령 총액을')도 1,500만이다 — 연금수령한도는 계좌마다 정해 합산하지 않는다. 1차 고침은 규칙 이름 바로 뒤
# '금액·합계액'만 알아 나머지를 다른 이름으로 넘겼다(11차 재검토 A). 꾸밈말에 인출·올해·계좌가 끼면 연금수령한도다(_GAP_QUAL 과 같다)
_REUSE_RULE = re.compile(r'(?:(?:' + _RULE_NAME + r')(?:[\s*]*(?![^\s*]*(?:인출|올해|계좌))[가-힣]{1,4}'
                         r'(?<![은는이가을를에로와과도만면고며서다요])){0,2}|(?:합산|합계|총액|총합)[\s*]*(?:금?액)?)[\s*]*(?:을|를)[\s*]*$')
_REUSE_LIM = re.compile(r'한도[\s*]*(?:을|를|보다)[\s*]*$')
_DEMO_LIM = re.compile(r'(?<![가-힣])(?:그|이|위|앞의|해당)[\s*]*$')       # '그 한도·이 한도'의 지시어
_REUSE_OTHER = re.compile(r'(?:나이|연령|기간|연차|햇수|횟수|기한|만기)[\s*]*(?:을|를|보다)[\s*]*$')


def _lim_is_limit(a, j):
    """a[:j] 끝의 '한도'가 연금수령한도(L)인가 — '분리과세 한도'처럼 1,500만 원 규칙을 한도라 부른 말이면 아니다."""
    ks = [x for x in _basis_kinds(_sent_head(a, j, 160)) if x[2] == 'lim']
    return bool(ks) and ks[-1][1] == 'L'


def _reuse_1500(a, i, j):
    """a[i:j](제외 말 뒤 ~ 초과분 앞)에서 1,500만을 다시 기준으로 부르는가 — '넘으면·그 이상·이보다 많은·이를 웃도는·위 금액을 넘으면'.
    '이상·…보다 많은'은 비교 대상이 꼭 앞에 와서 대명사·1,500만일 때만, '넘·초과·웃돌·남는'은 비교 대상이 다른 수·연금수령한도·
    나이·기간이 아니면(생략된 비교 대상은 앞에서 말한 1,500만) 다시 부른 것으로 본다."""
    for m in _EXCL_REUSE.finditer(a, i, j):
        lo = max(i, m.start() - 16)       # 앞말은 제외 말 뒤에서만 본다(잘라 내지 않고 pos 로 줘 앞 글자 확인이 맞게)
        d = _REUSE_DEMO.search(a, lo, m.start())
        if d:
            if d.group('n') and d.group('n').endswith('한도'):
                # '그 한도'는 앞에 한도라 부른 말이 있으면 그것, 없으면 앞에서 말한 1,500만이다
                prev = [x for x in _basis_kinds(_sent_head(a, d.start(), 160)) if x[2] == 'lim']
                if prev and prev[-1][1] == 'L':
                    continue
            return True
        if _REUSE_REF.search(a, lo, m.start()):
            return True
        q = _REUSE_QTY.search(a, lo, m.start())
        if q and not re.fullmatch(r'1,?500', q.group(1)):
            continue
        if m.group() == '이상' or m.group().startswith('보다'):
            continue
        if not m.group().startswith(('남', '나머지')):
            if _REUSE_OTHER.search(a, lo, m.start()):
                continue
            o = _REUSE_LIM.search(a, lo, m.start())
            if o:
                if _lim_is_limit(a, o.start() + 2):
                    continue
            elif _REUSE_OBJ.search(a, lo, m.start()) and not _REUSE_RULE.search(a, lo, m.start()):
                continue          # 지시어 없는 다른 이름을 넘는 말
        return True
    return False


def _excl_word_end(a, e):
    """a[:e] 끝의 1,500만 바로 뒤('에'·'판단·계산·기준') 같은 문장에 오는 제외 말('포함되지 않고·빼고')의 끝 위치 — 없으면 None."""
    w = a[e:e + 40]
    x = _SENT_END.search(w)
    if x:
        w = w[:x.start()]
    x = _EXCL_WORD.search(w)
    if not x or not _EXCL_HEAD.match(w, 0, x.start()) or _EXCL_STOP.search(w, 0, x.start()):
        return None
    return e + x.end()


def _excluded_1500(a, e, upto=None):
    """a[:e] 끝의 1,500만이 '…은 1,500만 원 판단에 포함되지 않고'처럼 1,500만 원 판단에서 빠지는 것을 말한 자리인가.
    upto: 초과분 자리 — 제외 말 뒤 거기까지 1,500만을 다시 기준으로 부르면 아니다."""
    xe = _excl_word_end(a, e)
    if xe is None:
        return False
    return upto is None or upto <= xe or not _reuse_1500(a, xe, upto)


# 제외 마디의 주어가 한도 초과로 뺀 돈인가 — '연금수령한도를 넘는 금액은·한도 초과로 인출한 돈은·연금외수령분은 연금소득 1,500만 원에
# 포함되지 않고'. 그러면 뒤 '초과분은 16.5% 기타소득세'는 그 돈(한도 초과분) 설명이다(doc39, 7차 검토 625~628).
# '한도'는 종류로 가른다 — '분리과세 한도 1,500만 원'은 1,500만 원 규칙을 한도라 부른 말이라 주어가 아니다
_EXCL_LIMIT_MONEY = re.compile(r'연금\s*외\s*수령|인출\s*(?:액|금액)|인출(?:한|된|하는)\s*(?:돈|금액)')
# 한도 초과분에 붙는 세금 — 세액공제 재원·운용수익은 16.5% 기타소득세, 퇴직금 재원은 퇴직소득세(doc39). 꼬리가 16.5% 분리과세·종합과세면
# _tail_comp 가 먼저 1,500만 원 규칙으로 걸러 여기까지 오지 않는다
_EXCL_LIMIT_TAIL = re.compile(r'기타\s*소득|연금\s*외\s*수령|퇴직\s*소득|16\.5\s*%')
# 초과분 꼬리가 세금을 말하는가 — 말하지 않으면('초과분이 생기는지는 세액공제 받은 돈과 운용수익으로만 봐요') 바로잡을 주장이 없다
_EXCESS_TAX = re.compile(r'\d+(?:\.\d+)?\s*%|과세|세율|세금|소득\s*세|징수|기타\s*소득')


def _excl_limit(a, e, tail):
    """a[:e] 끝의 제외 마디 1,500만('퇴직금은 1,500만 원 판단에 포함되지 않고') 뒤 초과분이 한도 규칙 이야기인가.
    제외 마디의 1,500만도 1,500만 원 규칙을 부른 말이다 — 뒤 마디가 '초과분(만) 16.5%·기타소득세'면 넘은 부분만 과세한다는
    오답이다(doc38 22행 '초과하는 금액에 대해 16.5%가 아니라 전체금액에 대해', 8차 검토 719~728: 7차 고침이 문장째 넘겨 이를 놓쳤다).
    한도 규칙 말(퇴직소득세·연금외수령)이 꼬리에 오면 앞에서 이미 걸렀다. 여기서는 주어가 한도 초과로 뺀 돈이고 꼬리가
    기타소득세·16.5%면 한도 설명으로 둔다('연금수령한도를 넘는 금액은 … 포함되지 않고, 초과분만 16.5%예요' — 그 돈의 세금이다)."""
    s = _span_1500(a, e) or e
    head = _sent_head(a, s)
    cuts = [c.end() for c in re.finditer(r'[,;](?!\d)', head)]     # 마디 경계 — '1,200만'의 쉼표는 아니다
    head = head[cuts[-1]:] if cuts else head
    sent = _sent_head(a, s, 300)        # 이어받기는 문장 머리까지 본다(마디는 위처럼 80자 안)
    xe = _excl_word_end(a, e) or e
    if _EXCL_LIMIT_TAIL.search(tail):
        # 한도 설명으로 넘기는 것은 제외 마디의 주어가 연금수령한도를 넘은 돈일 때뿐이다. 마디 안 어딘가에 연금외수령·한도라는
        # 말이 있는지가 아니라 주어(와 그 꾸밈말)로 본다 — '연금외수령과 달리 퇴직금은 1,500만 원 …'의 주어는 퇴직금이다(10차 검토 1021)
        x = None
        for x in _EXCL_WORD.finditer(a, e, xe):
            pass
        for seg in (head, a[e:x.start() if x else xe]):      # 1,500만 앞, 또는 1,500만과 제외 말 사이('1,500만 원 판단에서 연금외수령분은 빠지고')
            subj = _own_subject(seg, last=True)
            if subj:
                return _over_limit_money(_subject_phrase(seg, subj)) or not _EXCESS_TAX.search(tail)
        if _over_limit_money(head):
            return True       # 토씨 없이 앞에 둔 말('연금외수령분 1,500만 원 판단에 …'·'### 연금외수령' 머리말)
        # 제외 마디에 제 주어가 없거나 '이 금액은·이 돈은'처럼 앞말을 가리키면 주어는 쉼표 앞이나 앞 문장에 있다 —
        # '연금수령한도를 넘는 금액은, 연금소득 1,500만 원 …'·'한도를 넘겨 인출하면 연금외수령이에요. 이 금액은 1,500만 원 …'
        # (9차 검토 816·817: 마디만 봐 주어를 잃고 맞는 한도 설명에 줄이 붙었다). 이어받는 쪽은 그 주어(없으면 그 말 전체)가
        # 한도를 넘은 돈이라고 밝힌 말만 믿는다 — '퇴직금은 연금외수령과 달리,'의 주어는 퇴직금이다
        for seg in (sent[:len(sent) - len(head)], _prev_sentence(a, s - len(sent))):
            subj = _own_subject(seg)
            if _over_limit_money(seg[:subj.end()] if subj else seg):
                return True
            if subj:
                break
    return not _EXCESS_TAX.search(tail)


# 마디의 주어(이름+은/는/이/가/도)와 앞말을 가리키는 주어('이 금액은·그 돈은·이렇게 뺀 돈은·이는').
# '그래도·또는·혹은'은 이음말, '-지는/-지도'는 제외 말의 일부, '…에는·때는·경우는'은 때·자리를 든 말이라 주어가 아니다.
# '한도·제도·정도·나이'처럼 토씨 꼴로 끝나는 이름도 주어가 아니다('연금수령한도를'의 앞 '한도'를 주어로 읽어 이어받기를 끊었다)
_CLAUSE_SUBJ = re.compile(r'(?<![가-힣])(?!(?:그래도|또는|혹은)(?![가-힣]))[가-힣]*[가-힣](?<!지)(?:은|는|이|가|도)(?![가-힣])')
_NOT_SUBJ = re.compile(r'(?:에|에서|때|경우|경우에|땐|면)(?:는|도)$|(?:한도|제도|정도|연도|년도|용도|평가|물가|나이|차이)$'
                       r'|^(?:같이|많이|없이|굳이|함께|따로)$')
# '…이/가 아니다·되다'의 '이/가'는 주어가 아니라 보어다 — '연금외수령이 아닌 퇴직금은'의 주어는 퇴직금이다(10차 검토 1021·1022)
_COMPLEMENT = re.compile(r'[\s*]*(?:아니|아닌|아닙|아냐|되|돼|됩|된|될)')
_DEMO_SUBJ = re.compile(r'(?<![가-힣])(?:(?:이|그|이런|그런|이러한|그러한|이렇게|그렇게|이처럼|그처럼|해당|위)[\s*]+'
                        r'(?:[가-힣]+[\s*]+){0,2}?[가-힣]*(?:금액|돈|부분|초과분|인출액|인출분|분|것|거|연금)[\s*]*(?:은|는|이|가|도)'
                        r'|이것[은이도]|그것[은이도]|이건|그건|이는|이게|그게)(?![가-힣])')


# 표 칸·목록 머리말('| 퇴직금 |'·'- 퇴직금: '·'**퇴직금**')은 토씨 없이 주어 자리다 — 이를 주어 없음으로 읽으면 윗줄
# '| 연금외수령 | 16.5% |'를 이어받는다
_LABEL_SUBJ = re.compile(r'[가-힣)][\s*]*[:：|]|\*\*[^*\n]{1,20}\*\*')


def _subjects(seg):
    """seg 의 주어들(앞말을 가리키는 주어·보어 '이/가 아닌' 빼고) — 앞말을 가리키는 주어는 따로 [(시작, 끝)]."""
    demo = [(m.start(), m.end()) for m in _DEMO_SUBJ.finditer(seg)]
    subj = [m for m in _CLAUSE_SUBJ.finditer(seg)
            if not _NOT_SUBJ.search(m.group()) and not any(ds <= m.start() and m.end() <= de for ds, de in demo)
            and not (m.group()[-1] in '이가' and _COMPLEMENT.match(seg, m.end()))]
    return subj + list(_LABEL_SUBJ.finditer(seg)), demo


def _own_subject(seg, last=False):
    """seg 의 앞말을 가리키지 않는 첫 제 주어(match, 머리말 포함) — 없으면 None. last: 마지막 주어(제외 마디는 1,500만에 가장
    가까운 주어가 그 마디의 주어다 — '퇴직금은 퇴직소득세고 연금외수령분은 1,500만 원 …')."""
    subj = _subjects(seg)[0]
    if not subj:
        return None
    return max(subj, key=lambda m: m.start()) if last else min(subj, key=lambda m: m.start())


# 마디를 잇는 끝말('…고·…며·…지만·…는데') — 그 앞은 다른 마디라 주어 꾸밈말이 아니다. 주어 말('…은/는')로 자르면 '넘는·받은' 같은
# 꾸밈꼴까지 주어로 읽어 '연금수령한도를 넘는 금액은'의 꾸밈말을 잃는다
_JOIN_END = re.compile(r'[가-힣](?:고|며|지만|는데|은데|니까|므로|라서|어서|아서|해서)[\s*]+')


def _subject_phrase(seg, subj):
    """주어 subj 와 그 앞 꾸밈말 — 같은 마디(잇는 끝말 뒤)에서 subj 끝까지."""
    cuts = [m.end() for m in _JOIN_END.finditer(seg, 0, subj.start())]
    return seg[cuts[-1] if cuts else 0:subj.end()]


# 한도 넘은 돈을 부른 말 뒤의 부정·대조 — '연금외수령이 아닌·연금외수령과 달리·연금외수령분을 뺀·연금외수령 말고·
# 연금외수령에 해당하지 않는·연금수령한도를 넘지 않는·한도 밖 인출이 아닌'. 9차 고침은 주어 앞 꾸밈말의 이 말만 보고 부정을 놓쳐
# '연금외수령과 달리 퇴직금은 …'·'연금외수령이 아닌 퇴직금은 …'을 한도 설명으로 넘겼다(10차 검토 1021·1022).
# 부정은 그 말 뒤 12자 안, 같은 마디(쉼표·잇는 끝말까지)에서만 본다 — 더 뒤의 '않'은 다른 풀이말의 것이다
_NEG_WORD = re.compile(r'아닌|아니|않|말고|달리|다르|다른|외에|이외|해당\s*안')
_NEG_STOP = re.compile(r'[,;.]|[가-힣](?:고|며|면|지만|는데|은데|니까|므로|라서|어서|아서|해서)[\s*]')
# '빼다·제외'는 이름 바로 뒤(목적어)일 때만 부정이다 — '연금외수령분을 뺀 나머지'는 빼고 남은 돈, '한도를 넘겨 뺀 돈'은 인출한 돈이다
_NEG_EXCEPT = re.compile(r'(?:분|액|금액)?[\s*]*(?:을|를)?[\s*]*(?:뺀|빼고|빼면|제외)')
_OVER_LIM = re.compile(r'[\s*(]*(?:(?:연\s*)?[\d,.]+\s*만\s*원?[\s*)]*)?(?:를|을|이|가|보다)?[\s*]*(?:넘|초과|웃돌|웃도|벗어|밖|외|많)')


def _negated(seg, j):
    w = seg[j:j + 12]
    c = _NEG_STOP.search(w)
    return bool(_NEG_WORD.search(w[:c.end()] if c else w))


# 연금외수령을 견주거나 함께 넣은 말 — '연금외수령처럼·연금외수령과 마찬가지로·같이·동일하게·여부와 관계없이·연금외수령이든·
# 연금외수령도 포함해·연금외수령분까지 합쳐·연금외수령 시 적용되는 세율이'. 이 문장이 연금외수령 이야기라는 뜻이 아니다(11차 재검토 C)
_LIKE_WORD = re.compile(r'처럼|마찬가지|같이|같은|동일|관계\s*없|상관\s*없|무관|여부|이든|든지|포함|까지|적용')


def _likened(seg, j):
    """seg[j:] 바로 뒤(같은 마디 12자)가 견줌·포함 말인가 — 범위는 _negated 와 같다."""
    w = seg[j:j + 12]
    c = _NEG_STOP.search(w)
    return bool(_LIKE_WORD.search(w[:c.end()] if c else w))


_WHOLE = re.compile(r'전액|전체')


def _whole_stated(a, n):
    """1,500만 n 뒤 같은 문장에서 '전액·전체'(1,500만 원 규칙의 맞는 말)를 부정 없이 말했는가."""
    e = _SENT_END.search(a, n.end())
    seg = a[n.end():e.start() if e else len(a)]
    return any(not _negated(seg, x.end()) for x in _WHOLE.finditer(seg))


def _over_limit_money(seg):
    """seg 가 연금수령한도를 넘은 돈을 말하는가 — '연금외수령·인출액'이나 연금수령한도(값이 끼어도 됨) 뒤 '넘·초과·밖·외'.
    뒤에 부정·대조가 붙은 말('연금외수령이 아닌·한도를 넘지 않는·연금외수령분을 뺀')은 세지 않는다."""
    if any(not _negated(seg, m.end()) and not _NEG_EXCEPT.match(seg, m.end()) for m in _EXCL_LIMIT_MONEY.finditer(seg)):
        return True
    for i, k, g, _ in _basis_kinds(seg):
        v = _OVER_LIM.match(seg, i) if k == 'L' and g == 'lim' else None
        if v and not _negated(seg, v.end()):
            return True
    return False


def _prev_sentence(a, i):
    """a[i] 에서 시작하는 문장 바로 앞 문장(빈 줄은 건너뜀) — 없으면 ''."""
    lo = max(0, i - 300)
    ends = list(_SENT_END.finditer(a, lo, i))
    for j in range(len(ends) - 1, -1, -1):
        seg = a[ends[j - 1].end() if j else lo:ends[j].start()]
        if seg.strip():
            return seg
    return ''


def _span_1500(a, e):
    """a[:e] 끝의 '1,500만' 시작 위치 — 없으면 None."""
    x = re.search(r'1,?500\s*만$', a[max(0, e - 12):e])
    return e - len(x.group()) if x else None


def _amount_1500(a, s, e):
    """a[s:e] 의 1,500만이 기준이 아니라 초과분 금액인가."""
    if _AMT_AFTER.match(a, e):
        return True
    return bool(_AMT_BEFORE.search(a, max(0, s - 40), s)) and not _THRESH_AFTER.match(a, e)


def _basis_1500(a, s, e):
    """a[s:e] 의 1,500만이 초과분을 나누는 기준일 수 있는가 — 초과분 금액이거나 '…과는 별개'라고 떼어 놓은 기준은 아니다.
    '1,500만 원 판단에 포함되지 않고'처럼 판단에서 빠지는 것을 말한 1,500만은 남긴다 — 1,500만 원 규칙을 부른 말이라
    뒤 초과분이 무엇인지는 _excl_limit 가 가른다(7차 고침은 여기서 빼 '초과분만 16.5%' 오답까지 놓쳤다, 8차 검토 719~728)"""
    return not _amount_1500(a, s, e) and not _APART_AFTER.match(a, e)


def _limit_rule_excess(a, m, declared=None, excess=(), anchors=()):
    """이 '초과분'이 연금수령한도 규칙(초과분만 연금외수령) 이야기인가 — 같은 문장 안에서 본다.
    excess·anchors: 규칙 계산의 한도 초과액, 그 계산의 인출액·한도 값(_states_excess)."""
    if declared is None:
        declared = _declares_limit_1500(a)
    tail = _excess_tail(a, m)
    if _tail_comp(tail):
        # '초과분은 종합과세 또는 16.5% 분리과세' — 앞에 '한도'라고 불렀어도 1,500만 원 규칙이다. 한도 초과분은 연금외수령이라
        # 과세 방식을 고르지 않는다(doc39). 그래서 '한도 바로 뒤' 판정보다 먼저 본다 — 한도가 1,500만 원인 calc 에서
        # '1,500만원 한도 초과분 300만원에 대해 종합과세 또는 16.5% 선택'을 한도 규칙으로 봤다(4차 검토 314)
        return False
    end = _basis_end(a, m)
    head = _sent_head(a, end)
    base = end - len(head)
    # 초과분 금액으로 쓴 1,500만은 이 초과분을 나눈 기준이 아니다. '…과는 별개'로 떼어 놓은 1,500만은 그 앞 말까지 버린다
    # ('연금소득 분리과세 한도 1,500만 원 기준과는 별개로 초과분은' — 1,500만만 빼면 앞 '한도'가 1,500만 원 규칙 기준으로 남는다)
    # 1,500만 원 판단에서 무엇이 빠지는지 말한 1,500만('사적연금소득 1,500만 원 판단에 포함되지 않고')도 그 앞 말까지 버린다 —
    # 초과분을 나눈 기준은 아니라서다(7차 검토 622~629). 다만 1,500만 원 규칙을 부른 말이라 자리를 남겨 두고, 이 문장의 다른 기준이
    # 남지 않으면 아래 _excl_limit 로 뒤 마디를 가른다(8차 검토 719~728)
    basis, excluded, judged = [], None, False
    seen_lim = seen_n = None
    for x in _basis_kinds(head, declared):
        if x[2] == 'n':
            e = base + x[0]
            if _APART_AFTER.match(a, e):
                basis = []
                continue
            if _excluded_1500(a, e, end):
                basis, excluded = [], e
                seen_n = ('B', x[3])
                continue
            if _amount_1500(a, _span_1500(a, e) or e, e):
                continue
            if x[1] == 'L' and x[3] == 'n' and (judged or _excl_word_end(a, e) is not None):
                # '1,500만 원 판단에 포함되지 않고, 그 이상 받은 초과분'처럼 판단 말이 붙은 이름 없는 1,500만은 한도가 1,500만인
                # calc(declared)에서도 연금수령한도 값이 아니라 1,500만 원 기준이다(doc38 '이 1,500만원을 판단할 때').
                # 같은 문장에서 그 1,500만을 이름 없이 다시 부른 것('…포함되지 않으며, 1,500만 원을 넘으면 초과분만')도 같다(10차 검토 1020)
                x = (x[0], 'B', x[2], x[3])
                judged = True
            seen_n = (x[1], x[3])
        elif x[2] == 'lim' and _DEMO_LIM.search(head, 0, x[0] - 2) and (seen_lim or seen_n):
            # '그 한도·이 한도'는 앞에서 한도라 부른 말, 없으면 앞에서 말한 1,500만을 가리킨다('…1,500만 원 판단에 포함되지 않으며,
            # 그 한도를 넘으면 초과분만 16.5%' — '그 상한·위 금액'과 같은 지시 말이다, 10차 검토 1019)
            ref = seen_lim or seen_n
            x = (x[0], ref[0], x[2], ref[1])
        if x[2] == 'lim':
            seen_lim = (x[1], x[3])
        if x[2] in ('lim', 'n'):
            basis.append(x)
    k, org = (basis[-1][1], basis[-1][3]) if basis else (None, None)
    if k == 'L':
        return True       # '연금수령한도 초과분은 …'·'한도를 넘는 초과분'·'한도 안 1,500만 원은 …, 초과분 500만 원은'
    # 연금소득·사적연금소득·연간 연금소득으로 부른 1,500만을 넘는 초과분은 꼬리가 기타소득·연금외수령·퇴직소득이어도 1,500만 원 규칙을
    # 잘못 쓴 문장이다 — 그 초과분을 연금외수령으로 보는 규칙은 문서에 없다(doc38: 넘으면 전액 종합과세 또는 16.5% 분리과세).
    # 5차 고침이 꼬리 분리과세를 한도 규칙 말로 돌리자 아래 '연금외수령·퇴직소득이면 한도 규칙'이 먼저 걸려 이를 놓쳤다(6차 검토 517~524).
    # 초과액 값이 계산과 같아도 마찬가지라 초과액 대조보다 먼저 본다
    if k == 'R' and org == 'r':
        return False
    if excess and _states_excess(a, m, excess, anchors):
        return True       # 초과분이 규칙 계산의 (인출액 − 한도)다(3차 검토 231~239)
    if basis:
        # 이 문장이 초과분을 나눈 기준 — 그냥 1,500만으로 나누면('1,500만 원을 넘으면 초과분은') 1,500만 원 규칙이다. 한도 규칙 말
        # (연금외수령·퇴직소득)은 '1,500만 원 한도'(한도라 부름)나 '연금 1,500만 원 중'(쪼개는 금액)일 때만 믿는다 —
        # 이름 없는 1,500만을 넘는 초과분을 연금외수령이라 하면 1,500만 원 규칙 오답이다(한도가 1,500만인 calc 는 declared 로 L, 6차 523)
        e = base + basis[-1][0]
        thresh = k == 'B' and _B_THRESH.match(a, e) and not _WITHDRAW_SUBJ.search(a, max(0, e - 40), _span_1500(a, e) or e)
        return bool(_LIMIT_ONLY.search(tail)) and not thresh
    if _LIMIT_ONLY.search(tail):
        return True       # 기준 없이 '초과분은 연금외수령이라 16.5% 기타소득세'
    if excluded is not None:
        # 이 문장의 1,500만은 판단에서 빠지는 것을 말한 마디다 — 그 마디는 넘기고 뒤 마디를 본다. 앞 문장의 1,500만으로는 거슬러 가지 않는다
        return _excl_limit(a, excluded, tail)
    # 이 문장엔 기준이 없다('| **초과분** | 500만원 |'·'1,600만 원이면 초과분(100만 원)은') — 바로 앞 1,500만이 무엇을 부른 값인지 본다.
    # 초과분 금액으로 쓴 1,500만은 건너뛴다 — 앞 문장 '2,700만 원 중 1,500만 원이 초과분이에요'를 받은 '이 초과분은'(6차 526·527)
    ns_all = list(_OVER1500.finditer(a, max(0, end - 160), end))
    ns = [n for n in ns_all if _basis_1500(a, n.start(), n.end())]
    if ns_all and not ns:
        return True
    if ns and _whole_stated(a, ns[-1]) and not re.search(_RULE_NAME, head) and \
            any(not _negated(head, x.end()) and not _NEG_EXCEPT.match(head, x.end()) and not _likened(head, x.end())
                for x in _OUT_RECEIPT.finditer(head)):
        # 앞 문장이 1,500만 원 규칙을 맞게('넘으면 전액 …') 말했고 이 문장은 초과분 앞에서 연금외수령('연금외수령하면 그만큼만')을
        # 말했다 — 한도 초과분 이야기라 앞 1,500만으로 거슬러 가지 않는다('…전액 16.5% 분리과세예요. 연금외수령하면 그만큼만 16.5%
        # 기타소득세예요', 11차 검토 2013). 앞 문장이 전액을 말하지 않았으면('1,500만 원을 넘게 받으면 연금외수령으로 봐요. 연금외수령하면
        # 초과분만') 1,500만 원 규칙을 연금외수령으로 섞은 오답의 이어짐이다. 부정·대조('연금외수령이 아니면·연금외수령분을 뺀')나
        # 견줌·포함('연금외수령처럼·마찬가지로·여부와 관계없이·이든·도 포함해')으로 든 연금외수령은 세지 않는다(11차 재검토 C).
        # 이 문장 머리에 1,500만 원 규칙 말('분리과세를 원하면 연금외수령으로')이 있으면 그 규칙 이야기라 넘기지 않는다
        return True
    if ns and _excluded_1500(a, ns[-1].end(), end):
        # 앞 문장 '퇴직금은 1,500만 원 판단에 포함되지 않아요.'를 받은 '초과분만 16.5%'(8차 검토 725·726) — 같은 문장일 때처럼 가른다
        return _excl_limit(a, ns[-1].end(), tail)
    ks = _basis_kinds(_sent_head(a, ns[-1].end()), declared) if ns else []
    if ks and ks[-1][3] == 'n' and _excl_word_end(a, ns[-1].end()) is not None:
        return False      # 판단 말이 붙은 이름 없는 1,500만은 declared 여도 1,500만 원 기준이다(위 같은 문장 경우와 같다)
    return bool(ks) and ks[-1][1] == 'L'


# 규칙 계산 블록의 연금수령한도 줄 — taxcalc 가 '연금수령한도 = 평가액 ÷ (11 − n) × 120% = 값'(재원별은 '연금수령한도(R-05) = …')으로 쓴다.
# '내년 한도 = …'는 올해 한도가 아니라 잡지 않는다
_CALC_LIMIT = re.compile(r'연금수령한도(?:\(R-05\))?\s*=[^\n=]*×\s*120\s*%\s*=\s*([^\n(]+)')


def calc_limits(calc):
    """규칙 계산이 낸 올해 연금수령한도 값들(만 원)과 인출액 — 한도 계산이 없으면 ([], None)."""
    lims = []
    for b in (calc or {}).get('blocks') or []:
        for m in _CALC_LIMIT.finditer(b.get('text') or ''):
            v = money_values(m.group(1))
            if v:
                lims.append(v[0][0])
    return lims, ((calc or {}).get('parsed') or {}).get('withdraw')


# 금액 바로 뒤에 '을 넘·초과·까지'가 오면 그 금액은 초과분이 아니라 나누는 기준이다('1,500만 원을 넘는 초과분'의 1,500만).
# 다만 '이/가' 뒤의 '초과분·초과액'은 동사가 아니라 그 금액이 초과분이라는 말이다('2,000만 원 중 800만 원이 초과분이라' —
# 4차 고침이 이것까지 기준으로 빼 맞는 초과액을 못 찾았다, 5차 검토 421~423)
_THRESH_AFTER = re.compile(r'[\s*]*원?[\s*]*(?:의[\s*]*)?(?:한도[\s*]*)?(?![이가][\s*]*초과\s*(?:분|액|금액|부분))'
                           r'(?:을|를|이|가)?[\s*]*(?:넘|초과|웃돌|웃도|상회|까지|이하|이내|미만|이상)')
# '초과분' 바로 앞에 '은/는/이/가'로 붙은 1,500만도 기준이 아니라 초과분 금액이다 — 한도 1,200만·인출 2,700만이면 초과액이 마침
# 1,500만 원이다('2,700만 원 중 1,500만 원이 초과분이라 16.5% 기타소득세', 5차 검토 421·422). '1,500만 원 초과분'(조사 없음)은 기준
_AMOUNT_1500 = re.compile(r'(?<![\d,])1,?500\s*만[\s*]*원?[\s*]*(?:은|는|이|가)[\s*]*$')


def _basis_end(a, m):
    """초과분을 나눈 기준을 찾을 끝 위치 — 초과분 금액으로 쓴 바로 앞 1,500만은 빼고 그 앞까지 본다."""
    if m.group().startswith('초과분'):
        x = _AMOUNT_1500.search(a, max(0, m.start() - 20), m.start())
        if x:
            return x.start()
    return m.start()


def _states_excess(a, m, excess, anchors=()):
    """이 '초과분' 문장에 규칙 계산의 한도 초과액(인출액 − 연금수령한도)이 있는가 — '2,000만 원 − 1,500만 원' 뺄셈도 그 값으로 센다.
    기준으로 쓴 금액은 빼고 센다 — 초과액이 마침 1,500만 원이면(한도 1,200만·인출 2,700만) '1,500만 원을 넘으면'의 1,500만을
    초과액으로 봐 1,500만 원 규칙 오답을 놓쳤다(4차 검토 316).
    anchors: 그 계산의 인출액·한도 값 — 맞은 초과액이 1,500만 원뿐이면 이 값을 함께 밝혔을 때만 센다."""
    s = _sentence(a, m.start())
    vals = money_values(s)
    subs = [i for i in range(len(vals) - 1) if re.fullmatch(r'[\s*]*[−\-–][\s*]*', s[vals[i][2]:vals[i + 1][1]])]
    # 뺄셈의 두 항('1,100만 − 1,500만')도 초과액이 아니다 — 초과액은 그 차다
    ops = set(subs) | {i + 1 for i in subs}
    cands = [v for i, (v, _, e, _) in enumerate(vals) if i not in ops and not _THRESH_AFTER.match(s, e)]
    cands += [vals[i][0] - vals[i + 1][0] for i in subs]
    hits = [c for c in cands if any(_same(c, x) for x in excess)]
    if hits and anchors and all(_same(c, 1500) for c in hits):
        # 초과액이 마침 1,500만 원이면 1,500만 원 규칙 오답 '3,000만 원을 받으면 1,500만 원이 초과분이라 16.5%'와 값이 같다(6차 검토 525).
        # 초과분 앞 160자~문장 끝에 이 계산의 인출액이나 한도(1,500만이 아닌 값)를 밝혔을 때만 한도 초과액으로 센다
        e = _SENT_END.search(a, m.end())
        win = money_values(a[max(0, m.start() - 160):e.start() if e else len(a)])
        return any(_same(v, x) for v, *_ in win for x in anchors if not _same(x, 1500))
    return bool(hits)


# 초과분이 없다는 말('초과분이 없으면·초과분은 없고·초과분 없이')과, 뒤에 이어지는 있을 때 갈래
_NO_EXCESS = re.compile(r'[\s*]*(?:이|가|은|는|도)?[\s*]*(?:전혀[\s*]*)?없')
_HAS_EXCESS = re.compile(r'있으면|있다면|있을\s*(?:때|경우)|생기면|생긴다면|생길\s*(?:때|경우)|발생하면|넘으면|넘는|넘을|초과하면|초과할|초과되면')
_PART_LEAD = re.compile(r'[\s*]*원?[\s*]*(?:을|를|이|가|은|는|보다|에)?')       # 1,500만 바로 뒤 '원을·원이'
# 1,500만 판단에 들지 않아 따로 덜어 내는 돈 — 세액공제 안 받은 원금(과세제외금액)·비과세분·납입금·퇴직금(이연퇴직소득). '세금은·연금액은·
# 수령액은 그 부분만 빼서 16.5%'의 '그'는 초과분이다 — 아무 주어나 앞말로 보면 이 오답을 놓친다
_PART_MONEY = re.compile(r'원금|납입\s*금|과세\s*제외|비\s*과세|퇴직')


def _part_other(a, m):
    """'그 부분만·그만큼만' 뒤가 덜어 내는 말('빼고·제외하고·차감해')이고 '그'가 초과분이 아닌 앞말(원금·과세제외금액)을 가리키는가.
    가리킬 말은 같은 문장에서 마지막 1,500만 뒤(없으면 문장 머리)부터 '그' 앞까지의 주어(꾸밈말까지)로 본다 — '원금은 비과세라·
    원금이 있으면·원금을 뺀 나머지 전부가 대상이라'. 주어가 없거나('1,500만 원을 넘으면 그 부분만 빼서') 따로 덜어 내는 돈(_PART_MONEY)이
    아니거나 1,500만 원 규칙 이름·초과분('받는 연금소득은·넘는 금액은·초과분은')이면 '그'는 초과분이다(11차 재검토 B).
    '넘은 부분만·넘는 만큼만'은 초과분을 밝힌 말이라 보지 않는다."""
    if not m.group().startswith('그') or not _PART_EXCEPT.match(a, m.end()):
        return False
    head = _sent_head(a, m.start(), 160)
    ns = list(_OVER1500.finditer(head))
    if ns:
        head = head[_PART_LEAD.match(head, ns[-1].end()).end():]
    for s in _subjects(head)[0]:
        ph = _subject_phrase(head, s)
        cuts = [x.end() for x in _NEG_STOP.finditer(ph)]       # '넘으면,'처럼 앞 마디는 꾸밈말이 아니다
        ph = ph[cuts[-1]:] if cuts else ph
        if _PART_MONEY.search(ph) and not re.search(_RULE_NAME + r'|초과|넘', ph):
            return True
    return False


def fix_over1500(answer, calc=None):
    """(고친 답, 고친 곳 수). calc: 이 질문의 규칙 계산(taxcalc.compute) — 연금수령한도를 계산했으면 글자 대신 그 값으로 가른다."""
    orig = answer or ''
    if OVER1500_NOTE in orig:
        return orig, 0
    a = _norm_1500(orig)
    # 규칙 계산이 연금수령한도를 냈으면 구조로 가른다(3차 검토 231~239) — 초과분이 (인출액 − 한도)인 문장은 한도 규칙이다.
    # 한도가 마침 1,500만 원이면(시연 질문 1억 ÷ (11 − 3) × 120%) 이름 없이 쓴 '1,500만'은 그 한도로 본다(declared).
    # 그래도 통째로 건너뛰지는 않는다 — 1,500만 원 기준은 전 금융기관 연금계좌를 합산해 보므로(doc38 10행) 이 계좌 한도 안에서도
    # 걸릴 수 있고, '연금소득이 1,500만 원을 넘으면 초과분은 종합과세/분리과세'처럼 1,500만 원 규칙 말로 쓴 문장은 그대로 틀린 말이다
    # (4차 검토 314·315: 한도 1,500만 calc 에서 이런 문장을 모두 놓쳤다)
    lims, w = calc_limits(calc)
    excess = [w - x for x in lims if w is not None and w > x]
    anchors = lims + ([w] if w is not None else [])
    declared = _declares_limit_1500(a) or any(_same(x, 1500) for x in lims)
    for m in _PART_ONLY.finditer(a):
        if _PART_REF.fullmatch(m.group()) and (not _PART_TAX.search(_excess_tail(a, m)) or _part_other(a, m)):
            continue          # '그 부분만'은 뒤에 1,500만 원 규칙 세금 말이 올 때만, 다른 돈을 덜어 내는 말('원금은 그 부분만 빼고')이 아닐 때만 본다
        comp = _tail_comp(_excess_tail(a, m))
        # 1,500만을 초과분 금액으로만 썼으면('1,500만 원이 초과분이라 16.5% 기타소득세') 1,500만 원 기준을 말한 문장이 아니다(5차 검토 421·422).
        # 앞 문장에서 금액으로 밝힌 1,500만('2,700만 원 중 1,500만 원이 초과분이에요. 이 초과분은 …', 6차 526·527)도 같다.
        # 꼬리에 종합과세·분리과세가 오면 금액이라도 1,500만 원 규칙 말이라 그대로 본다
        end = m.start() if comp else _basis_end(a, m)
        ns = [n for n in _OVER1500.finditer(a, max(0, end - 160), end) if comp or _basis_1500(a, n.start(), n.end())]
        # '초과분만이 아니라 전액', '초과분(100만 원)이 아닌 전체 금액'처럼 바로 부정하는 맞는 문장은 건드리지 않는다(저장된 답 1,118건 점검)
        if not ns or \
                re.search(r'아니|아닌|않|말고|전체|전액', a[m.end():m.end() + 60]) or re.search(r'오해|착각', a[max(0, m.start() - 20):m.start()]):
            continue
        # '초과분이 없으면 3~5% 연금소득세로 끝나요'는 넘지 않을 때 이야기라 초과분만 따로 과세한다는 주장이 아니다(9차 검토 818).
        # 같은 문장에 '있으면·생기면·넘으면' 갈래가 이어지면 그 갈래가 초과분 세금을 말할 수 있어 그대로 본다
        if _NO_EXCESS.match(a, m.end()) and not _HAS_EXCESS.search(_excess_tail(a, m)):
            continue
        # 초과분에 종합과세·분리과세를 붙인 문장은 초과액 값이 맞아도 1,500만 원 규칙이다 — 한도 초과분은 연금외수령(16.5% 기타소득세,
        # doc39)이지 과세 방식을 고르는 돈이 아니다. 연금소득·사적연금소득으로 부른 기준도 같다 — 그래서 초과액 대조는
        # _limit_rule_excess 안에서 이 둘을 본 뒤에 한다(4차 검토 316, 6차 검토 517~524)
        if _limit_rule_excess(a, m, declared, excess, anchors):
            continue
        return orig.rstrip() + '\n\n' + OVER1500_NOTE, 1
    return orig, 0


# 공제율 기준 섞기 — 10-14 m.Talk 비교 M03: '총급여가 7,000만 원이라면 이는 종합소득금액 4,500만 원을 초과하므로'.
# 직장인은 총급여 5,500만 원, 종합소득이 있으면 종합소득금액 4,500만 원이 기준이다(doc41). 총급여와 비교하는 문장의
# '종합소득금액 4,500만 원을 초과/넘'만 바꾼다(앞에 5,500이 이미 있으면 '총급여 5,500만 원(종합소득 4,500만 원)' 병기라 그대로)
_BASIS = re.compile(r'(총급여(?:(?!5,500|5500)[^.\n]){0,30}?)종합\s*소득(?:\s*금액)?\s*4,500\s*만\s*원?(을|를)?\s*(초과|넘)')
_BASIS_SLASH = re.compile(r'총급여\s*[/·]\s*종합\s*소득(?:\s*금액)?\s*5,500\s*만\s*원?\s*(이하|초과)')


def fix_income_basis(answer):
    """(고친 답, 고친 곳 수)."""
    a, n1 = _BASIS.subn(lambda m: f"{m.group(1)}총급여 기준 5,500만 원{m.group(2) or '을'} {m.group(3)}", answer or '')
    a, n2 = _BASIS_SLASH.subn(lambda m: f'총급여 5,500만 원 {m.group(1)}(종합소득금액 4,500만 원 {m.group(1)})', a)
    return a, n1 + n2


# 납입한도와 세액공제 한도 섞기 — 10-14 외부 검토: '연금저축과 IRP의 합산 납입한도는 900만 원'이 숫자 대조 11/11 통과.
# 입금(납입)은 합산 연 1,800만 원, 세액공제를 받는 한도가 900만 원이다(doc41 '세액공제 받을 수 있는 납입한도 … 연900만원').
# 앞에 '세액공제'가 붙은 '세액공제 납입한도 900만'은 맞는 말이라 그대로 둔다
_PAY_LIMIT_900 = re.compile(r'(납입\s*한도)(\s*(?:는|은|가|이|도)?\s*(?:연\s*)?(?:최대\s*)?900\s*만)')


def fix_limit_terms(answer):
    """(고친 답, 고친 곳 수)."""
    a, n = answer or '', 0
    out, last = [], 0
    for m in _PAY_LIMIT_900.finditer(a):
        if re.search(r'세액\s*공제\s*(?:받을\s*수\s*있는\s*)?$|공제\s*$', a[max(0, m.start() - 16):m.start()]):
            continue
        out.append(a[last:m.start()] + '세액공제 한도' + m.group(2))
        last = m.end()
        n += 1
    return (''.join(out) + a[last:], n) if n else (a, 0)


def fix_outdated(answer):
    """(고친 답, 고친 곳 수)."""
    n = 0
    for rx, rep in _OUTDATED:
        answer, k = rx.subn(rep, answer or '')
        n += k
    return answer, n


# 근거에 없는 연도 기준 — 모델이 '2023년 기준', '현 시점(2025년)' 처럼 연도를 지어 붙인다(시연 실측).
# 질문·앞 대화·근거 어디에도 없는 연도일 때만 그 꾸밈말을 뺀다. 문서에 있는 연도(시행일 등)는 그대로 둔다.
_YEAR_ASOF = re.compile(r'\(\s*(20\d{2})\s*년\s*기준\s*\)\s*|(?<![\d.])(20\d{2})\s*년\s*기준(?:으로)?\s*,?\s*')
_YEAR_NOW = re.compile(r'(현\s*시점|현재|지금|올해)\s*\(\s*(20\d{2})\s*년\s*\)')
_YEAR_AGE = re.compile(r'(?<![\d.])(20\d{2})\s*년\s*(?:현재\s*)?,?\s*(?=만\s*\d{2}\s*세)')     # '2023년 만 62세에'


def strip_unsupported_years(answer, supported):
    """(고친 답, 고친 곳 수) — supported 글(질문·대화·근거)에 없는 연도의 '…년 기준' 꾸밈말을 뺀다."""
    have = set(re.findall(r'20\d{2}', supported or ''))
    n = 0

    def asof(m):
        nonlocal n
        y = m.group(1) or m.group(2)
        if y in have:
            return m.group(0)
        n += 1
        return ''

    def now(m):
        nonlocal n
        if m.group(2) in have:
            return m.group(0)
        n += 1
        return m.group(1)
    answer = _YEAR_ASOF.sub(asof, answer or '')
    answer = _YEAR_AGE.sub(asof, answer)
    answer = _YEAR_NOW.sub(now, answer)
    return answer, n


def summary_line(res):
    s = f"숫자 대조: {res['matched']}/{res['checked']} 확인"
    if res['unmatched']:
        s += ' · 미확인 ' + ', '.join(u['text'] for u in res['unmatched'][:5])
    if res['contradiction'] or res.get('conflict'):
        s += f" · 규칙 계산({', '.join(res['key_figures'])})과 어긋남 → {res['action']}"
    return s


_HANDOFF_WHY = '설명 문장을 만드는 과정에서 위 계산과 다른 금액이 나와, 혼동을 막기 위해 계산 결과만 먼저 보여드립니다. '


def handoff_answer(calc, cite=None, why=_HANDOFF_WHY):
    """계산과 어긋난 답 대신 내보낼 글 — 규칙 계산 요약 + 상담 연결.
    why: 계산 결과만 보이는 까닭(마감 초과·연결 끊김 때는 위에 따로 밝히므로 빈 문자열)."""
    ref = f'[문서{cite}]' if cite else ''
    lines = ['**결과(규칙 계산)**']
    for b in (calc or {}).get('blocks', []):
        if not b.get('numbers'):
            continue
        body = b['text'].split('[원문]')[0].split('\n')[1:]
        for ln in body:
            ln = ln.strip()
            ln = re.sub(r'\(R-\d+(?:·R-\d+)*\)', '', ln)       # 고객 화면에는 규칙 번호를 빼고(근거 칸에 따로 나온다)
            if ln.startswith('- '):
                lines.append(ln + (f' {ref}' if ref and ('합계' in ln or '세액공제액' in ln) else ''))
    lines += ['',
              (why or '') +
              '위 금액은 문서 규칙으로 계산한 값이며, 실제 세금은 원천징수영수증·계좌 재원 내역에 따라 달라질 수 있습니다.',
              '',
              # 운영시간은 doc54 원문. 이관 연동은 없어 '넘겨 드린다'고 하지 않는다(본선_작업정리/12 부록 F)
              '**다음 행동**: 계좌의 실제 재원과 퇴직소득세는 연금 상담(연금자산관리센터 ☎ 1588-5577, 평일 08시~17시, '
              '주말·공휴일 제외)에서 확인할 수 있어요.']
    return '\n'.join(lines)


def note_answer(answer, calc, cite=None):
    """답 끝에 규칙 계산값을 밝힌다 — 핵심 금액과 비교(나눠 받기 등) 줄을 블록에서 그대로 옮긴다."""
    ref = f'[문서{cite}]' if cite else ''
    keep = []
    for b in (calc or {}).get('blocks', []):
        if not b.get('numbers'):
            continue
        for ln in b['text'].split('[원문]')[0].split('\n')[1:]:
            ln = re.sub(r'\(R-\d+(?:·R-\d+)*\)', '', ln.strip())
            if ln.startswith('- ') and re.search(r'합계|세액공제액|비교|질문한 방식|조합', ln):
                keep.append(ln)
    if not keep:
        figs = [b['key_figure'] for b in (calc or {}).get('blocks', []) if b.get('key_figure')]
        keep = [f'- {", ".join(figs)}']
    return (answer.rstrip() + f'\n\n**계산 확인** — 위 설명에 문서 규칙 계산과 다른 금액이 있어, 규칙으로 계산한 값을 밝힙니다{ref}. '
            '다르면 아래 값이 기준입니다.\n' + '\n'.join(keep[:4]))
