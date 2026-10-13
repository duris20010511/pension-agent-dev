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
