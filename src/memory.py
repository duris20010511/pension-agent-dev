# -*- coding: utf-8 -*-
"""
대화 기억(O5) — 세션마다 앞 질문·답변과 고객이 말한 조건(나이·계좌·금액·소득)을 기억한다.

왜 — 예선은 질문 하나에 답 하나였다. 실제 상담은 이어진다.
    "62살인데 IRP에서 연 1,800만 원 받으면 세금은?" → "그럼 1,500만 원으로 줄이면요?" → "70살부터는요?"
둘째·셋째 질문은 혼자서는 뜻이 없다. 그대로 검색하면 엉뚱한 문서가 오고, 세금 계산(taxcalc)도 나이·금액을 못 찾는다.
그래서 이어지는 질문은 앞 대화로 '혼자서도 뜻이 통하는 질문'으로 고쳐 쓴 뒤(followup.py) 검색·계산에 쓰고,
답변을 만들 때는 앞 대화 요약과 고객 정보를 함께 보여 준다.

이 모듈은 API 를 부르지 않는다(test_memory.py 에서 오프라인으로 검사). 기억은 프로세스 메모리에만 두고
TTL(기본 30분)이 지나면 지운다 — 디스크에 쓰지 않는다. session_id 없이 온 요청은 예전처럼 기억 없이 처리한다
(대회 평가 규격은 그대로).
"""
import os
import re
import time
import threading

import taxcalc

TTL_S = float(os.environ.get('AGENT_MEMORY_TTL_S', '1800'))
MAX_SESSIONS = int(os.environ.get('AGENT_MEMORY_MAX', '1000'))
MAX_TURNS = 6           # 세션마다 남기는 턴 수(오래된 것부터 버린다)
SHOW_TURNS = 2          # 답변 프롬프트에 보여 줄 앞 턴 수

_PRODUCTS = [
    ('IRP', r'IRP|개인형\s*퇴직연금'),
    ('연금저축', r'연금저축'),
    ('DC형', r'(?<![A-Za-z])DC(?![A-Za-z])|확정기여'),
    ('DB형', r'(?<![A-Za-z])DB(?![A-Za-z])|확정급여'),
    ('ISA', r'ISA'),
    ('국민연금', r'국민연금'),
    ('퇴직금', r'퇴직금|퇴직급여'),
]


def slots(q):
    """질문 하나에서 기억할 조건. taxcalc 의 읽기 규칙을 그대로 쓴다(두 곳이 다르게 읽으면 안 된다)."""
    p = taxcalc.parse(q or '')
    s = {}
    if p['age'] is not None:
        s['age'] = f'{p["age"][1]}대' if isinstance(p['age'], tuple) else f'{p["age"]}세'
    for x in p['payout']:
        if x['src'] not in (None, 'private'):
            continue                    # 국민연금·남편 금액 등은 고객 연금계좌 수령액이 아니다
        if x['period'] == 'monthly':
            s['payout'] = f'월 {taxcalc.won(x["v"])}(연 {taxcalc.won(x["v"] * 12)}) 수령'
        elif x['period'] == 'yearly':
            s['payout'] = f'연 {taxcalc.won(x["v"])} 수령'
    if p['income']:
        s['income'] = f'{p["income"][0]} {taxcalc.won(p["income"][1])}'
    if p['balance'] is not None:
        s['balance'] = f'평가액 {taxcalc.won(p["balance"])}'
    if p['withdraw'] is not None:
        s['withdraw'] = f'인출 {taxcalc.won(p["withdraw"])}'
    if p['isa']:
        s['isa'] = f'ISA 만기 전환 {taxcalc.won(p["isa"])}'
    # 재원 구성·퇴직소득세·연차 — 되묻기에 답한 정보가 다음 턴 계산에 이어지게(장면 2)
    f = p.get('fund') or {}
    if f:
        s['fund'] = ' · '.join(f'{taxcalc.FUND_NAME[k].split(" ", 1)[1]} {taxcalc.won(f[k])}'
                               for k in ('free', 'retire', 'credit') if k in f)
    if p.get('retire_eff') is not None:
        s['rtax'] = f'퇴직소득세 실효세율 {p["retire_eff"]:g}%'
    elif p.get('rtax') is not None:
        s['rtax'] = f'퇴직소득세 {taxcalc.won(p["rtax"])}'
    if p.get('year_n') and not p.get('year_assumed'):
        s['year'] = f'연금수령연차 {p["year_n"]}년차'
    elif p.get('year_assumed') and re.search(r'퇴직', q or ''):
        s['year'] = '올해 퇴직(올해가 연금수령연차 1년차)'      # 다음 턴 재작성이 이 전제를 잃지 않게
    if p['contrib']:
        s['contrib'] = '납입 ' + ', '.join(('IRP ' if c['acct'] == 'irp' else '연금저축 ') + taxcalc.won(c['v'])
                                         for c in p['contrib'])
    prods = []
    for name, rx in _PRODUCTS:
        for m in re.finditer(rx, q or '', re.I):
            # '연금저축 아니고 IRP'처럼 부정한 계좌는 기억하지 않는다
            if not re.match(r'\s*(?:은|는|이|가)?\s*(?:아니|말고|대신)', (q or '')[m.end():m.end() + 8]):
                prods.append(name)
                break
    if prods:
        s['products'] = prods
    return s


_ORDER = ['age', 'products', 'income', 'payout', 'balance', 'withdraw', 'fund', 'rtax', 'year', 'contrib', 'isa']


def merge_profile(turns):
    """턴마다의 조건을 합친다. 같은 조건은 나중 턴이 이긴다(정정·조건 변경). 계좌·상품은 모은다."""
    prof = {}
    prods = []
    for t in turns:
        s = t.get('slots') or {}
        for k, v in s.items():
            if k == 'products':
                for x in v:
                    if x in prods:
                        prods.remove(x)
                    prods.append(x)
            else:
                prof[k] = v
    if prods:
        prof['products'] = prods
    return prof


def profile_text(prof):
    parts = []
    for k in _ORDER:
        if k not in prof:
            continue
        v = prof[k]
        parts.append('·'.join(v) if isinstance(v, list) else (f'나이 {v}' if k == 'age' else v))
    return ' · '.join(parts)


_CITE_BLOCK = re.compile(r'\n\s*\*\*(?:근거|참고 자료)\*\*[\s\S]*$')


def _brief(answer, n):
    """앞 답변 요약 — 근거 목록·꾸밈을 걷어내고 앞부분만."""
    a = _CITE_BLOCK.sub('', answer or '')
    a = re.sub(r'\[\d+\]', '', a)
    a = re.sub(r'[*#>`]+', '', a)
    a = re.sub(r'\s+', ' ', a).strip()
    return a if len(a) <= n else a[:n].rstrip() + ' …'


FLAGGED_Q = '(붙여넣은 글 — 지시문이 의심되어 내용을 생략함)'


def history_text(turns, n=SHOW_TURNS, max_answer=300):
    lines = []
    for t in turns[-n:]:
        if t.get('flagged'):
            # 지시문 의심 턴을 그대로 되보이면 다음 턴에서 주입 방어(INJECTION_POLICY)가 빠진 채로 모델에 들어간다
            lines.append(f'고객: {FLAGGED_Q}')
            continue
        lines.append(f'고객: {t["q"]}')
        if t.get('standalone') and t['standalone'] != t['q']:
            lines.append(f'(해석한 질문: {t["standalone"]})')
        if t.get('answer'):
            lines.append(f'상담: {_brief(t["answer"], max_answer)}')
    return '\n'.join(lines)


# 이어지는 질문의 표지 — 앞 대화 없이 뜻이 안 통하는 말
_LEAD = re.compile(r'^\s*(?:아\s*|아니\s*|음\s*)?(?:그럼|그러면|그렇다면|그런데|근데|그건|그거|그게|그걸|그때|그\s*경우|그\s*돈|그\s*계좌|'
                   r'그\s*펀드|그\s*상품|그\s*방법|그쪽|만약|만일|아까|방금|앞에서|위에서|말씀하신|이거|저거|이건|저건|또|'
                   r'그리고|혹시\s*그|왜요|왜\s|어째서|더\s*자세히|좀\s*더|반대로|대신)')
_TAIL = re.compile(r'(?:면요|라면요|이면요|으면요|는요|은요|인가요\s*그럼|이요|도요|까지는요|부터는요)\s*[?？]?\s*$')
_CORRECT = re.compile(r'잘못\s*(?:말|썼|적)|정정|아니라\s*\d|가\s*아니고|이\s*아니고')
_DEMONSTRATIVE = re.compile(r'(?:^|\s)(?:그|이|저|해당|위|앞)\s*(?:펀드|상품|계좌|방법|돈|금액|제도|경우|부분|조건|서류|세금|세율|한도)')


# 앞 답의 계산·조건을 가리키는 말 — 금액이 새로 없어도 앞 대화 없이는 뜻이 안 통한다
_REF = re.compile(r'나머지|달라지|달라져|달라질|그렇게\s*하면|그\s*경우|이\s*경우|한도까지만|나눠\s*받으면|그\s*대신|그럼\s*내년')
_SHORT_END = re.compile(r'(?:면|면요|이면|이면요|라면|는요|은요|이요|요|죠|나요|까요)\s*[?？.…]*\s*$')
_ADD = re.compile(r'(?:[가-힣A-Za-z\d](?:도|만|에만|까지|부터|엔|에는)|(?:^|\s)(?:더|또))\s')
_DOMAIN = re.compile(r'연금|IRP|ISA|세금|세액|공제|펀드|DC|DB|퇴직|수령|인출|한도|수수료|디폴트|TDF|상품|계좌|보험|이전|해지|중도|절차|서류|신청', re.I)
_OWNER = re.compile(r'제\s*(?:건|거)|(?:남편|아내|와이프)\s*(?:건|거|만|은|는)|내년엔|그럼요|부부\s*합|둘\s*다|둘\s*중')
# 이 모드면 앞 턴이 있을 때 늘 재작성한다(재작성 모델이 주제 전환을 스스로 판단). score_dialog.py --mode 로 비교한다.
FOLLOWUP_MODE = os.environ.get('AGENT_FOLLOWUP_MODE', 'detect')


def is_followup(q, turns, mode=None):
    """(이어지는 질문인가, 이유). 앞 턴이 없으면 늘 False.

    처음 규칙은 대화 43개(이어지는 턴 80개)에서 판정 정확도 66%였다 — '매달 120만 원씩이면?',
    'IRP도 300 넣으면?', '제 건 80살 되면 얼마나 줄어요?' 같은 생략형을 놓쳤다. 그래서 짧은 질문의
    끝맺음·보조사(도·만·더)·숫자만 있는 질문·소유 표현을 더 본다."""
    if not turns:
        return False, ''
    if (mode or FOLLOWUP_MODE) == 'always':
        return True, '늘 재작성'
    t = (q or '').strip()
    real = [x for x in turns if not x.get('flagged')]
    if real and real[-1].get('needs') and (re.search(r'\d', t) or re.search(r'모르|없어|없습니다|처음', t)):
        return True, '정보 보충'      # 앞 답이 계산에 필요한 정보를 물었고 고객이 그 값을 알려 준 턴
    if _CORRECT.search(t):
        return True, '정정'
    if len(t) <= 70 and _REF.search(t):
        return True, '이어 말하기'      # '올해는 한도까지만 받고 나머지는 내년에 받으면 어떻게 달라져요?'(다음 행동 버튼)
    if _LEAD.search(t):
        return True, '이어 말하기'
    if len(t) <= 30 and _TAIL.search(t):
        return True, '생략'
    if _DEMONSTRATIVE.search(t):
        return True, '지시어'
    if _OWNER.search(t):
        return True, '지시어'
    if len(t) <= 12:
        return True, '짧은 질문'
    if len(t) <= 20 and re.search(r'면\s*[?？]?\s*$', t):
        return True, '조건만'        # 'IRP에 500 더 넣으면?' — 본문 없이 조건만 바꾼 질문
    if len(t) <= 35 and _SHORT_END.search(t) and (_ADD.search(t) or re.match(r'^\s*[\d,]+', t) or not _DOMAIN.search(t)):
        return True, '생략'
    # 숫자(나이·금액)만 바꾸고 계좌·상품 이름이 없는 짧은 질문 — 앞 대화의 계좌를 이어받는다
    if len(t) <= 40 and re.search(r'\d', t) and not re.search(r'연금저축|IRP|ISA|DC|DB|퇴직|국민연금|펀드|TDF', t, re.I):
        return True, '조건만'
    return False, ''


_BAD_REWRITE = re.compile(r'^(?:네|아니요|답변|정답|설명)\b|입니다\.?\s*$|습니다\.?\s*$')


def clean_rewrite(text, q):
    """재작성 결과 검사. 질문 하나가 아니면 None(→ 대체 방식)."""
    if not text:
        return None
    t = text.strip().split('\n')[0].strip()
    t = re.sub(r'^(?:고쳐\s*쓴\s*질문|재작성|질문|답)\s*[:：]\s*', '', t).strip().strip('"\'“”')
    if not (4 <= len(t) <= 300) or _BAD_REWRITE.search(t):
        return None
    return t


def fallback_rewrite(q, turns):
    """재작성 호출이 실패했을 때 — 앞 질문을 붙여 검색이라도 맞는 쪽으로 가게 한다."""
    real = [t for t in turns if not t.get('flagged')]
    if not real:
        return q
    prev = re.sub(r'\s*\(앞 질문:.*$', '', real[-1].get('standalone') or real[-1]['q'])   # 겹겹이 쌓이지 않게
    return f'{q} (앞 질문: {prev})'


class Store:
    """세션 기억. 스레드 안전, TTL·개수 제한. 디스크에 쓰지 않는다."""

    def __init__(self, ttl=TTL_S, max_sessions=MAX_SESSIONS):
        self.ttl = ttl
        self.max = max_sessions
        self._d = {}
        self._lock = threading.Lock()

    def _evict(self, now):
        dead = [k for k, v in self._d.items() if now - v['t'] > self.ttl]
        for k in dead:
            del self._d[k]
        while len(self._d) > self.max:
            del self._d[min(self._d, key=lambda k: self._d[k]['t'])]

    def history(self, sid):
        now = time.time()
        with self._lock:
            self._evict(now)
            s = self._d.get(sid)
            return [dict(t) for t in s['turns']] if s else []

    def add(self, sid, q, standalone, answer, flagged=False, needs=None):
        """막힌 턴은 넣지 말 것(server 가 거른다). flagged = 지시문 의심 — 넣되 내용·조건은 남기지 않는다.
        needs = 이 턴의 답이 고객에게 물은 정보(계산에 빠진 값). 다음 턴이 그 값을 알려 주면 이어지는 질문으로 본다."""
        now = time.time()
        if flagged:
            turn = {'q': FLAGGED_Q, 'standalone': FLAGGED_Q, 'answer': '', 'slots': {}, 't': now, 'flagged': True}
        else:
            turn = {'q': q, 'standalone': standalone or q, 'answer': answer or '',
                    'slots': slots(standalone or q), 't': now, 'needs': list(needs or [])}
        with self._lock:
            s = self._d.setdefault(sid, {'turns': [], 't': now})
            s['turns'].append(turn)
            s['turns'] = s['turns'][-MAX_TURNS:]
            s['t'] = now
            self._evict(now)

    def reset(self, sid):
        with self._lock:
            return self._d.pop(sid, None) is not None

    def view(self, sid):
        turns = self.history(sid)
        return {'turns': len(turns), 'profile': profile_text(merge_profile(turns)),
                'questions': [t['q'] for t in turns]}


STORE = Store()
