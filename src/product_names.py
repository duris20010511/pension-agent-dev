# -*- coding: utf-8 -*-
"""
상품 이름 줄여 부르기 — '인도빌리언컨슈머', '미국인덱스EMP'처럼 정식 이름의 일부로 물어도 받은 문서의 상품을 찾는다.

10-13 외부 시험: '인도빌리언컨슈머랑 미국인덱스EMP 비교해줘'에 검색이 상품표(doc37 연금 인출 가이드)를 놓치고
"문서에서 확인되지 않아 답할 수 없다"로 끝났다. 같은 대화에서 정식 이름('TIGER 인도빌리언컨슈머',
'미래에셋미국인덱스EMP(주식-재간접)종류C-P2')을 붙여 넣자 바로 비교했다.

목록: 펀드 카드 100종(data/fund_cards.json)의 펀드 이름 + 받은 문서 상품표의 '상품명' 칸(지금은 doc37 세 표).
찾는 법: 질문의 낱말(붙은 조사는 뗀다)과 이웃 낱말 2~3개를 붙인 조각이 정식 이름(띄어쓰기·괄호·클래스 표기를 뺀 것)
안에 그대로 들어 있으면 그 상품. 한 상품이면 그 상품으로 보고(answer.retrieve 가 그 상품이 든 조각을 근거 맨 앞에),
2~4개면 시연 채널(/chat)에서 "어느 상품인가요?"로 되묻는다. 5개 넘게 걸리는 흔한 낱말('인덱스'·'채권')은 쓰지 않는다.
모델을 부르지 않는다.
"""
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, 'extracted', 'docs')
CARDS = os.path.join(ROOT, 'data', 'fund_cards.json')
MAX_AMBIG = 4
MIN_LEN = 4
# 여러 상품 이름에 흔하거나 상품이 아닌 낱말 — 이것만으로는 상품을 찾지 않는다
_GENERIC = {
    '연금저축', '퇴직연금', '개인연금', '세액공제', '연금계좌', '인덱스', '인덱스펀드', '채권형', '주식형', '혼합형', '증권',
    '투자신탁', '자투자신탁', '증권투자신탁', '증권자투자신탁', '미래에셋', '미래에셋증권', '펀드', '상품', '연금상품', '연금펀드',
    '성장형', '안정형', '인컴형', '디폴트옵션', '타깃데이트', '글로벌', '코리아', '미국', '중국', '인도', '배당', '채권', '주식',
    '연금', '퇴직', '개인', '증권사', '투자회사', '자산운용사', '자산운용', '운용', 'etf', 'tdf', 'irp', 'dc', 'db', 'isa', '계좌', '추천', '비교',
}
_PARTICLE = re.compile(r'(?:이랑|랑|하고|와|과|및|은|는|을|를|이|가|도|의|에서|에|로|으로|이나|나|이요|요)$')
_CACHE = None


def norm(s):
    """정식 이름 비교용 — 괄호 안·클래스 표기('종류C-P2', 'C-Pe')·띄어쓰기·기호를 뺀 소문자."""
    s = re.sub(r'\([^)]*\)|\[[^\]]*\]|（[^）]*）', '', s or '')
    s = re.sub(r'종류\s*[A-Za-z0-9\-]+.*$', '', s)
    return re.sub(r'[\s·\-_/.,]', '', s).lower()


_ROWS = None


def table_rows():
    """받은 문서 상품표의 행 → [{'name','file','headers','cells','section','notes'}].
    section = 표 위의 '성장형 자산 (30%)' 같은 제목, notes = 표 바로 아래 '※' 줄(출처·기준일·클래스 주의)."""
    global _ROWS
    if _ROWS is not None:
        return _ROWS
    out = []
    for f in sorted(os.listdir(DOCS_DIR)):
        if not f.endswith('.txt'):
            continue
        with io.open(os.path.join(DOCS_DIR, f), encoding='utf-8') as fh:
            lines = fh.read().split('\n')
        head, col, section, rows = None, None, '', []
        for k, ln in enumerate(lines):
            s = ln.strip()
            if re.search(r'자산\s*\(\d+%\)\s*$', s):
                section = s
            cells = [c.strip() for c in ln.split('|')] if '|' in ln else None
            if cells and '상품명' in cells:
                head, col, rows = cells, cells.index('상품명'), []
                continue
            if head is None:
                continue
            if cells and len(cells) > col:
                name = cells[col]
                # '○○은행 정기예금'·'△△증권 ELB' 같은 자리표시 예시는 상품이 아니다
                if name and not re.fullmatch(r'[-\s]*', name) and not re.search(r'[○△□◇☆]', name):
                    row = {'name': name, 'file': f, 'headers': head, 'cells': cells + [''] * (len(head) - len(cells)),
                           'section': section, 'notes': []}
                    rows.append(row)
                    out.append(row)
                continue
            # 표가 끝났다 — 바로 아래 '※' 줄을 이 표의 주의 문구로 붙인다
            notes = []
            for nxt in lines[k:k + 8]:
                t = nxt.strip()
                if t.startswith('※'):
                    notes.append(t)
                elif t and notes:
                    break
            for r in rows:
                r['notes'] = notes
            head, col, rows = None, None, []
    _ROWS = out
    return out


def _table_products():
    """받은 문서 상품표의 '상품명' 칸 → [(이름, 파일)]."""
    return [(r['name'], r['file']) for r in table_rows()]


def table_answer(found, cite=1):
    """줄여 부른(또는 정식) 이름이 모두 상품표의 상품이면, 표의 행을 그대로 옮긴 비교표 답(모델 호출 없음) — 아니면 None.
    10-13 외부 시험: 모델이 표를 옮기며 위험등급을 이웃 행 값('매우높은위험')으로 바꿔 쓰고, '안정성 중시 → ○○'처럼 골라 줬다."""
    rows = []
    for m in found:
        if len(m['names']) != 1:
            return None
        r = next((x for x in table_rows() if x['name'] == m['names'][0]), None)
        if r is None:
            return None
        if all(r is not x for x in rows):
            rows.append(r)
    if not rows:
        return None
    heads = []
    for r in rows:
        heads += [h for h in r['headers'] if h not in heads and h != '상품명']
    lines = [f'받은 문서 「연금 인출 가이드」의 상품표 값을 그대로 옮겼어요 [문서{cite}].', '',
             '| 항목 | ' + ' | '.join(r['name'] for r in rows) + ' |',
             '|---|' + '---|' * len(rows)]
    for h in heads:
        vals = []
        for r in rows:
            v = r['cells'][r['headers'].index(h)] if h in r['headers'] else ''
            vals.append(v.replace('|', '/') or '-')
        lines.append(f'| {h} | ' + ' | '.join(vals) + ' |')
    if any(r['section'] for r in rows):
        lines += ['', '- 표에서의 자리: ' + ' / '.join(f"{r['name']} — {r['section']}" for r in rows if r['section'])]
    notes = list(dict.fromkeys(n for r in rows for n in r['notes']))
    if notes:
        lines += [''] + [f'- {n.lstrip("※").strip()}' for n in notes]
    lines += ['', '이 표는 문서의 자산배분 예시예요. 어느 상품이 맞는지는 위험등급과 투자성향(적합성) 진단 결과에 따라 달라서 '
                  '정해 드리지 않아요. 고르는 단계는 아래 다음 행동의 투자성향 진단이나 상담으로 이어 갈 수 있어요.']
    return '\n'.join(lines)


def catalog():
    """[{'name','key','file','kind'}] — 같은 이름이 두 곳에 있으면 둘 다 둔다(근거를 둘 다 붙일 수 있게)."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    items = []
    try:
        with io.open(CARDS, encoding='utf-8') as fh:
            for c in json.load(fh):
                if c.get('facts'):
                    items.append({'name': c['fund_name'], 'key': norm(c['fund_name']), 'file': c['file'], 'kind': 'card'})
    except Exception:
        pass
    try:
        for name, f in _table_products():
            items.append({'name': name, 'key': norm(name), 'file': f, 'kind': 'table'})
    except Exception:
        pass
    _CACHE = [x for x in items if len(x['key']) >= MIN_LEN]
    return _CACHE


def _pieces(question):
    """질문에서 상품 이름일 수 있는 조각 [(원문 조각, 정규화)] — 낱말 하나와 이웃 2~3개를 붙인 것, 긴 것부터."""
    words = re.findall(r'[A-Za-z0-9가-힣&+]+', question or '')
    words = [_PARTICLE.sub('', w) if len(w) > 3 else w for w in words]
    out = []
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            raw = ' '.join(words[i:i + n])
            key = norm(raw)
            # '미래에셋 연금'처럼 흔한 낱말만 붙인 조각은 상품 이름으로 보지 않는다
            if n > 1 and all(norm(w) in _GENERIC or len(norm(w)) < 2 for w in words[i:i + n]):
                continue
            if len(key) >= MIN_LEN and key not in _GENERIC:
                out.append((raw, key, i, i + n))
    return out


def resolve(question):
    """질문 속 줄인 상품 이름 → [{'said', 'names': [정식 이름…], 'items': [catalog 항목…]}].
    정식 이름이 질문에 이미 다 있으면(띄어쓰기 무시) 건너뛴다. 5개 넘게 걸리는 조각은 흔한 낱말로 보고 버린다."""
    cat = catalog()
    if not cat or not question:
        return []
    qn = norm(question)
    used, out = set(), []
    for raw, key, a, b in _pieces(question):
        if any(i in used for i in range(a, b)):
            continue
        hits = [x for x in cat if key in x['key']]
        names = list(dict.fromkeys(x['name'] for x in hits))
        if not names or len(names) > MAX_AMBIG:
            continue
        if any(x['key'] in qn for x in hits):
            used.update(range(a, b))         # 정식 이름을 이미 썼다 — 펀드 카드 라우팅·검색이 맡는다
            continue
        used.update(range(a, b))
        out.append({'said': raw, 'names': names, 'items': hits})
    return out


def note(found):
    """화면 '가정' 칸과 생각 과정에 쓸 한 줄 — 한 상품으로 정해진 것만."""
    return [f"'{m['said']}' → '{m['names'][0]}'(받은 문서의 정식 이름)로 보고 답함" for m in found if len(m['names']) == 1]


def ask_answer(amb):
    """여러 상품에 걸리는 이름 — 고르게 묻는다(모델 호출 없음)."""
    out = ['말씀하신 상품 이름이 받은 문서에서 여러 상품에 걸려요. 어느 상품인지 골라 주시면 문서 기준으로 바로 답해 드릴게요.', '']
    for m in amb:
        out.append(f"**'{m['said']}'** 와 이름이 겹치는 상품")
        out += [f'- {n}' for n in m['names']]
        out.append('')
    out.append('아래 다음 행동에서 고르시거나, 정식 이름으로 다시 물어봐 주세요.')
    return '\n'.join(out).strip()


def ask_actions(question, amb, limit=4):
    """되묻기 버튼 — 질문 속 줄인 이름을 후보 정식 이름으로 바꾼 질문."""
    acts = []
    for m in amb:
        for n in m['names']:
            q2 = question.replace(m['said'], n) if m['said'] in question else f'{n} — {question}'
            acts.append({'label': n if len(n) <= 28 else n[:27] + '…', 'kind': 'chat', 'payload': q2})
    return acts[:limit]
