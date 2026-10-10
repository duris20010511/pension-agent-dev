# -*- coding: utf-8 -*-
"""
문서 꼬리표(doc_meta) 회귀 테스트. API 호출 없음.

    python -X utf8 src/test_doc_meta.py

검사하는 것
  (1) 문서 58건(extracted/docs/doc1~58.txt) 모두 꼬리표가 있고 값이 정해진 범위 안에 있다
  (2) as_of 가 '미상'이 아니면 as_of_evidence 가 원문에 글자 그대로 있고, 그 문장에서 읽은 날짜가 as_of 와 같다
      (rule_date 도 같은 방식). 추측한 기준일이 끼어들지 못하게 한다
  (3) stale 패턴이 해당 파일 원문에 실제로 매치되고, 근거 문장(evidence)이 원문에 그대로 있다
  (4) 06_문서분류지도·doc_policy.py 와 독자 분류가 다르면 notes 에 그 차이가 적혀 있다
  (5) 손으로 확인한 예시 — 기준일·라벨·옛 기준 주석·날짜 읽기
검토 때 더한 것
  (3') stale 패턴이 58건 전수에서 **목록에 적은 파일에만** 걸린다 — 빠진 문서(doc27 '연금소득세(3~5%)')와
       엉뚱한 문서(ISA 전환 한도 '1,200만 원')를 함께 잡는다. 항목마다 자기 근거 문장에 주석이 실제로 붙는지도 본다
  (2') 기준일은 근거 문장의 **첫 날짜**여야 한다(심사필 '시작~끝'에서 끝 날짜를 고르면 실패). 머리·표지 날짜는
       문서 앞머리(300자 안)에 있어야 한다 — 화면 예시 날짜를 기준일로 잘못 고르는 실수를 막는다
  (4') 생애주기 단계가 06 §2 표와 맞는다
  (6) 이상 입력(None·bool·bytes·숫자·대문자·원본 확장자)과 사본 격리, 고객 문구에 판단 표현·HTML 태그가 없는지
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import doc_meta as dm
import doc_policy
import sources
import taxcalc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'extracted', 'docs')
MAP06 = os.path.join(ROOT, '현서_2차', '현서_2차', '06_문서분류지도.md')

AS_OF_RE = re.compile(r'^(?:\d{4}-\d{2}-\d{2}|\d{4}-\d{2}|\d{4}|미상)$')
_DATE = r'\d{4}(?:-\d{2}(?:-\d{2})?)?'
LABEL_RE = re.compile(r'^(?:고객용|직원용|고객사 담당자용|외부기관 자료) · 기준일 (?:' + _DATE +
                      r'|미상(?:\(본문 규정 ' + _DATE + r'\))?)$')
# 고객에게 보이는 문구(라벨·주석)에 들어가면 안 되는 말 — 계약서 공통 규칙(판단 표현 금지, HTML 태그 금지)
BANNED = re.compile(r'추천|최적|유리|가장 좋은|<[A-Za-z/!]')
HEAD_CHARS = 300          # 머리·표지 날짜가 있어야 하는 문서 앞머리 길이(파일 머리 '# 원본…' 두 줄 포함)
# 06 의 독자 → 이 꼬리표의 독자. '둘 다'·'불명확'은 정해진 답이 없으니 notes 에 사유가 있어야 한다.
FROM_06 = {'고객': '고객', '사무담당자': '직원', '고객사 담당자': '고객사 담당자', '둘 다': None, '불명확': None}

_TEXT = {}


def text(f):
    if f not in _TEXT:
        with open(os.path.join(DOCS, f), encoding='utf-8') as fh:
            _TEXT[f] = fh.read()
    return _TEXT[f]


class T:
    def __init__(self):
        self.n = 0
        self.fail = []

    def ok(self, cond, msg):
        self.n += 1
        if not cond:
            self.fail.append(msg)


def check_all_docs(t):
    """(1) 58건 모두 있음 + 값 범위."""
    files = sorted((f for f in os.listdir(DOCS) if re.fullmatch(r'doc\d+\.txt', f)),
                   key=lambda f: int(re.sub(r'\D', '', f)))
    t.ok(len(files) == 58, f'[문서 수] extracted/docs 에 doc*.txt 가 {len(files)}건')
    t.ok(dm.docs() == files, f'[문서 목록] 꼬리표 {len(dm.docs())}건 ≠ 원문 {len(files)}건 '
                             f'빠짐={sorted(set(files) - set(dm.docs()))} 남음={sorted(set(dm.docs()) - set(files))}')
    for f in files:
        m = dm.meta(f)
        if not m:
            t.ok(False, f'[꼬리표 없음] {f}')
            continue
        t.ok(m.get('file') == f, f'[file] {f}: {m.get("file")}')
        t.ok(bool(m.get('title')), f'[제목 없음] {f}')
        t.ok(m.get('audience') in dm.AUDIENCES, f'[독자] {f}: {m.get("audience")}')
        t.ok(m.get('stage') in dm.STAGES, f'[단계] {f}: {m.get("stage")}')
        t.ok(isinstance(m.get('plan'), list) and len(m['plan']) > 0, f'[제도] {f}: {m.get("plan")}')
        t.ok(bool(AS_OF_RE.match(m.get('as_of', ''))), f'[as_of 형식] {f}: {m.get("as_of")}')
        t.ok(bool(m.get('notes')), f'[notes 없음] {f}')
        # 제목 — 손으로 고친 제목(sources.TITLE_OVERRIDE)이 있으면 그것과 같아야 근거 목록과 화면이 어긋나지 않는다
        if f in sources.TITLE_OVERRIDE:
            t.ok(m['title'] == sources.TITLE_OVERRIDE[f], f'[제목 불일치] {f}: {m["title"]} ≠ {sources.TITLE_OVERRIDE[f]}')


def first_date(s):
    """근거 문장에서 가장 앞에 나오는 날짜(같은 자리에서 시작하면 긴 쪽 — '2026.02.21' 은 '2026-02' 가 아니라 일까지).
    dm.parse_dates 와 같은 정규식·같은 범위 검사를 쓴다."""
    def ymd(y, m, d):
        y, m, d = int(y), int(m), int(d)
        return f'{y:04d}-{m:02d}-{d:02d}' if 1 <= m <= 12 and 1 <= d <= 31 else None

    def ym(y, m):
        y, m = int(y), int(m)
        return f'{y:04d}-{m:02d}' if 1 <= m <= 12 else None

    conv = [(dm._D_YMD, lambda g: ymd(*g)), (dm._D_KYMD, lambda g: ymd(*g)),
            (dm._D_SYMD, lambda g: ymd(2000 + int(g[0]), g[1], g[2])), (dm._D_YM, lambda g: ym(*g)),
            (dm._D_SYM, lambda g: ym(2000 + int(g[0]), g[1])), (dm._D_Y, lambda g: g[0])]
    best = None
    for rx, fn in conv:
        for x in rx.finditer(s or ''):
            v = fn(x.groups())
            if v:
                cand = (x.start(), -len(v), v)
                best = cand if best is None or cand < best else best
    return best[2] if best else None


def check_as_of(t):
    """(2) 기준일 근거가 원문에 글자 그대로 있고, 그 문장의 날짜가 기준일과 같다."""
    for f in dm.docs():
        m = dm.meta(f)
        a, ev = m['as_of'], m.get('as_of_evidence', '')
        if a != dm.UNKNOWN:
            t.ok(bool(ev) and ev in text(f), f'[as_of 근거 원문에 없음] {f}: {ev!r}')
            t.ok(a in dm.parse_dates(ev), f'[as_of ≠ 근거 날짜] {f}: {a} / {sorted(dm.parse_dates(ev))}')
            # (2') 근거 문장의 첫 날짜여야 한다 — 심사필 '(2026.02.09~2027.02.08)'에서 끝 날짜를 고르면 실패
            t.ok(first_date(ev) == a, f'[as_of ≠ 근거 첫 날짜] {f}: {a} / 첫 날짜 {first_date(ev)}')
            k = m.get('as_of_kind')
            t.ok(k in dm.AS_OF_KINDS, f'[as_of_kind] {f}: {k!r}')
            # 종류와 근거 문장이 맞는지 — 심사필이면 '심사필', 기준 표기면 '기준'이 문장에 있어야 한다
            need = {'심사필 시작일': '심사필', '작성 기준일': '기준일', '본문 기준 표기': '기준'}.get(k)
            t.ok(need is None or need in ev, f'[as_of_kind ↔ 근거] {f}: {k} / {ev!r}')
            # (2') 머리·표지 날짜는 문서 앞머리에 있어야 한다 — 본문 화면 예시 날짜를 머리 날짜로 적는 실수를 막는다
            if k in ('문서 머리 날짜', '표지 날짜'):
                pos = text(f).find(ev)
                t.ok(0 <= pos < HEAD_CHARS, f'[머리·표지 날짜 위치] {f}: {pos}자 (≥ {HEAD_CHARS})')
                t.ok(text(f).count(ev) == 1, f'[머리·표지 근거가 여러 번 나옴] {f}')
            t.ok(not m.get('rule_date'), f'[as_of 와 rule_date 둘 다] {f}')
        else:
            t.ok(not ev and not m.get('as_of_kind'), f'[미상인데 근거가 있음] {f}: {ev!r}')
        rd, rde = m.get('rule_date', ''), m.get('rule_date_evidence', '')
        if rd:
            t.ok(bool(AS_OF_RE.match(rd)) and rd != dm.UNKNOWN, f'[rule_date 형식] {f}: {rd}')
            t.ok(bool(rde) and rde in text(f), f'[rule_date 근거 원문에 없음] {f}: {rde!r}')
            t.ok(rd in dm.parse_dates(rde), f'[rule_date ≠ 근거 날짜] {f}: {rd} / {sorted(dm.parse_dates(rde))}')
        else:
            t.ok(not rde, f'[rule_date 없는데 근거가 있음] {f}')


def check_stale(t):
    """(3) stale 패턴·근거가 원문과 맞는다."""
    items = dm.stale_list()
    t.ok(len(items) >= 10, f'[stale 수] {len(items)}건')
    ids = [s['id'] for s in items]
    t.ok(len(ids) == len(set(ids)), f'[stale id 중복] {ids}')
    for s in items:
        sid = s['id']
        t.ok(s.get('kind') in dm.STALE_KINDS, f'[stale 종류] {sid}: {s.get("kind")}')
        try:
            rx = re.compile(s['pattern'])
        except re.error as e:
            t.ok(False, f'[stale 정규식] {sid}: {e}')
            continue
        t.ok(bool(s.get('files')), f'[stale 파일 없음] {sid}')
        for f in s['files']:
            t.ok(dm.meta(f) is not None, f'[stale 파일 꼬리표 없음] {sid}: {f}')
            t.ok(bool(rx.search(text(f))), f'[stale 패턴이 원문에 없음] {sid}: {f} / {s["pattern"]}')
            # 문서 꼬리표 notes 가 이 항목을 가리켜야 문서만 보는 사람도 안다
            t.ok('목록' in dm.meta(f)['notes'], f'[notes 에 상충·옛 기준 언급 없음] {f} ({sid})')
        t.ok(bool(s.get('evidence')), f'[stale 근거 없음] {sid}')
        for e in s.get('evidence', []):
            t.ok(e['text'] in text(e['file']), f'[stale 근거 원문에 없음] {sid}: {e["file"]} {e["text"][:40]!r}')
        # 옛(또는 문제가 된) 쪽 문장을 인용했는지 — 근거 중 하나는 대상 파일에서 나오고 패턴에 걸려야 한다
        t.ok(any(e['file'] in s['files'] and rx.search(e['text']) for e in s.get('evidence', [])),
             f'[stale 근거에 대상 문장 없음] {sid}')
        t.ok(bool(s.get('old')), f'[old 없음] {sid}')
        t.ok(bool(s.get('new')) or s['kind'] == '확인 필요', f'[new 없음] {sid}')
        t.ok(bool(s.get('note')), f'[note 없음] {sid}')
        for rid in s.get('rule_ids', []):
            t.ok(rid in taxcalc.RULE_NAME, f'[규칙 번호 없음] {sid}: {rid}')
        # (3') 58건 전수 — 패턴이 걸리는 문서 = 목록의 파일. 빠진 문서도, 같은 숫자를 다른 뜻으로 쓴 문서도 없어야 한다
        hit = sorted((f for f in dm.docs() if rx.search(text(f))), key=lambda f: int(re.sub(r'\D', '', f)))
        want = sorted(s['files'], key=lambda f: int(re.sub(r'\D', '', f)))
        t.ok(hit == want, f'[stale 전수 대조] {sid}: 패턴이 걸린 문서 {hit} ≠ 목록 {want}')
        # 자기 근거 문장(대상 파일 쪽)에 주석이 실제로 붙고, 고객 문구에 판단 표현·태그·내부 지시가 없다
        for e in s.get('evidence', []):
            if e['file'] in s['files'] and rx.search(e['text']):
                notes = dm.stale_notes(e['file'], e['text'])
                mine = dm._note(dict(s))
                t.ok(mine in notes, f'[stale 주석이 안 붙음] {sid}: {notes}')
                t.ok(not BANNED.search(mine) and '할 것' not in mine, f'[stale 주석 문구] {sid}: {mine}')


def _nums(spec):
    """'7, 10, 12~18' → {7, 10, 12, …, 18}."""
    out = set()
    for part in re.split(r'[,\s]+', spec.replace('*', '')):
        mm = re.fullmatch(r'(\d+)(?:~(\d+))?', part)
        if mm:
            a = int(mm.group(1))
            out.update(range(a, int(mm.group(2) or a) + 1))
    return out


def check_audience(t):
    """(4) 06·doc_policy 와 독자 대조. 다르면 notes 에 차이가 있어야 한다."""
    for f in dm.docs():
        m = dm.meta(f)
        want = FROM_06.get(m.get('audience_06'), 'X')
        t.ok(want != 'X', f'[audience_06 값] {f}: {m.get("audience_06")}')
        if want is None or want != m['audience']:
            t.ok('06' in m['notes'], f'[06 과 다른데 notes 에 없음] {f}: 06={m.get("audience_06")} 꼬리표={m["audience"]}')
        # 직원·고객사 담당자·외부기관 자료는 doc_policy 가 어떻게 다루는지 적어 둔다
        if m['audience'] != '고객':
            t.ok('doc_policy' in m['notes'], f'[doc_policy 언급 없음] {f}')
    # doc_policy 가 따로 다루는 문서
    special = set(doc_policy.EXCLUDE) | set(doc_policy.SCOPE) | set(doc_policy.AUDIENCE_NOTE)
    for f in special:
        t.ok(dm.meta(f) is not None and 'doc_policy' in dm.meta(f)['notes'], f'[doc_policy 대상인데 notes 에 없음] {f}')
    for f in doc_policy.SCOPE:          # 과학기술인공제회 — 다른 기관
        t.ok(dm.audience(f) == '외부기관', f'[SCOPE 문서 독자] {f}: {dm.audience(f)}')
    t.ok(dm.audience('doc55.txt') == '고객사 담당자', f'[doc55 독자] {dm.audience("doc55.txt")}')

    # 06 원문 표(§1 대상 독자)와 audience_06 을 맞춰 본다 — 06 파일이 없는 배포본에서는 건너뛴다
    if not os.path.exists(MAP06):
        return
    md = open(MAP06, encoding='utf-8').read()
    rows = {}
    for name, key in (('고객', '고객'), ('사무담당자 전용', '사무담당자'), ('둘 다', '둘 다'), ('불명확', '불명확')):
        mm = re.search(r'^\|\s*\**' + re.escape(name) + r'\**\s*\|\s*\**(\d+)\**\s*\|\s*(.+?)\|\s*$', md, re.M)
        t.ok(mm is not None, f'[06 표 행 없음] {name}')
        if mm:
            spec = re.sub(r'\([^)]*\)', '', mm.group(2))   # '27(과기공 FAQ, …)' 설명 괄호 제거
            nums = _nums(spec)
            t.ok(len(nums) == int(mm.group(1)), f'[06 표 건수] {name}: {len(nums)} ≠ {mm.group(1)}')
            rows[key] = nums
    for f in dm.docs():
        n = int(re.sub(r'\D', '', f))
        got = dm.meta(f)['audience_06']
        got = '사무담당자' if got == '고객사 담당자' else got     # 06 §1 은 doc55 를 사무담당자 전용에 센다(§'두 종류' 2번)
        want = [k for k, v in rows.items() if n in v]
        t.ok(want == [got], f'[audience_06 전사] {f}: {got} / 06 표 {want}')


def check_stage(t):
    """(4') 생애주기 단계 — 06 §2 표. 한 문서가 두 단계에 걸치면(doc22: 이전·중도인출) 그중 하나면 된다."""
    for f in dm.docs():
        lab = dm.label(f)
        t.ok(bool(LABEL_RE.match(lab)), f'[라벨 형식] {f}: {lab!r}')
        t.ok(not BANNED.search(lab), f'[라벨 문구] {f}: {lab!r}')
    if not os.path.exists(MAP06):
        return
    md = open(MAP06, encoding='utf-8').read()
    sec = re.search(r'^## 2\..*?(?=^## 3\.)', md, re.M | re.S)
    t.ok(sec is not None, '[06 §2 없음]')
    if not sec:
        return
    where = {}
    for name, spec in re.findall(r'^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$', sec.group(0), re.M):
        if name in ('단계', '---') or set(name) <= set('-'):
            continue
        name = re.sub(r'\([^)]*\)', '', name).strip()                   # '세금(수령·퇴직소득)' → '세금'
        t.ok(name in dm.STAGES, f'[06 §2 단계 이름] {name}')
        for n in _nums(re.sub(r'\([^)]*\)', '', spec)):                  # '16(압류)' → 16
            where.setdefault(f'doc{n}.txt', set()).add(name)
    t.ok(sorted(where, key=lambda f: int(re.sub(r'\D', '', f))) == dm.docs(), f'[06 §2 문서 수] {len(where)}')
    for f in dm.docs():
        st = dm.meta(f)['stage']
        t.ok(st in where.get(f, set()), f'[단계 ≠ 06 §2] {f}: {st} / 06 {sorted(where.get(f, []))}')


def check_robust(t):
    """(6) 이상 입력·사본 격리."""
    # 파일 이름 — 대문자·앞 0·원본 확장자는 같은 문서, 지어내지 않는 입력은 None
    for k in ('DOC10.TXT', 'doc010', 'docs_renamed/doc10.pdf', ' doc10.txt ', 'Doc10.Docx'):
        t.ok(dm.as_of(k) == '2026-02-21', f'[파일 이름 {k!r}] {dm.as_of(k)}')
    for k in (None, '', 0, -1, True, False, 'doc0', 'doc59.txt', 'doc', '10.0', 10.5, '²', 'KR5127420034.txt', [], {}):
        t.ok(dm.meta(k) is None and dm.as_of(k) == '미상' and dm.audience(k) == '' and dm.label(k) == ''
             and dm.stale_for(k) == [], f'[모르는 입력 {k!r}]')
    # 조각 글자 — None·빈 문자열·숫자는 주석 없음, bytes 는 utf-8 로 읽는다
    line22 = '年12,00만원 초과: 전액종합과세/2024년 세법변경 연 15,00만원'
    for v in (None, '', 0, 12345, [], b''):
        t.ok(dm.stale_notes('doc22.txt', v) == [], f'[stale 빈 입력 {v!r}]')
    t.ok(len(dm.stale_notes('doc22.txt', line22.encode('utf-8'))) == 1, '[stale bytes 입력]')
    t.ok(len(dm.stale_notes(0, line22)) == 1 and len(dm.stale_notes(True, line22)) == 1,
         '[stale 파일 이름이 이상해도 any_file 항목은 건다]')
    # 사본 격리 — 부르는 쪽이 고쳐도 다음 호출은 원래 값
    m = dm.meta('doc1')
    m['plan'].append('X')
    m['notes'] = ''
    t.ok('X' not in dm.meta('doc1')['plan'] and dm.meta('doc1')['notes'], '[meta 사본 격리]')
    sl = dm.stale_list()
    sl[0]['files'].append('doc99.txt')
    sl[0]['evidence'].clear()
    t.ok('doc99.txt' not in dm.stale_list()[0]['files'] and dm.stale_list()[0]['evidence'], '[stale_list 사본 격리]')
    # 날짜 읽기 경계값
    for s in (None, '', '2026.13.40', '2026.00.10', '1899.01.01', '2026년 13월 1일'):
        got = dm.parse_dates(s)
        t.ok(not any(re.fullmatch(r'\d{4}-(?:1[3-9]|00)(?:-\d{2})?', x) for x in got) and '1899-01-01' not in got,
             f'[날짜 경계 {s!r}] {sorted(got)}')


def check_examples(t):
    """(5) 손으로 확인한 예시."""
    # 파일 이름 여러 모양
    for k in ('doc10', 'doc10.txt', 10, 'extracted/docs/doc10.txt', r'extracted\docs\doc10.txt'):
        t.ok(dm.as_of(k) == '2026-02-21', f'[as_of {k!r}] {dm.as_of(k)}')
    # 머리 날짜: doc10 '퇴직연금 가입대상 \n2026.02.21.'
    t.ok(dm.label('doc10') == '고객용 · 기준일 2026-02-21', f'[label doc10] {dm.label("doc10")}')
    # 심사필 시작일: doc30 '(2026.02.09~2027.02.08)'
    t.ok(dm.label('doc30') == '고객용 · 기준일 2026-02-09', f'[label doc30] {dm.label("doc30")}')
    # 표지 날짜 '2026.03'
    t.ok(dm.as_of('doc32') == '2026-03', f'[as_of doc32] {dm.as_of("doc32")}')
    # 본문 기준 표기 "'23년12월기준" — 다른 기관
    t.ok(dm.label('doc27') == '외부기관 자료 · 기준일 2023-12', f'[label doc27] {dm.label("doc27")}')
    # 규정 시점만 있는 문서는 기준일 미상 — '24.1.1 이후 연금수령하는 분부터 적용'은 문서 작성일이 아니다
    t.ok(dm.as_of('doc1') == '미상', f'[as_of doc1] {dm.as_of("doc1")}')
    t.ok(dm.label('doc1') == '직원용 · 기준일 미상(본문 규정 2024-01-01)', f'[label doc1] {dm.label("doc1")}')
    t.ok(dm.label('doc55') == '고객사 담당자용 · 기준일 미상(본문 규정 2022)', f'[label doc55] {dm.label("doc55")}')
    t.ok(dm.label('doc3') == '직원용 · 기준일 미상', f'[label doc3] {dm.label("doc3")}')
    # 화면 예시 날짜(2023.08.14)는 기준일이 아니다
    t.ok(dm.as_of('doc7') == '미상' and not dm.meta('doc7')['rule_date'], f'[doc7] {dm.meta("doc7")}')
    # 모르는 파일은 지어내지 않는다
    t.ok(dm.meta('KR5127420034.txt') is None and dm.as_of('KR5127420034.txt') == '미상'
         and dm.audience('KR5127420034.txt') == '' and dm.label('KR5127420034.txt') == '', '[모르는 파일]')
    t.ok(dm.tag('doc39') == {'as_of': '미상', 'audience': '고객', 'label': '고객용 · 기준일 미상'}, f'[tag doc39] {dm.tag("doc39")}')

    # 옛 기준 — doc22 '年12,00만원 초과: 전액종합과세'
    line22 = next(ln for ln in text('doc22.txt').split('\n') if '12,00만원' in ln)
    n = dm.stale_notes('doc22.txt', line22)
    t.ok(len(n) == 1 and n[0].startswith('(옛 기준: 현재는 연 1,500만 원') and 'R-08' in n[0], f'[stale doc22] {n}')
    # 같은 '1,200만 원'이라도 ISA 전환 공제 한도는 옛 기준이 아니다(doc23·doc4)
    t.ok(dm.stale_notes('doc23.txt', '최대 1,200만원까지 세액공제 대상 납입액이 된다.') == [], '[stale doc23 오탐]')
    t.ok(dm.stale_notes('doc4.txt', '만약, IRP 계좌로 전환한 경우는 900 만원+300 만원 = 1,200 만원') == [], '[stale doc4 오탐]')
    # 답변 본문(파일 없음)에도 좁은 패턴은 건다
    n = dm.stale_notes(None, '연 1,200만 원 초과 : 전액 종합과세 됩니다')
    t.ok(len(n) == 1 and '1,500만 원' in n[0], f'[stale 답변 본문] {n}')
    t.ok(dm.stale_notes(None, '연 1,500만 원 초과 시 전액 종합과세 또는 16.5% 분리과세') == [], '[stale 현행 기준 오탐]')
    # 예금자보호 — doc27 '5천만원을 한도로' → 현재는 1억 원(doc30·31)
    n = dm.stale_notes('doc27.txt', '예금자보호법에서 금융기관별 5천만원을 한도로 하여 보장하고 있습니다.')
    t.ok(len(n) == 1 and '1억 원' in n[0] and n[0].startswith('(옛 기준:'), f'[stale doc27] {n}')
    t.ok(dm.stale_notes('doc30.txt', '1인당 1억원까지"(운용되는 금융상품 판매회사별 보호상품 합산)') == [], '[stale doc30 오탐]')
    # 상충 — doc5 '2000.01.01 이전' ↔ doc1 '2000.12.31 이전'. doc5 에만 건다
    s5 = '개인연금저축(2000.01.01 이전 개설)의 경우 중도인출 불가'
    n = dm.stale_notes('doc5.txt', s5)
    t.ok(n == ['(상충 주의: (구)개인연금저축 = 2000.12.31 이전 개설(doc1) — R-15)'], f'[stale doc5] {n}')
    t.ok(dm.stale_notes('doc1.txt', s5) == [], '[stale doc5 항목이 doc1 에 걸림]')
    # 확인 필요 — doc38 종신연금 3.3% (taxcalc R-07 원문 표)
    n = dm.stale_notes('doc38.txt', taxcalc.rule('rate_table'))
    t.ok(len(n) == 1 and n[0].startswith('(확인 필요:'), f'[stale doc38 종신] {n}')
    # 깨끗한 원문 — doc39 감면 구간은 주석이 붙지 않는다
    t.ok(dm.stale_notes('doc39.txt', taxcalc.rule('retire_years')) == [], '[stale doc39 오탐]')
    # 한 조각에 두 항목 — doc26 문단
    p26 = next(ln for ln in text('doc26.txt').split('\n') if '10년이상 수령할 때' in ln)
    p26 += '\n' + next(ln for ln in text('doc26.txt').split('\n') if '초과 수령하면 종합과세된다' in ln)
    n = dm.stale_notes('doc26.txt', p26)
    # 상충 2건 + 같은 줄의 '26만 4천만원' 단위 오기 1건
    t.ok(len(n) == 3 and sum(x.startswith('(상충 주의:') for x in n) == 2 and n[2].startswith('(표기 주의: 26만 4천 원'),
         f'[stale doc26 세 항목] {n}')
    t.ok(dm.stale_for('doc26.txt') == ['sep_tax_only_comprehensive', 'retire_30_over_10y', 'typo_26_4'],
         f'[stale_for doc26] {dm.stale_for("doc26.txt")}')

    # 단위 오기 — 손계산: 총급여 5,000만 원, 연금저축 600 + IRP 300 → 공제 대상 900만 원 × 16.5% = 148만 5천 원
    e, r = taxcalc.eligible(600, 300), taxcalc.credit_rate(5000)
    t.ok(e == 900 and r == 16.5 and taxcalc.won(e * r / 100) == '148만 5천 원', f'[손계산 148.5] {e} {r}')
    line41 = next(ln for ln in text('doc41.txt').split('\n') if '148만 5천만원' in ln)
    n = dm.stale_notes('doc41.txt', line41)
    t.ok(n == [f"(표기 주의: {taxcalc.won(e * r / 100)}(900만 원 × 16.5%) — R-01, R-02)"], f'[stale doc41 오기] {n}')
    t.ok(dm.stale_notes(None, '최대 148만 5천만원까지 돌려받을 수 있습니다') == n, '[stale 답변 본문 오기]')
    t.ok(dm.stale_notes(None, '최대 148만 5천 원까지 돌려받을 수 있습니다') == [], '[stale 바른 표기 오탐]')
    # 손계산: 26만 4천 원 × 30% = 7만 9,200원 — doc26 이 같은 문단에 적은 절세액과 맞는 쪽이 26만 4천 원
    t.ok(taxcalc.won(26.4 * 0.3) == '7만 9,200원' and '7만 9,200원' in text('doc26.txt'), '[손계산 26.4×30%]')
    t.ok(any(taxcalc.won(26.4) in x for x in dm.stale_notes('doc26.txt', '퇴직소득세는 26만 4천만원이다.')), '[stale doc26 오기]')

    # 옛 기준 1,200만 원 — 답변 본문 변형과 ISA·분배금 문장 구분
    for s in ('연 1,200만 원을 초과하면 전액 종합과세됩니다', '사적연금이 1,200만원 초과 시 16.5% 분리과세',
              '1200만원 초과 수령 시 전액 다른 소득과 합산 종합과세'):
        t.ok(len(dm.stale_notes(None, s)) == 1, f'[stale 1,200 변형] {s}')
    for s in ('IRP로 전환하면 900만 원+300만 원 = 1,200만 원까지 세액공제 대상', '연간 1,200만원의 분배금은 금융소득종합과세에서 제외',
              '올해 한도 1,200만원, 월지급', '1,200만 원 초과분은 공제 대상이 아닙니다',
              '금융소득 2,000만 원 초과 시 종합과세', '분기별 300만원, 연간 1,200만원 한도',
              '1,200만 원 이하로 납입하면 세액공제를 받고, ISA 초과 수익은 9.9% 분리과세'):
        t.ok(dm.stale_notes(None, s) == [] and dm.stale_notes('doc23.txt', s) == [], f'[stale 1,200 오탐] {s}')
    # 투자설명서(58건 밖)에도 옛 기준 '연 1,200만원 이하인 경우 분리과세'가 있다 — 줄바꿈으로 '분리과/세'가 갈려도 잡는다
    pro = os.path.join(ROOT, 'extracted', 'prospectus')
    for f, frag in (('KR5116501001.txt', '1200만원 이하인 경우 「분리과'), ('KR5153420022.txt', '1,200만원 이하인 경우')):
        p = os.path.join(pro, f)
        if os.path.exists(p):
            body = open(p, encoding='utf-8', errors='replace').read()
            i = body.find(frag)
            n = dm.stale_notes(f, body[max(0, i - 50):i + 120]) if i >= 0 else []
            t.ok(i >= 0 and len(n) == 1 and n[0].startswith('(옛 기준: 현재는 연 1,500만 원'), f'[stale 투자설명서 {f}] {n}')

    # 표기 차이 — doc27 도 '연금소득세(3~5%)'. 같은 문장이라도 목록에 없는 파일이면 걸지 않는다(any_file 아님)
    s27 = '연금으로 수급하는 경우에는 연금소득세(3~5%)로 저율 과세되어'
    n = dm.stale_notes('doc27.txt', s27)
    t.ok(len(n) == 1 and '5.5%' in n[0] and 'R-07' in n[0], f'[stale doc27 3~5%] {n}')
    t.ok(dm.stale_notes('doc41.txt', s27) == [], '[stale 3~5% 다른 파일]')
    # doc24 집중투자한도 '폐지' — 상충이 아니라 범위(리츠) 표기
    n = dm.stale_notes('doc24.txt', '(2023.07.03 집중투자한도(DB 10%, DC/IRP 30%) 폐지)')
    t.ok(len(n) == 1 and n[0].startswith('(표기 주의:') and '리츠' in n[0], f'[stale doc24] {n}')
    # doc29 Q53 조각에 붙는 주석은 Q53·Q68 둘 다 말해야 한다(Q53 내용만 되풀이하지 않게)
    q53 = next(ln for ln in text('doc29.txt').split('\n') if ln.startswith('53 |'))
    n = dm.stale_notes('doc29.txt', q53)
    t.ok(len(n) == 1 and 'Q53' in n[0] and 'Q68' in n[0], f'[stale doc29 Q53] {n}')
    # 확인 필요 주석에 내부 지시가 섞이지 않는다
    n = dm.stale_notes('doc38.txt', taxcalc.rule('rate_table'))
    t.ok(all('할 것' not in x for x in n), f'[stale 확인 필요 문구] {n}')

    # 날짜 읽기
    cases = [("현재('23년12월기준)는", {'2023-12'}),
             ('(2026.02.09~2027.02.08)', {'2026-02-09', '2027-02-08'}),
             ('<개정 2024. 6. 28.>', {'2024-06-28'}),
             ('※ 24.1.1 이후 연금수령하는 분부터 적용', {'2024-01-01'}),
             ('(시행일 : 2024년 10월 31일)', {'2024-10-31'}),
             ('작성 기준일 | 2026-03-13 00:00:00', {'2026-03-13'}),
             ('2026.03', {'2026-03'}),
             ('2020년 개정을 거쳐', {'2020'}),
             ('연금소득세 5.5% · 1,500만원 · 0.15%', set())]
    for s, must in cases:
        got = dm.parse_dates(s)
        t.ok(must <= got and (must or not got), f'[날짜 읽기] {s!r}: {sorted(got)}')
    t.ok('2026-02' in dm.parse_dates('2026.02.09') and '2026' not in dm.parse_dates('2026.02.09'),
         f'[날짜 읽기 세부] {sorted(dm.parse_dates("2026.02.09"))}')


def main():
    t = T()
    check_all_docs(t)
    check_as_of(t)
    check_stale(t)
    check_audience(t)
    check_stage(t)
    check_robust(t)
    check_examples(t)
    print(f'{t.n - len(t.fail)}/{t.n} 통과')
    for f in t.fail:
        print('  ' + f)
    return 1 if t.fail else 0


if __name__ == '__main__':
    sys.exit(main())
