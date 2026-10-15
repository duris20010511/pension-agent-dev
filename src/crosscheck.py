# -*- coding: utf-8 -*-
"""
금액 읽기 이중 확인 — 정규식(taxcalc.parse)이 읽은 금액의 쓰임·기간·나이를 HCX-005 가 따로 뽑은 것과 견준다.

왜 — 금액 읽기는 글자 단서 규칙이라 하나를 막으면 비슷한 어순이 깨진다(10-14 고침 1~4차마다 새 회귀:
'퇴직금을 제가 IRP에 300만 원 넣으면'을 본인 납입으로, '연 1,000만 원 받는 연말정산'을 납입으로 읽음).
잘못 읽으면 검증(verify)도 그 계산값을 기준으로 삼아 못 잡는다. 그래서 계산값을 내기 전에 다른 방법으로
한 번 더 읽고, 둘이 다르면 숫자를 내지 않고 고객에게 확인한다.

견주는 것(계산에 쓰인 금액만)
  쓰임    납입 / 인출 / 연금 수령 / 평가액 / 소득 / 퇴직금 / 퇴직소득세 / ISA 전환
  기간    1년 / 매달 (납입·수령)
  주인    다른 사람 돈인지(정규식이 계산에서 뺐어도 — 합쳐 묻는 질문인지 못 가른다)
  나이    고객 나이, 받는 나이
  못 읽음 HCX-005 가 쓰임을 붙인 금액인데 정규식이 아예 못 읽었거나 읽고도 계산에 안 쓴 것(10-15)
HCX-005 가 '모름'이라고 한 칸은 견주지 않는다 — 모를 때까지 막으면 되묻기만 늘어난다.
"""
import json
import os
import re
import time

import clova

MODEL = os.environ.get('AGENT_XCHECK_MODEL', 'HCX-005')

SYSTEM = """너는 연금 상담 질문에서 '돈 금액'과 그 쓰임을 뽑는다. 계산하지 말고, 질문에 있는 그대로만 적는다.
출력은 JSON 하나만:
{"amounts": [{"text": "질문 속 금액 표현 그대로", "man": 금액(만 원 단위 숫자, 예: 600만 원→600, 1억→10000, 5천→5000, 50,000원→5),
  "use": "납입|인출|연금수령|평가액|소득|퇴직금|퇴직소득세|ISA|기타|모름",
  "period": "1년|매달|한번|모름",
  "whose": "본인|다른사람|모름"}],
 "age": 고객의 지금 나이 숫자 또는 null,
 "receive_age": 연금을 받기 시작하거나 받는 나이(지금 나이와 다를 때만) 또는 null}
쓰임 기준:
- 납입: 고객이 연금저축·IRP·DC(본인 부담금)에 넣는 돈. 회사가 넣어 주는 돈·퇴직금을 옮겨 넣는 돈은 '퇴직금'.
- 인출: 계좌에서 빼거나 해지해서 찾는 돈(연금 아닌 방식). 연금수령: 연금으로 나눠 받는 돈.
- 평가액: 계좌에 지금 들어 있는 돈. 소득: 총급여·연봉·종합소득금액. 퇴직소득세: 퇴직소득세 금액.
- ISA: ISA 만기금을 옮기는 돈. 기타: 위 어느 것도 아닌 돈(세금 금액, 남의 돈, 예시 기준 금액 등).
- 확실하지 않으면 '모름'. 금액이 아닌 숫자(나이·연도·%·개월)는 넣지 않는다."""

_USE_OF_ROLE = {'contrib': '납입', 'withdraw': '인출', 'payout': '연금수령', 'balance': '평가액', 'income': '소득',
                'rtax': '퇴직소득세', 'isa': 'ISA'}
# 같은 쓰임으로 봐도 되는 짝(정규식 이름 ↔ 모델 이름)
_COMPAT = {('인출', '연금수령'): True, ('연금수령', '인출'): True, ('평가액', '퇴직금'): True}
# 모델이 쓰임 대신 질문 주제를 적는 경우(세액공제·소득공제·기타)는 견주지 않는다 — 144개 시험에서 헛경보의 대부분
_KNOWN_USE = {'납입', '인출', '연금수령', '평가액', '소득', '퇴직금', '퇴직소득세', 'ISA'}


def _won(v):
    try:
        import taxcalc
        return taxcalc.won(v)
    except Exception:
        return f'{v:g}만 원'


def _calc_num(m):
    """JSON 이 아닌 man 칸 → 값. 셈식('110 * 12')은 곱셈·나눗셈만 계산하고, 쉼표 숫자('50,000')는 쉼표를 빼고,
    그 밖('모름' 같은 맨 글자)은 null(견주지 않음)."""
    e = m.group(2).strip()
    if e[:1] in ('"', '[', '{'):
        return m.group(0)
    e = re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', e)
    toks = re.findall(r'\d+(?:\.\d+)?|[*/]', e)
    if not re.fullmatch(r'[\d.\s*/]+', e) or len(toks) % 2 == 0:
        return m.group(1) + 'null'
    v = float(toks[0])
    for op, n in zip(toks[1::2], toks[2::2]):
        v = v * float(n) if op == '*' else v / float(n) if float(n) else 0
    return m.group(1) + f'{v:g}'


def extract(q, timeout=20):
    msgs = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': q}]
    out = clova.chat(msgs, temperature=0.0, max_tokens=700, model=MODEL, timeout=timeout, retries=2)
    m = re.search(r'\{.*\}', out or '', re.S)
    if not m:
        return None
    s = m.group(0)
    try:
        return json.loads(s)
    except Exception:
        pass
    # 모델이 man 칸에 셈식·쉼표 숫자·맨 글자를 적으면('110 * 12', '50,000', '모름') JSON 이 깨져 확인 전체를 건너뛰었다
    # (10-15 측정 256개 중 6개). man 칸 값만 다음 칸 앞까지 읽어 고친다
    s = re.sub(r'("man"\s*:\s*)(.*?)(?=\s*,\s*"|\s*\})', _calc_num, s)
    try:
        return json.loads(s)
    except Exception:
        return None


def _str(v):
    """모델이 칸에 목록·숫자를 넣기도 한다('use': ['납입']) — 글 하나로 맞춘다."""
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ''
    return str(v or '').strip()


def _span_use(s):
    r = s.get('role')
    if r == 'fund':
        return '퇴직금' if s.get('kind') == 'retire' else None   # 공제받은 돈·안 받은 돈 구성은 견주지 않는다
    return _USE_OF_ROLE.get(r)


def _values(a):
    """모델이 뽑은 금액 하나 → 만 원 값 후보들. 모델이 단위를 섞어 쓴다(300만 원 → 3000000, 월 100만 → 1200)
    — 그래서 원문 표현(text)을 정규식 금액 읽기로 다시 읽은 값을 먼저 쓰고, man 은 단위를 바꿔 본 값까지 넣는다."""
    vals = []
    try:
        import taxcalc
        for sp in taxcalc.parse(taxcalc.normalize(str(a.get('text') or ''))).get('spans') or []:
            vals.append(float(sp['v']))
    except Exception:
        pass
    if vals:
        return vals                        # 원문 표현으로 읽었으면 그것만 — man 단위 바꿔 보기가 다른 금액과 엉뚱하게 맞았다
    m = a.get('man')
    if isinstance(m, (int, float)) and m > 0:
        vals += [float(m), float(m) / 10000, float(m) / 12]
    return vals


# 질문 속 금액 표현 덩어리 — '1억2천'·'8백50만'·'148만 5천 원'·'50,000원'·'3천'. 정규식 금액 읽기(taxcalc)와 따로 넉넉히 읽는다:
# 모델이 뽑은 금액이 질문에 정말 있는지(모델이 계산해 지어낸 '1년에 960만 원'이 아닌지) 보고, 모델 man 칸(단위를 섞어 씀:
# '8백50만'→8500)보다 원문 표현의 값을 믿으려고. 띄어 쓴 뒤 숫자는 단위가 따라올 때만 잇는다('3천 2년차'의 2는 다른 숫자)
_NUM = r'\d[\d,]*(?:\.\d+)?'
_AMT = re.compile(r'(?<![\d.,])' + _NUM + r'(?:\s*(?:억|천|백|만)(?:' + _NUM + r'|\s+' + _NUM
                  + r'(?=\s*(?:억|천|백|만|원)))?)*(?:\s*원)?')
# 금액이 아닌 숫자 — 나이·연도·기간·비율·차수('62살'·'2년차'·'12월에'·'16.5%')
_NOT_AMT = re.compile(r'\s*(?:살|세(?!액|금|율)|년|개월|개(?!인)|월|%|퍼|프로|차(?!이)|번|회(?!사)|명(?!의)|배(?!우|당)|시간)')
# 모델이 옮긴 원문 표현 속 기간 말 — 모델 기간 칸이 표현에 근거가 있는지 본다('연금'·'연말'의 연은 아니다)
_PER_WORD = {'매달': re.compile(r'월|매달|한\s*달|달마다|다달이'), '1년': re.compile(r'연(?!금|말)|매년|1\s*년|일\s*년|해마다|한\s*해')}
# 금액 바로 앞(14자)이 기간 말로 끝나는가 — 정규식 기간이 바로 앞 말에서 왔는지 본다(taxcalc 의 앞말 읽기와 같은 꼴).
# '올해·매년 연금저축에 30씩'처럼 계좌 이름을 건너 먼 앞말에서 온 기간은 여기에 맞지 않는다
_PRE_PERIOD = {
    'monthly': re.compile(r'(?:(?<![\d개])월|매달|매월|한\s*달(?:에)?|달마다|다달이)\s*(?:에|에만)?\s*(?:납입(?:액|금)?\s*)?'
                          r'(?:은|는|이|가)?\s*(?:약|대략|한)?\s*$'),
    'yearly': re.compile(r'(?:1년|일년|연간?|매년|한\s*해|해마다)\s*(?:에|에만|동안)?\s*(?:납입(?:액|금)?\s*)?'
                         r'(?:은|는|이|가)?\s*(?:약|대략|한)?\s*$')}
# 금액 바로 뒤에 붙은 기간 말 — '30씩 매달', '25만 원씩 12개월', '50씩, 매달이요'. 정규식은 앞말이 있으면 뒤의 이 말을 안 본다.
# 바로 뒤만 본다 — '150씩 받으면 1년에 1,800'의 1년에는 150 의 기간이 아니라 고객이 합친 값의 기간이다
_POST_PER = {'매달': re.compile(r'\s*(?:만\s*원|원)?\s*(?:을|를)?\s*(?:씩\s*,?\s*\d{1,2}\s*개월|씩?\s*,?\s*'
                               r'(?:매달|매월|월마다|달마다|다달이|한\s*달))'),
             '1년': re.compile(r'\s*(?:만\s*원|원)?\s*(?:을|를)?\s*씩?\s*,?\s*(?:매년|해마다|연간|1\s*년에|일\s*년에|한\s*해에)')}
# 계산에 안 쓰인 금액이 다른 금액에서 나온 값인지 볼 때 곱해 보는 수 — 1년 치(×12)와 세율(문서의 16.5·13.2·5.5·4.4·3.3%)
_DERIVE = [k * r for k in (1, 12) for r in (1, 0.165, 0.132, 0.055, 0.044, 0.033)]


def _amount_value(t):
    """금액 표현 하나 → 만 원 값. 단위 없이 끝나면 만 원('3천'=3,000만 원, '30'=30만 원)이고, '만' 뒤에 오는 천·백은
    원('148만 5천 원')이다. '억' 뒤 끝의 수는 만 원('1억2천'=1억 2천만 원)."""
    total, block, man, won = 0.0, 0.0, False, False
    for num, unit in re.findall(r'(' + _NUM + r')?\s*(억|천|백|만|원)?', t):
        if not num and not unit:
            continue
        n = float(num.replace(',', '')) if num else None
        if unit in ('천', '백'):
            block += (1 if n is None else n) * (1000 if unit == '천' else 100)
        elif unit in ('만', '억'):
            total += (block + (n or 0) or 1) * (1e4 if unit == '만' else 1e8)
            block = 0.0
            man = man or unit == '만'
        elif unit == '원':
            total += block + (n or 0)
            block, won = 0.0, True
        else:
            block += n
    if block:
        total += block * (1 if man or won else 1e4)
    return total / 1e4


def _amounts_in(t):
    """글 속 금액 표현 [(만 원 값, 시작, 끝, 단위 없는 맨 숫자인지)] — 나이·연도·기간 숫자는 뺀다."""
    out = []
    for m in _AMT.finditer(t or ''):
        if _NOT_AMT.match(t, m.end()):
            continue
        v = _amount_value(m.group(0))
        if v > 0:
            out.append((v, m.start(), m.end(), not re.search(r'[억천백만원]', m.group(0))))
    return out


def _same(v, w, bare=False):
    """같은 금액인가 — 맨 숫자 다섯 자리 이상은 원 단위로도 본다('50,000'은 정규식이 5만 원으로 읽는다)."""
    return abs(v - w) < 0.6 or bare and v >= 10000 and abs(v / 10000 - w) < 0.06


def _derived(v, pos, parts):
    """다른 금액들로 만든 값인가 — 고객이 스스로 계산해 말한 결과('1년 합치면 1,560', '148만 5천 원 다 돌려받아요?',
    '330만 원 내는 거 맞아요? … 2,000만 원 해지하면'). 계산 입력이 아니라 결과를 옮긴 말이라 '못 읽음'으로 막지 않는다.
    ×12·×세율 값은 우연히 맞기 어려워 어디 있든 보고, 그냥 더한 값은 재료 뒤에 올 때만 본다('A에 300, B에 100, IRP 200'의
    300은 뒤 둘의 합과 같아도 입력이다)."""
    import itertools
    for k in range(1, min(len(parts), 4) + 1):
        for c in itertools.combinations(parts, k):
            s = sum(w for w, _ in c)
            for f in _DERIVE:
                if f == 1 and (k == 1 or any(p > pos for _, p in c)):
                    continue
                # 600원 안쪽만 같다고 본다 — 만 원 단위로 넉넉히 보면 '결정세액 30만 원'이 900×3.3%(29.7)와 우연히 맞았다
                if abs(v - s * f) < 0.06:
                    return True
    return False


def _norm(t):
    """정규식 읽기와 같은 표기로('육백만'→'600만') — spans 의 자리(s·e)가 이 글의 자리다."""
    try:
        import taxcalc
        return taxcalc.normalize(taxcalc.norm_ws(t))
    except Exception:
        return t


def _items(ext):
    """모델 금액 목록을 금액 하나씩으로 — 한 칸에 둘을 몰아 적기도 한다
    ({'text': '총급여 5000만원, 연금저축은 600만원', 'man': [5000, 600], 'use': ['소득', '납입']}). 몰아 적은 것을 첫 쓰임 하나로
    보면 600을 '소득'으로 견주어 헛경보가 났다(10-15 같은 뜻 다른 표현 F1). 원문 표현도 금액 자리마다 나눈다."""
    out = []
    for a in (ext or {}).get('amounts') or []:
        if not isinstance(a, dict):
            continue
        n = max([len(v) for v in a.values() if isinstance(v, (list, tuple))] or [0])
        if n <= 1:
            out.append(a)
            continue
        text = str(a.get('text') or '')
        pieces = [text[s:e] for _, s, e, _ in _amounts_in(text)]
        for i in range(n):
            b = {k: (v[i] if isinstance(v, (list, tuple)) and len(v) == n else v) for k, v in a.items()}
            b['text'] = pieces[i] if len(pieces) == n else ''
            out.append(b)
    return out


def unread(parsed, ext, q):
    """HCX-005 가 쓰임(_KNOWN_USE)을 붙인 금액인데 정규식이 아예 못 읽었거나(spans 에 그 값이 없음) 읽고도 쓰임을 못 붙인
    (역할 None) 것 → [(금액, '계산에 못 넣음', 모델 쓰임, '못 읽음', 원문 표현)].
    왜 — 쓰임만 견주면 정규식이 버린 금액은 견줄 짝이 없어 지나간다(10-15 현실 질문 120개: 틀린 숫자 12개 중 3개만 막음.
    'A에 300, B에 400'의 300, '연금저축 월 30, IRP 월 20'의 30, '8백50만 원'을 50으로 읽음, '공제 안 받고 넣은 게 3천').
    모델이 지어낸 금액·계산한 금액(질문에 없는 값), 다른 사람 돈, 고객이 스스로 계산해 말한 결과는 막지 않는다 — 헛경보."""
    out = []
    if not ext or not q:
        return out
    qa = _amounts_in(_norm(q))
    spans = parsed.get('spans') or []
    used = [(s['v'], s['s']) for s in spans if s.get('role')]
    cands = []
    for a in _items(ext):
        # 원문 표현의 값이 질문에 있는 금액이어야 한다 — 숫자 없는 표현('총급여')·모델이 계산한 값('1년에 960만 원')은 자리를 못 찾는다
        mine = [(v, b) for v, _, _, b in _amounts_in(_norm(str(a.get('text') or '')))]
        hits = [(v, p) for v, _ in mine for w, p, _, _ in qa if _same(v, w)]
        if len(mine) != 1 or not hits:
            continue                       # 표현 하나에 금액이 둘 이상이면 어느 것인지 모른다
        cands.append((a, hits[0][0], hits[0][1], mine[0][1]))
    # 결과값 재료 — 정규식이 쓴 금액과 모델이 쓰임을 붙인 금액. 같은 금액을 두 번 넣으면 2,000+2,000 같은 없는 합이 생긴다
    parts = []
    for w, p in used + [(v, p) for a, v, p, _ in cands if _str(a.get('use')) in _KNOWN_USE]:
        if not any(_same(w, x) for x, _ in parts):
            parts.append((w, p))
    for a, v, pos, bare in cands:
        use = _str(a.get('use')) or '모름'
        # 소득은 못 읽어도 틀린 숫자가 안 나온다 — 세액공제 계산이 소득을 모르면 두 공제율(16.5%·13.2%)을 다 보인다. 정규식이 소득을
        # 하나 읽었는데 모델이 소득을 더 뽑으면 지난해·배우자 소득이다('원래는 6천이었고요', '와이프는 4천')
        if use not in _KNOWN_USE or use == '소득' or _str(a.get('whose')) == '다른사람':
            continue
        if any(_same(v, w, bare) for w, _ in used):
            continue                       # 정규식도 그 금액을 읽어 계산에 썼다(쓰임 다름은 compare 가 본다)
        if _derived(v, pos, [x for x in parts if not _same(x[0], v)]):
            continue
        if any(_same(v, x[0]) for x in out):
            continue
        out.append((v, '계산에 못 넣음', use, '못 읽음', str(a.get('text') or '').strip()))
    return out


def compare(parsed, ext, q=None):
    """[(금액, 정규식 읽기, 모델 읽기, 무엇이 다른지)] — 계산에 쓰인 금액, 그리고 q 를 주면 정규식이 못 읽은 금액(unread)."""
    diffs = []
    if not ext:
        return diffs
    amts = _items(ext)
    for s in parsed.get('spans') or []:
        mine = _span_use(s)
        if not mine:
            continue
        cand = [a for a in amts if any(abs(v - float(s['v'])) < 0.6 for v in _values(a))]
        if not cand:
            continue                       # 모델이 그 금액을 못 뽑았으면 견줄 수 없다(막지 않음)
        a = cand[0]
        use = _str(a.get('use')) or '모름'
        if use in _KNOWN_USE and use != mine and not _COMPAT.get((mine, use)):
            diffs.append((s['v'], mine, use, '쓰임'))
            continue
        # 정규식이 남의 돈을 계산에서 뺐어도 되묻는다 — 계산 블록에 뺐다는 말이 없어 '둘이 합쳐·각자 공제 얼마'에 본인 몫만 답처럼
        # 놓였다(10-15 검증: '남편은 IRP에 900, 저는 연금저축에 600 … 둘이 합쳐' → 99만 원). 본인 몫만 묻는지는 글자 단서로 못 가른다
        if _str(a.get('whose')) == '다른사람' and mine in ('납입', '인출', '연금수령'):
            diffs.append((s['v'], mine + '(본인 돈)', '다른 사람 돈', '주인'))
            continue
        if mine in ('납입', '연금수령'):
            per = _str(a.get('period')) or '모름'
            # 모델이 man 칸을 1년 치로 바꿔 적으면('월 110' → man 1320, '1년') 기간 칸은 바꾼 값의 기간이다 — 원문 표현은 매달이다
            tv = [v for v, _, _, _ in _amounts_in(str(a.get('text') or ''))]
            m12 = a.get('man')
            if per == '1년' and len(tv) == 1 and isinstance(m12, (int, float)) and \
                    any(abs(m12 - tv[0] * 12 * k) < 0.6 for k in (1, 10000)):
                per = '모름'
            # 정규식이 금액 바로 앞 기간 말(월·매년·1년에)을 읽었으면, 모델 기간 칸은 모델이 옮긴 원문 표현에 그 기간 말이 있을 때만
            # 믿는다 — '매년 600씩'의 600을 표현 '600'만 옮기고 '매달'이라 적어 맞는 답을 막았다(10-15 실측 cancel-15).
            # 다만 정규식 기간이 먼 앞말('올해·매년 연금저축에 30씩 매달')에서 왔거나 금액 바로 뒤에 모델 기간 쪽 말이 붙어 있으면
            # ('IRP에 매년 40씩 다달이') 모델 기간을 그대로 둔다 — 버리면 1년으로 잘못 읽은 월 납입이 그대로 나갔다
            # (10-15 검증: 4만 9,500원, 맞는 값 59만 4천 원). q 가 없으면 앞뒤 말을 못 보므로 버리지 않는다(예전 동작)
            if q and s.get('period') in _PRE_PERIOD and per in _PER_WORD and not _PER_WORD[per].search(str(a.get('text') or '')):
                qn = _norm(q)
                ss, se = int(s.get('s') or 0), int(s.get('e') or 0)
                if _PRE_PERIOD[s['period']].search(qn[max(0, ss - 14):ss]) and not _POST_PER[per].match(qn, se):
                    per = '모름'
            mon = s.get('period') == 'monthly' or any(c.get('monthly') and abs(c['v'] - s['v']) < 0.6 or
                                                      (c.get('raw') and abs(c['raw'] - s['v']) < 0.6 and c.get('monthly'))
                                                      for c in parsed.get('contrib') or [])
            if per == '매달' and not mon and s.get('period') != 'monthly':
                diffs.append((s['v'], mine + ' 1년', mine + ' 매달', '기간'))
            elif per == '1년' and mon:
                diffs.append((s['v'], mine + ' 매달', mine + ' 1년', '기간'))
    for key, mine in (('age', parsed.get('age')), ('receive_age', parsed.get('rate_age'))):
        theirs = ext.get(key)
        if isinstance(theirs, (int, float)) and mine and abs(float(theirs) - float(mine)) >= 1:
            diffs.append((None, f'{"나이" if key == "age" else "받는 나이"} {mine:g}세', f'{theirs:g}세', key))
    if q:
        # answer.py 는 diffs 를 네 칸으로 푼다 — 원문 표현(다섯째 칸)은 message 에서 unread 를 다시 불러 얻는다
        diffs += [u[:4] for u in unread(parsed, ext, q) if not any(d[0] is not None and _same(d[0], u[0]) for d in diffs)]
    return diffs


# 못 읽은 금액을 되물을 때 쓰임을 고객 말로
_PLAIN = {'납입': '넣는 돈', '인출': '빼는 돈', '연금수령': '연금으로 받는 돈', '평가액': '계좌에 있는 돈', '소득': '소득',
          '퇴직금': '퇴직금', '퇴직소득세': '퇴직소득세', 'ISA': 'ISA에서 옮기는 돈'}


def message(diffs, parsed, said=None):
    """said: {금액: 질문 속 원문 표현} — 못 읽은 금액을 고객이 쓴 말 그대로 짚으려고(unread 의 다섯째 칸)."""
    read = []
    for s in parsed.get('spans') or []:
        u = _span_use(s)
        if u:
            read.append(f"{_won(s['v'])} = {u}" + (' (매달)' if s.get('period') == 'monthly' else ''))
    only_unread = all(d[3] == '못 읽음' for d in diffs)
    lines = [('질문 속 금액 가운데 제가 계산에 넣지 못한 것이 있어서' if only_unread else '질문 속 숫자가 두 가지로 읽혀서')
             + ', 틀린 금액으로 계산하지 않으려고 먼저 확인할게요.', '']
    if read:
        lines.append('**제가 읽은 것**: ' + ' · '.join(read))
    alt = []
    for v, mine, theirs, what in diffs:
        if what == '못 읽음':
            t = next((x for w, x in (said or {}).items() if _same(w, v)), '')
            alt.append((f"'{t}'({_won(v)})" if t and _won(v) not in t else f"'{t or _won(v)}'")
                       + f" — 제가 계산에 넣지 못했어요({_PLAIN.get(theirs, theirs)}인 것 같아요)")
            continue
        alt.append(f"{_won(v) if v is not None else ''} {mine} ↔ {theirs}".strip())
    lines.append('**다르게 읽힐 수 있는 곳**: ' + ' · '.join(alt))
    lines += ['', '금액마다 무엇인지 짧게 덧붙여 다시 말씀해 주세요. 예: ' + _example(diffs)]
    return '\n'.join(lines)


# 다르게 읽힌 쓰임에 맞는 예시 — 늘 세액공제 예시를 보이면 연금 받는 질문에 엉뚱하다
_EXAMPLES = [
    ('연금수령', "'만 65세예요. 연금저축에서 1년에 1,000만 원을 연금으로 받으면 세금이 얼마예요?'"),
    ('인출', "'만 50세예요. 연금저축에서 2,000만 원을 해지해 찾으면 세금이 얼마예요?'"),
    ('퇴직금', "'월급에서 모은 제 돈 300만 원을 IRP에 넣으면 세액공제 얼마예요?' — 퇴직금을 옮기는 거라면 '퇴직금 300만 원을 IRP로 옮겼어요'"),
    ('평가액', "'IRP에 지금 1억 원이 있고 올해 1,800만 원을 빼면 세금이 얼마예요? 만 62세'"),
    ('나이', "'지금 만 62세이고, 연금은 65세부터 받을 거예요'"),
]


def _example(diffs):
    said = ' '.join(f'{a} {b}' for _, a, b, _ in diffs)
    for key, ex in _EXAMPLES:
        if key in said and not ('납입' in said and key in ('나이',)):
            return ex
    return "'연금저축에 1년에 600만 원 넣었고, IRP에 300만 원 더 넣으면 세액공제 얼마예요?'"


def check(q, calc):
    """계산 블록에 숫자가 있을 때만 이중 확인. 다르면 {'diffs','message','sec'}, 같거나 확인 못 하면 None."""
    if not calc or not any(b.get('numbers') for b in calc.get('blocks') or []):
        return None
    t = time.time()
    try:
        ext = extract(q)
    except Exception:
        return None                        # 모델이 안 되면 막지 않는다(지금 동작 유지)
    p = calc.get('parsed') or {}
    try:
        diffs = compare(p, ext, q)
        said = {u[0]: u[4] for u in unread(p, ext, q)}
    except Exception:
        return None                        # 모델 출력 모양이 이상하면 막지 않는다
    if not diffs:
        return None
    return {'diffs': diffs, 'message': message(diffs, p, said), 'sec': round(time.time() - t, 1), 'ext': ext}
