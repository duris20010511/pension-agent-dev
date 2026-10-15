# -*- coding: utf-8 -*-
"""온톨로지 판독 기준표 — 계산 블록이 쓴 규칙의 조건을 질문과 대조한다.

  계산 블록(taxcalc.compute) 숫자 블록의 규칙 번호(rids)
    → data/ontology.json rules[].conditions + rules[].read_conditions (attr · test · waived_by · cmp)
    → 그 속성(attributes[].read: 모델 칸 key · 허용 값 · 근거 말 · 계산기 쪽 값)
  모델(HCX-005)은 그 속성만 질문에서 읽고(프롬프트 칸 목록·허용 값을 온톨로지에서 만든다), 계산기 쪽 값(calc['parsed'] 매핑 →
  블록이 밝힌 것 → 가정 기본값)과 조건마다 견준다. 다르면 계산기 숫자를 내지 않고 온톨로지의 ask 말로 되묻는다.

  코드에 있는 것은 비교 방식뿐이다 — 열거값 같음(eq·in·same), 수 범위 시험(min·max·bands, plus 로 더해서), 있음/없음(same),
  묻는 일을 다 계산했나(covers). 칸·값·근거 말·계산기 쪽 값은 모두 ontology.json 에 있어서, 규칙에 조건을 하나 더하면
  프롬프트 칸과 검사가 함께 는다.

  read_conditions 는 conditions 와 같은 꼴이지만 따로 둔다 — conditions 에 넣으면 ontology.path 의 '빠진 속성 확인'이 그 속성을
  '가정'으로 화면에 띄운다(동결된 경로 문구가 바뀐다).

견주는 원칙(10-15 연습 판정에서 배운 것)
  - 모델 값은 질문 글자 근거(read 의 evidence — 근거 말이 질문·묻는 문장·한 문장 안에 있어야)가 있을 때만 믿는다.
    HCX-005 는 칸을 헷갈리고 숫자 비교·달 수 세기를 못 한다 — 수 시험은 코드가 하고, 문턱은 계산기 값으로 건다.
  - 계산기가 이미 밝힌 것(두 사람 각자·결정세액)은 블록 글로 계산기 쪽 값이 되므로 다르다고 보지 않는다.
  - 규칙의 판정(조건을 모두 채우나·어느 세율 구간인가)이 같으면 칸 값이 달라도 숫자는 같다 — 판정이 갈릴 때만 다르다고 본다.
  - 모델이 안 되거나 출력이 깨지면 막지 않는다(flag False).
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ONTO_PATH = os.path.join(os.path.dirname(HERE), 'data', 'ontology.json')
MODEL = 'HCX-005'

_CACHE = {}


def load(onto=None):
    """ontology.json(원본 그대로). dict 를 주면 그대로, 경로를 주면 그 파일 — 시험이 임시 사본을 쓴다."""
    if isinstance(onto, dict):
        return onto
    path = onto or ONTO_PATH
    try:
        mt = os.path.getmtime(path)
    except OSError:
        mt = None
    hit = _CACHE.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    o = json.load(open(path, encoding='utf-8'))
    _CACHE[path] = (mt, o)
    return o


# ---------------------------------------------------------------------------
# 질문 글 — 문장·묻는 문장
# ---------------------------------------------------------------------------
def _sentences(q):
    """물음표·느낌표·줄바꿈, 뒤에 빈칸이 오는 마침표('16.5%'의 점은 아니다), 빈칸 앞의 '…요'로 나눈다.
    '…다 '로는 나누지 않는다 — '2,400 다 해지하면', '거보다 세금이'의 '다'는 문장 끝이 아니다."""
    return [s.strip() for s in re.split(r'(?<=[?!\n])|(?<=\.)(?=\s|$)|(?<=[가-힣]요)\s+', q or '') if s and s.strip()]


def _ask_part(q):
    """고객이 묻는 문장 — 물음표가 있는 문장들, 없으면 마지막 문장."""
    ss = _sentences(q)
    asked = [s for s in ss if '?' in s]
    return ' '.join(asked) if asked else (ss[-1] if ss else '')


def _ws(s):
    return re.sub(r'\s+', '', s or '')


# ---------------------------------------------------------------------------
# 무엇을 대조하나 — 숫자 블록의 규칙 → 조건 → 속성
# ---------------------------------------------------------------------------
def _cmp_of(c, attr):
    """조건의 비교 — 조건에 cmp 가 있으면 그것, 없으면 속성 read 의 기본, 그것도 없으면 '같게 읽었나'."""
    return c.get('cmp') or (attr.get('read') or {}).get('cmp') or {'same': True}


def _model_attr(a):
    return bool(a and (a.get('read') or {}).get('key'))


def consult(calc, onto=None):
    """→ (숫자 블록, 규칙 번호, [(규칙, 조건, 비교)], 모델이 읽을 속성 id, 속성 표)."""
    o = load(onto)
    attrs = {a['id']: a for a in o.get('attributes', [])}
    rules = {r['id']: r for r in o.get('rules', [])}
    blocks = [b for b in (calc or {}).get('blocks') or [] if b.get('numbers')]
    rids = list(dict.fromkeys(r for b in blocks for r in b.get('rids') or []))
    conds = []
    for rid in rids:
        r = rules.get(rid) or {}
        for c in list(r.get('conditions') or []) + list(r.get('read_conditions') or []):
            a = attrs.get(c.get('attr'))
            if _model_attr(a):                    # 질문에서 읽을 수 없는 속성(평가액·연차 …)은 crosscheck·되묻기 몫
                conds.append((rid, c, _cmp_of(c, a)))
    ids = []
    for _, c, cmp in conds:
        for aid in (c['attr'], cmp.get('plus')):
            if aid and _model_attr(attrs.get(aid)) and aid not in ids:
                ids.append(aid)
    return blocks, rids, conds, ids, attrs


# ---------------------------------------------------------------------------
# 계산기 쪽 값 — read.engine: parsed 매핑 → 블록이 밝힌 것 → 가정 기본값
# ---------------------------------------------------------------------------
def _items(x):
    if isinstance(x, dict):
        return list(x.keys())
    if isinstance(x, (list, tuple, set, frozenset)):
        return list(x)
    return []


def _source(s, attr, ctx):
    p = ctx['parsed']
    if 'default' in s:
        return s['default']
    if s.get('rules'):
        # 묻는 일 — 값마다 그 일을 계산하는 규칙이 숫자 블록에 있으면 계산기가 그 일을 계산한 것
        return [v['v'] for v in (attr.get('read') or {}).get('values') or []
                if set(v.get('rules') or []) & set(ctx['rids'])] or None
    if 'block' in s:
        return s.get('value') if any(re.search(s['block'], t) for t in ctx['texts']) else None
    if 'topic' in s:
        return s.get('value') if any(s['topic'] in (b.get('topic') or '') for b in ctx['blocks']) else None
    if 'parsed' in s:
        x = p.get(s['parsed'])
        if x is None or x is False or x == '' or x == [] or x == {}:
            return None
        if 'has' in s:
            return s.get('value', True) if s['has'] in _items(x) else None
        if 'where' in s:
            hit = any(isinstance(i, dict) and all(i.get(k) == v for k, v in s['where'].items()) for i in _items(x))
            return s.get('value') if hit else None
        if 'field' in s:
            out = [s.get('map', {}).get(i.get(s['field']), None) if s.get('map') else i.get(s['field'])
                   for i in _items(x) if isinstance(i, dict)]
            return list(dict.fromkeys(v for v in out if v is not None)) or None
        if 'index' in s:
            try:
                v = x[s['index']]
            except (IndexError, KeyError, TypeError):
                return None
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        if s.get('int'):
            return x if isinstance(x, int) and not isinstance(x, bool) else None   # '60대'(튜플)는 모름으로 본다
        if 'map' in s:
            return s['map'].get(x) if isinstance(x, str) else None
        if 'value' in s:
            return s['value']
        return x
    return None


def engine_value(attr, ctx):
    """→ (값, 설명, 가정인가). 값이 없으면 (None, None, False) — 계산기가 그 칸을 쓰지 않았거나 못 읽었다."""
    for s in (attr.get('read') or {}).get('engine') or []:
        try:
            v = _source(s, attr, ctx)
        except Exception:
            v = None
        if v is not None:
            return v, s.get('say'), 'default' in s
    return None, None, False


# ---------------------------------------------------------------------------
# 모델 읽기 — 프롬프트는 온톨로지 속성에서 만든다
# ---------------------------------------------------------------------------
def _vtext(r):
    vals = [v['v'] for v in r.get('values') or []]
    if r.get('type') == 'list':
        return '[' + '|'.join(vals) + ', ...] 또는 []'
    if r.get('type') == 'int':
        return '정수 또는 null'
    return '|'.join(vals) + ' 또는 null'


def build_prompt(ids, onto=None, n_examples=2):
    o = load(onto)
    attrs = {a['id']: a for a in o.get('attributes', [])}
    use = [attrs[i] for i in ids if _model_attr(attrs.get(i))]
    sk = ', '.join(f'"{a["read"]["key"]}": {{"q": "근거 글", "v": {_vtext(a["read"])}}}' for a in use)
    lines = ['너는 연금 상담 질문을 정해진 칸에 맞춰 읽는다. 계산하지 말고, 질문에 적힌 것만 적는다.',
             '칸마다 {"q": 근거 글, "v": 값}을 적는다. q 를 먼저, 반드시 적는다 — 그 값의 근거가 된 질문 글을 고치지 말고 그대로 옮긴다. '
             '질문에 근거가 없으면 {"q": "", "v": null}.',
             '출력은 JSON 하나만:', '{' + sk + '}', '칸 뜻:']
    for a in use:
        r = a['read']
        vh = [f"{v['v']}={v['hint']}" for v in r.get('values') or [] if v.get('hint')]
        lines.append(f"- {r['key']}({a['label']}): {r.get('hint', '')}" + (' — ' + ' / '.join(vh) if vh else ''))
    # 보기 — 이번 칸과 겹치는 것이 많은 순서로. 보기에 없는 칸은 비워 둔다(근거 없으면 null 을 보여 준다)
    exs = sorted((o.get('reading') or {}).get('examples') or [],
                 key=lambda e: -sum(1 for a in use if a['id'] in e.get('read', {})))[:n_examples]
    for e in exs:
        out = {a['read']['key']: e.get('read', {}).get(a['id'], {'q': '', 'v': [] if a['read'].get('type') == 'list' else None})
               for a in use}
        lines.append('예) 질문: ' + e['q'])
        lines.append(json.dumps(out, ensure_ascii=False))
    return '\n'.join(lines)


def _repair(s):
    """깨진 JSON 을 너그럽게 — 코드 울타리, 파이썬 None/True, 홑따옴표, 끝 쉼표, 따옴표 없는 글 값, 숫자 뒤 단위."""
    s = re.sub(r'```(?:json)?', '', s)
    s = re.sub(r'\bNone\b', 'null', s)
    s = re.sub(r'\bTrue\b', 'true', s)
    s = re.sub(r'\bFalse\b', 'false', s)
    s = re.sub(r"'([^'\n]*)'", r'"\1"', s)
    s = re.sub(r'("[A-Za-z_]+"\s*:[ \t]*)(-?\d+(?:\.\d+)?)[ \t]*[가-힣]+', r'\1\2', s)
    s = re.sub(r',\s*([}\]])', r'\1', s)
    s = re.sub(r'("[A-Za-z_]+"\s*:)[ \t]*(?![ \t]|null\b|true\b|false\b|-?\d|\[|\{|")([^,}\n]+?)(\s*[,}\n])',
               lambda m: m.group(1) + ' ' + json.dumps(m.group(2).strip(), ensure_ascii=False) + m.group(3), s)
    return s


_VAL = r'("(?:[^"\\]|\\.)*"|\[[^\]]*\]|-?\d+(?:\.\d+)?|null|true|false)'


def _keywise(s):
    """마지막 수단 — 칸마다 따로. {"q": …, "v": …} 꼴과 값만 적은 꼴 둘 다."""
    out = {}
    for m in re.finditer(r'"([A-Za-z_]+)"\s*:\s*\{([^{}]*)\}', s):
        inner = {}
        for k in re.finditer(r'"(q|v)"\s*:\s*' + _VAL, m.group(2)):
            try:
                inner[k.group(1)] = json.loads(k.group(2))
            except Exception:
                pass
        out[m.group(1)] = inner
    for m in re.finditer(r'"([A-Za-z_]+)"\s*:\s*' + _VAL, s):
        if m.group(1) not in out and m.group(1) not in ('q', 'v'):
            try:
                out[m.group(1)] = json.loads(m.group(2))
            except Exception:
                pass
    return out or None


def parse_reply(text):
    """모델 답 → {key: {'q','v'}} 또는 None."""
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m:
        return None
    s = m.group(0)
    d = None
    for t in (s, _repair(s)):
        try:
            d = json.loads(t)
            if isinstance(d, dict):
                break
        except Exception:
            d = None
    if not isinstance(d, dict):
        d = _keywise(_repair(s))
    if not isinstance(d, dict):
        return None
    out = {}
    for k, v in d.items():
        if isinstance(v, dict) and not ({'q', 'v'} & set(v)) and len(v) == 1 and str(next(iter(v))).strip():
            # HCX-005 가 {"근거 글": 값} 꼴로 적기도 한다 — 키가 근거 글, 값이 칸 값
            (qq, vv), = v.items()
            out[k] = {'q': str(qq), 'v': vv}
        elif isinstance(v, dict):
            out[k] = {'q': str(v.get('q') or ''), 'v': v.get('v')}
        else:
            out[k] = {'q': '', 'v': v}                # 값만 적은 칸 — 근거 글은 질문 글자로 다시 찾는다
    return out


def read_question(q, ids, onto=None, chat=None, timeout=20):
    """모델이 질문에서 칸을 읽는다 → {key: {'q','v'}} 또는 None(모델 실패·깨진 출력). chat 을 주면 그것을 부른다(시험)."""
    if not ids:
        return {}
    msgs = [{'role': 'system', 'content': build_prompt(ids, onto)}, {'role': 'user', 'content': '질문: ' + (q or '')}]
    if chat is None:
        import clova
        out = clova.chat(msgs, temperature=0.0, max_tokens=min(900, 120 + 70 * len(ids)), model=MODEL,
                         timeout=timeout, retries=2)
    else:
        out = chat(msgs)
    return parse_reply(out)


# ---------------------------------------------------------------------------
# 모델 값 다듬기와 질문 글자 근거
# ---------------------------------------------------------------------------
def _key(s):
    return re.sub(r'[\s·()]+', '', str(s))


def _clean(attr, raw):
    """모델 값 → 속성 type 에 맞는 값(모르면 None)."""
    r = attr.get('read') or {}
    t = r.get('type')
    if raw is None:
        return None
    if t == 'int':
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            return int(raw)
        m = re.search(r'[-+]?\d+', str(raw))
        return int(m.group(0)) if m else None
    allowed = {_key(v['v']): v['v'] for v in r.get('values') or []}
    if t == 'list':
        xs = raw if isinstance(raw, list) else re.split(r'[,|/·]+', str(raw))
        out = [allowed.get(_key(x)) for x in xs if x is not None]
        return list(dict.fromkeys(x for x in out if x)) or None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return allowed.get(_key(raw)) if raw is not None else None


def _vspec(attr, v):
    return next((x for x in (attr.get('read') or {}).get('values') or [] if x['v'] == v), {})


def _as(attr, v):
    """값의 묶음('요양' → '부득이') — 비교는 묶음으로."""
    return _vspec(attr, v).get('as', v) if isinstance(v, str) else v


def _num_ok(v, t):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    return ('min' not in t or v >= t['min']) and ('max' not in t or v <= t['max'])


def _hit(s, v, ctx):
    """근거 한 갈래 → 맞은 글(또는 None)."""
    if 'if' in s and not _num_ok(v, s['if']):
        return None
    if 'engine_attr' in s:
        ev = ctx['engine'](s['engine_attr'])
        return f"계산기가 읽은 값 {ev:g}" if _num_ok(ev, s) else None
    cues = s.get('cue')
    cues = [cues] if isinstance(cues, str) else list(cues or [])
    if s.get('num') and isinstance(v, (int, float)):
        cues.append(s['num'].replace('{v}', re.escape(str(int(v)))))
    if not cues:
        return None
    scope = s.get('scope', 'q')
    texts = [ctx['q']] if scope == 'q' else [ctx['ask']] if scope == 'ask' else ctx['sents']
    # 계산기는 줄임말을 풀어 읽는다('연저'→연금저축) — 근거도 푼 글에서 함께 찾아야 '질문에 없는 계좌를 적었다'는 헛경보가 안 난다
    # (10-15 서버 실측: '연저 600 넣었어. 12월에 irp 더 넣으면' 13/13 되묻기)
    norm = ctx.get('norm') or {}
    texts = texts + [x for x in norm.get(scope, []) if x not in texts]
    for t in texts:
        ms = [re.search(c, t, re.I) for c in cues]
        if all(ms) and not (s.get('unless') and re.search(s['unless'], t, re.I)):
            return t if scope == 'sentence' else ms[-1].group(0).strip()
    return None


def evidence(attr, v, ctx, first=True):
    """모델 값 v 에 질문 글자 근거가 있나 → 맞은 글 또는 None. 정수 칸은 read.evidence, 열거 칸은 값마다의 evidence
    (목록 칸의 둘째 값부터는 also — '견주는 말'처럼 함께 묻는다는 근거가 따로 있어야)."""
    r = attr.get('read') or {}
    if r.get('type') == 'int':
        specs = r.get('evidence') or []
    else:
        vs = _vspec(attr, v)
        specs = (vs.get('also') if not first and vs.get('also') else vs.get('evidence')) or []
    for s in specs:
        try:
            h = _hit(s, v, ctx)
        except re.error:
            h = None
        if h:
            return h
    return None


def _derive(attr, v, q):
    """read.derive — 모델이 그 칸을 채웠으면 값은 질문 글에서 셈('만기 3개월 지났' → 90일). 달·날 셈은 코드가 한다."""
    d = (attr.get('read') or {}).get('derive')
    if not d or v is None:
        return v
    m = re.search(d['re'], q or '')
    if m and m.group(2) in d.get('units', {}):
        return int(m.group(1)) * d['units'][m.group(2)]
    return v


def _quote(mq, h, ctx):
    """되묻기에 보일 근거 글. 모델 근거 글이 질문에 있고 코드가 찾은 근거 글자와 겹치면(한쪽이 다른 쪽을 품으면) 그대로,
    아니면 그 글자가 든 문장. HCX-005 는 다른 사람 말('형이 70 넘으면')을 근거로 적기도 한다 — 되묻기에 엉뚱한 글을 싣지 않게."""
    hit_in_q = bool(h) and _ws(h) in _ws(ctx['q'])
    if mq and _ws(mq) in _ws(ctx['q']) and len(_ws(mq)) >= 4 and \
            (not hit_in_q or _ws(h) in _ws(mq) or _ws(mq) in _ws(h)):
        return mq.strip()
    if hit_in_q:
        sent = [s for s in ctx['sents'] if _ws(h) in _ws(s)]
        if sent:
            return min(sent, key=len)
    return (mq.strip() if mq and _ws(mq) in _ws(ctx['q']) else None) or h


def _join_quotes(qs, ctx):
    """근거 글 여럿을 하나로 — 한쪽이 다른 쪽을 품으면 긴 것만, 아니면 질문에 나온 순서로 ' … '로 잇는다.
    나이와 해가 함께 다를 때 되묻기에 '지금 69세예요.'만 보이고 '내년부터'가 빠지지 않게."""
    out = []
    for x in qs:
        if not x:
            continue
        if any(_ws(x) in _ws(y) for y in out):
            continue
        out = [y for y in out if _ws(y) not in _ws(x)] + [x]
    qw = _ws(ctx['q'])
    out.sort(key=lambda x: qw.find(_ws(x)) if _ws(x) in qw else len(qw))
    return ' … '.join(out) if out else None


def model_values(reading, ids, attrs, ctx):
    """→ {속성: (값, 근거 글)} — 근거가 있는 값만. 목록 칸은 근거 있는 값들의 목록."""
    out = {}
    for aid in ids:
        a = attrs[aid]
        cell = (reading or {}).get(a['read']['key'])
        if not isinstance(cell, dict):
            continue
        v = _clean(a, cell.get('v'))
        if v is None:
            continue
        mq = cell.get('q') or ''
        if a['read'].get('type') == 'list':
            keep = [(x, evidence(a, x, ctx, first=(i == 0))) for i, x in enumerate(v)]
            keep = [(x, h) for x, h in keep if h]
            if keep:
                out[aid] = ([x for x, _ in keep], _quote(mq, keep[0][1], ctx))
            continue
        v = _derive(a, v, ctx['q'])
        h = evidence(a, v, ctx)
        if h:
            out[aid] = (v, _quote(mq, h, ctx))
    return out


# ---------------------------------------------------------------------------
# 대조
# ---------------------------------------------------------------------------
def _say(attr, v):
    r = attr.get('read') or {}
    if v is None:
        return '모름'
    if isinstance(v, list):
        return ' · '.join(_say(attr, x) for x in v)
    if r.get('type') == 'int':
        tpl = r.get('say_neg') if v < 0 and r.get('say_neg') else r.get('say_zero') if v == 0 and r.get('say_zero') else r.get('say')
        try:
            return (tpl or '{v}').format(v=v, n=abs(v))
        except (KeyError, ValueError, IndexError):
            return str(v)
    return _vspec(attr, v).get('say') or str(v)


def _outcome(cmp, v):
    """조건 시험 하나 — 참/거짓, bands 면 구간 번호. 값이 없으면 None."""
    if v is None:
        return None
    if 'bands' in cmp:
        return sum(1 for b in cmp['bands'] if v >= b) if isinstance(v, (int, float)) else None
    ok = True
    if 'eq' in cmp:
        ok = ok and v == cmp['eq']
    if 'in' in cmp:
        ok = ok and v in cmp['in']
    if 'not_in' in cmp:
        ok = ok and v not in cmp['not_in']
    if 'min' in cmp or 'max' in cmp:
        ok = ok and _num_ok(v, cmp)
    return bool(ok)


_VERDICT = ('eq', 'in', 'not_in', 'min', 'max', 'bands')


def _facts(attrs, val_of):
    """waived_by 가 보는 사실 — 값에 facts 가 달린 속성('퇴직금 있음' → retire)."""
    out = set()
    for a in attrs.values():
        v = val_of(a['id'])
        for x in (v if isinstance(v, list) else [v]):
            out |= set(_vspec(a, x).get('facts') or []) if isinstance(x, str) else set()
    return out


def compare(q, calc, reading, onto=None):
    """→ (diffs, consulted). reading 은 read_question 결과. 모델 값이 없는 칸은 계산기 값과 같다고 본다."""
    blocks, rids, conds, ids, attrs = consult(calc, onto)
    consulted = {'rules': rids, 'attrs': ids}
    if not conds:
        return [], consulted
    ctx = {'q': q or '', 'ask': _ask_part(q), 'sents': _sentences(q), 'parsed': (calc or {}).get('parsed') or {},
           'blocks': blocks, 'rids': rids, 'texts': [(b.get('text') or '').split('[원문]')[0] for b in blocks]}
    try:
        import taxcalc
        qn = taxcalc.norm_ws(taxcalc.normalize(q or ''))
    except Exception:
        qn = q or ''
    if qn != (q or ''):
        ctx['norm'] = {'q': [qn], 'ask': [_ask_part(qn)], 'sentence': _sentences(qn)}
    E = {}

    def eng(aid):
        if aid not in E:
            E[aid] = engine_value(attrs[aid], ctx) if aid in attrs else (None, None, False)
        return E[aid]
    ctx['engine'] = lambda aid: eng(aid)[0]
    M = model_values(reading, ids, attrs, ctx)
    facts_e = _facts(attrs, lambda aid: eng(aid)[0])
    facts_m = facts_e | _facts({k: attrs[k] for k in M}, lambda aid: M[aid][0])

    diffs = []

    def add(aid, rid, engine, question, quote):
        if any(d['attr'] == aid for d in diffs):
            return
        diffs.append({'attr': aid, 'label': attrs[aid]['label'], 'rule': rid, 'engine': engine, 'question': question,
                      'quote': quote})

    def e_text(aid):
        v, say, assumed = eng(aid)
        return (say if say and (assumed or isinstance(v, str)) else _say(attrs[aid], v)) if v is not None else \
            (attrs[aid].get('read') or {}).get('unknown_say') or '질문에서 못 읽음'

    covered_done = False
    for rid in rids:
        rows = []
        for r2, c, cmp in conds:
            if r2 != rid:
                continue
            aid = c['attr']
            a = attrs[aid]
            if cmp.get('covers'):
                if covered_done:
                    continue
                covered_done = True
                _covers(a, rid, eng(aid)[0] or [], M.get(aid), ctx, add)
                continue
            if cmp.get('same'):
                _same(a, rid, eng(aid), M.get(aid), ctx, add, e_text)
                continue
            if not any(k in cmp for k in _VERDICT):
                continue
            ev_, _, _ = eng(aid)
            mv = M.get(aid)
            plus = cmp.get('plus')
            pe = eng(plus)[0] if plus else None
            pm = M.get(plus) if plus else None
            if ev_ is None:
                # 계산기가 못 읽은 값을 질문은 말한다 — read.engine_unknown 이 'diff' 인 칸만(나이: 구간을 늘어놓은 블록)
                if mv and (a.get('read') or {}).get('engine_unknown') == 'diff':
                    add(aid, rid, e_text(aid), _say(a, mv[0]), mv[1])
                continue
            m_base = mv[0] if mv else ev_
            m_plus = pm[0] if pm else pe
            e_eff = _as(a, ev_) if not plus else ev_ + (pe or 0)
            m_eff = _as(a, m_base) if not plus else m_base + (m_plus or 0)
            w = c.get('waived_by')
            oe = True if w and w in facts_e else _outcome(cmp, e_eff)
            om = True if w and w in facts_m else _outcome(cmp, m_eff)
            rows.append((aid, cmp, oe, om, mv, pm, ev_, pe, m_base, m_plus))
        # 규칙 판정 — 거짓 조건이 하나라도 있으면 '안 됨', 아니면 구간 번호들. 판정이 같으면 숫자도 같다
        def verdict(i):
            outs = [(row[1], row[i]) for row in rows if row[i] is not None]
            if any(o is False for c_, o in outs if 'bands' not in c_):
                return 'FAIL'
            return tuple(o for c_, o in outs if 'bands' in c_)
        if verdict(2) == verdict(3):
            continue
        for aid, cmp, oe, om, mv, pm, ev_, pe, m_base, m_plus in rows:
            if oe == om:
                continue
            a = attrs[aid]
            if pm and (not mv or mv[0] == ev_):
                # 값(나이)은 같게 읽었고 해만 다르다 — '지금 69세 … 내년부터'. 물을 것은 해 쪽, 근거 글은 두 문장 다
                pa = attrs[cmp['plus']]
                add(cmp['plus'], rid, f"{_say(pa, pe or 0)} 기준 {_say(a, ev_)}",
                    f"{_say(pa, m_plus)} — 그때 {_say(a, m_base + (m_plus or 0))} 무렵",
                    _join_quotes([mv[1] if mv else None, pm[1]], ctx))
            elif mv:
                qtxt = _say(a, m_base) + (f" ({_say(attrs[cmp['plus']], m_plus)} — 그때 {_say(a, m_base + (m_plus or 0))} 무렵)"
                                          if pm and m_plus else '')
                add(aid, rid, e_text(aid), qtxt, _join_quotes([mv[1], pm[1] if pm and m_plus else None], ctx))
    return diffs, consulted


def _covers(a, rid, covered, mv, ctx, add):
    """묻는 일을 계산기가 다 계산했나 — 일부만 계산했으면 빠진 쪽, 하나도 안 했으면 계산한 쪽이 질문 글에 없을 때만."""
    if not mv:
        return
    asked, quote = mv
    missing = [v for v in asked if v not in covered]
    if not missing:
        return
    if len(missing) == len(asked) and any(evidence(a, v, ctx) for v in covered):
        return                                    # 계산한 일도 질문에 있다 — 둘 다 읽힐 수 있는 질문은 막지 않는다
    eng = ' · '.join(_say(a, v) for v in covered) or '숫자 계산'
    what = ' · '.join(_say(a, v) for v in missing)
    add(a['id'], rid, f'{eng}만 계산', what + (' (함께 물음)' if len(missing) < len(asked) else ''), quote)


def _same(a, rid, e, mv, ctx, add, e_text):
    """같게 읽었나 — 목록 칸은 계산기가 질문 글에 근거 없는 값을 더 적었을 때만(고객이 말하지 않은 계좌), 하나짜리 칸은 값이 다를 때."""
    ev_, _, _ = e
    if not mv or ev_ is None:
        return
    m, quote = mv
    if isinstance(m, list) or isinstance(ev_, list):
        ms = m if isinstance(m, list) else [m]
        es = ev_ if isinstance(ev_, list) else [ev_]
        extra = [x for x in es if x not in ms and not evidence(a, x, ctx)]
        if extra:
            add(a['id'], rid, _say(a, extra), _say(a, ms), quote)
        return
    if _as(a, m) != _as(a, ev_):
        add(a['id'], rid, e_text(a['id']), _say(a, m), quote)


# ---------------------------------------------------------------------------
# 겉 — check · ask_message · trace_line
# ---------------------------------------------------------------------------
def check(q, calc, onto=None, chat=None, timeout=20):
    """calc 에 블록이 없으면 None. 아니면 {'flag','diffs','ask','consulted','sec'} (+ 'note'·'error' 설명).
    모델이 실패하거나 출력이 깨지면 flag False — 지금 동작(계산기 숫자)을 그대로 둔다."""
    res, _ = check_full(q, calc, onto, chat, timeout)
    return res


def check_full(q, calc, onto=None, chat=None, timeout=20):
    """check 와 같고 모델이 읽은 칸(reading)도 돌려준다 — 연습 세트 분석·캐시용."""
    if not calc or not calc.get('blocks'):
        return None, None
    reading = None
    t0 = time.time()
    res = {'flag': False, 'diffs': [], 'ask': '', 'consulted': {'rules': [], 'attrs': []}, 'sec': 0.0}
    try:
        _, rids, conds, ids, _ = consult(calc, onto)
    except Exception as e:                       # 온톨로지가 깨져도 답은 나가야 한다
        res.update(error=f'온톨로지 읽기 실패: {type(e).__name__}', sec=round(time.time() - t0, 2))
        return res, None
    res['consulted'] = {'rules': rids, 'attrs': ids}
    if not rids:
        res.update(note='숫자 블록 없음', sec=round(time.time() - t0, 2))
        return res, None
    if not ids:
        res.update(note='대조할 조건 속성 없음', sec=round(time.time() - t0, 2))
        return res, None
    try:
        reading = read_question(q, ids, onto, chat=chat, timeout=timeout)
    except Exception as e:                       # 시간 초과·네트워크 — 막지 않는다
        reading = None
        res['error'] = f'모델 실패: {type(e).__name__}'
    if reading is None:
        res.setdefault('error', '모델 출력을 읽지 못함')
        res['sec'] = round(time.time() - t0, 2)
        return res, None
    try:
        diffs, consulted = compare(q, calc, reading, onto)
    except Exception as e:
        diffs, consulted = [], res['consulted']
        res['error'] = f'대조 실패: {type(e).__name__}'
    res.update(flag=bool(diffs), diffs=diffs, consulted=consulted, ask=ask_message(diffs, onto) if diffs else '',
               sec=round(time.time() - t0, 2))
    return res, reading


def _jong(word):
    """마지막 글자의 받침 번호(0 = 받침 없음). 끝이 한글이 아니면 None — 숫자·영문·괄호로 끝나면 조사를 둘 다 적는다."""
    w = re.sub(r'[\s?!.,~)]+$', '', word or '')
    if not w or not ('가' <= w[-1] <= '힣'):
        return None
    return (ord(w[-1]) - 0xAC00) % 28


def _ro(word):
    """'으로'/'로' — 받침이 있으면(ㄹ 받침 빼고) '으로'."""
    j = _jong(word)
    return '(으)로' if j is None else '으로' if j and j != 8 else '로'


def _rago(word):
    """'이라고'/'라고' — 받침이 있으면 '이라고'."""
    j = _jong(word)
    return '(이)라고' if j is None else '이라고' if j else '라고'


def ask_message(diffs, onto=None):
    """고객에게 보일 되묻기 — 해요체 '제가 이렇게 읽었는데 맞나요?' 꼴. 물을 것은 온톨로지 속성의 ask 말."""
    if not diffs:
        return ''
    try:
        attrs = {a['id']: a for a in load(onto).get('attributes', [])}
    except Exception:
        attrs = {}
    # 바로 아래 줄이 '저는 …로 계산했는데'라 '계산하기 전에'와 어긋난다(검증 지적) — 숫자를 내기 전에 묻는다고 쓴다
    lines = ['제가 질문을 이렇게 읽었는데 맞나요? 틀린 숫자를 드리지 않으려고 숫자를 드리기 전에 먼저 여쭤볼게요.', '']
    asks = []
    for d in diffs:
        q = d.get('quote') or ''
        said = f"질문에 '{q}'{_rago(q)} 하셔서 " if q and not q.startswith('계산기') else ''
        lines.append(f"- {d['label']}: 저는 '{d['engine']}'{_ro(d['engine'])} 계산했는데, {said}'{d['question']}'{_ro(d['question'])} 보여요.")
        a = attrs.get(d['attr']) or {}
        # read.ask 먼저 — 속성 ask 를 새로 달면 ontology.path 의 되묻기 글이 바뀌어 read 안에 둔 칸이 있다
        ask = (a.get('read') or {}).get('ask') or a.get('ask') or d['label']
        if ask not in asks:
            asks.append(ask)
    lines += ['', '아래를 알려 주시면 그 조건으로 다시 계산해 드릴게요.'] + [f'- {x}' for x in asks]
    return '\n'.join(lines)


def trace_line(result, onto=None):
    """생각 과정 한 줄 — '온톨로지 판독 기준표: 규칙 R-07·R-08 조건 [나이·…] 대조 → 통과/다름(…)'."""
    if not result:
        return '온톨로지 판독 기준표: 계산 블록 없음 → 대조 안 함'
    c = result.get('consulted') or {}
    rules = '·'.join(c.get('rules') or []) or '없음'
    try:
        lab = {a['id']: a['label'] for a in load(onto).get('attributes', [])}
    except Exception:
        lab = {}
    attrs = '·'.join(lab.get(x, x) for x in c.get('attrs') or [])
    head = f'온톨로지 판독 기준표: 규칙 {rules} 조건 [{attrs}] 대조'
    if result.get('note'):
        return f"온톨로지 판독 기준표: {result['note']} → 대조 안 함"
    if result.get('error'):
        return f"{head} → 판독 못 함({result['error']}) — 계산기 숫자 그대로"
    if result.get('flag'):
        why = ' · '.join(f"{d['label']} {d['rule']}: 계산 '{d['engine']}' ↔ 질문 '{d['question']}'" for d in result['diffs'])
        return f'{head} → 다름({why}) — 숫자 대신 되묻기'
    return f"{head} → 통과 ({result.get('sec', 0):.1f}초)"
