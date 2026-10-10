# -*- coding: utf-8 -*-
"""
문서 꼬리표(C) — 근거 문서마다 누구를 위한 자료인지, 언제 기준인지, 옛 기준·상충 값이 있는지.

왜 — 문서 58건에는 독자가 섞여 있고(직원용 업무매뉴얼, 회사 인사담당자용 doc55, 다른 기관인 과학기술인공제회
doc27·28), 옛 기준이 남은 문서가 있다(doc22 '연 1,200만 원 초과 종합과세', doc27 '예금자보호 5천만 원').
답변이 이런 조각을 근거로 쓸 때 "누구용·언제 기준" 꼬리표와 "옛 기준: 현재는 …" 주석을 붙이려고 만든다.

데이터 — data/doc_meta.json
  docs[파일] = {title, audience, audience_06, stage, plan, doc_type,
               as_of, as_of_kind, as_of_evidence, rule_date, rule_date_evidence, notes}
  stale = [{id, kind, pattern, files, any_file?, old, new, rule_ids, note, evidence: [{file, text}]}]
  58건을 모두 열어 날짜 표현(연도·'기준'·'시행'·'개정'·'작성'·'심사필')을 찾고 앞뒤를 읽었다.
  독자는 현서_2차 06_문서분류지도(audience_06 에 원래 값)와 doc_policy.py 를 대조했고, 다르면 notes 에 적었다.

기준일(as_of) 원칙 — **문서가 스스로 적은 날짜만** 쓴다. 추측하지 않는다.
  머리 날짜(doc10·12~18 '2026.02.21.'), 표지 날짜(doc32 '2026.03'), 작성 기준일(doc29), 심사필 유효기간 시작일
  (doc30·31·37), 본문 '기준' 표기(doc27 "'23년12월기준") 가 그것이다. 못 찾으면 '미상'.
  본문에 규정의 시행·개정·적용 시점만 있는 문서(doc1 '24.1.1 이후 연금수령하는 분부터 적용', doc42 '2025.1.1~ 개정세법')는
  as_of 를 '미상'으로 두고 rule_date 에 따로 적는다. 그 날짜는 규정이 바뀐 날이지 문서를 쓴 날이 아니다 —
  sources.py 가 as_of 를 고객 화면에 '(기준일 …)'으로 붙이므로, 섞으면 doc45 가 '기준일 2020'처럼 낡은 문서로 보인다.
  화면 예시 날짜(doc7 주문일, doc24 청약일), 예시 연도(doc52), 가입 시기 구분일(doc39 '2013.3.1')도 기준일이 아니다.

대조하며 찾은 것(06 과 다른 점)
  - 06 §4 3-2 는 '부득이한 사유 인출도 1,500만 원 계산에서 제외'가 문서에 없다고 했지만 doc2·doc5·doc21 에 있다
    ("(공적연금 제외, 퇴직소득재원 및 부득이한사유 제외) 1500만원 초과 시").
  - doc24 '2023.07.03 집중투자한도(DB 10%, DC/IRP 30%) 폐지'는 리츠 유상청약 안내 속 문장이다. doc56 표가 동일법인
    한도에서 '집합투자증권(리츠포함)'을 빼므로 상충이 아니라 범위(리츠)가 빠진 표기로 둔다(06 에 없던 항목).
  - 06 에 없던 표기 오류: doc41 '최대 148만 5천만원(=900만원 x 16.5%)', doc26 '퇴직소득세는 26만 4천만원' — '만 원' 단위 오기.
    doc27 도 doc55 처럼 '연금소득세(3~5%)'(지방소득세 제외)로 적었다.
  - doc_policy.py 머리말은 내부직원용 업무매뉴얼을 18건이라 했고 06 §1 은 사무담당자 전용을 17건(doc55·28·29 포함)으로 센다.
    doc_policy 는 문서 단위로 빼지 않고(doc55 섹션, doc29 README, doc27 신고 문항만 뺌) 06 §7 은 고객 질문에서 제외를 권고했다.

이 모듈은 clova·answer 를 부르지 않는다 — test_doc_meta.py 가 API 없이 검사한다.
"""
import copy
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_PATH = os.path.join(ROOT, 'data', 'doc_meta.json')

AUDIENCES = ('고객', '직원', '고객사 담당자', '외부기관')
STAGES = ('제도 이해·가입', '납입·세액공제', '운용·상품', '이전', '중도인출·해지', '연금 수령', '세금',
          '권리 보호', '퇴직급여 지급')            # 06 §2 고객 생애주기 순서
STALE_KINDS = ('옛 기준', '상충', '표기 차이', '확인 필요')
# as_of 로 인정하는 날짜 종류 — 문서가 스스로 적은 것만. 규정 시행·개정일은 rule_date 로 간다.
AS_OF_KINDS = ('문서 머리 날짜', '표지 날짜', '작성 기준일', '심사필 시작일', '본문 기준 표기')
_AUD_LABEL = {'고객': '고객용', '직원': '직원용', '고객사 담당자': '고객사 담당자용', '외부기관': '외부기관 자료'}
UNKNOWN = '미상'

_CACHE = None


def load():
    """JSON 을 한 번 읽어 둔다. stale 정규식은 여기서 컴파일한다."""
    global _CACHE
    if _CACHE is None:
        with open(META_PATH, encoding='utf-8') as fh:
            raw = json.load(fh)
        stale = []
        for s in raw.get('stale', []):
            s = dict(s)
            s['_re'] = re.compile(s['pattern'])
            stale.append(s)
        _CACHE = {'docs': raw.get('docs', {}), 'stale': stale}
    return _CACHE


_DOC_NAME = re.compile(r'(?i)doc0*(\d+)(?:\.(?:txt|pdf|docx|xlsx|pptx))?')


def _key(file):
    """12 · 'doc12' · 'doc12.txt' · 'DOC12.TXT' · 'docs_renamed/doc12.pdf' · 'extracted/docs/doc12.txt' → 'doc12.txt'.

    원본 이름(docs_renamed/docN.pdf·.docx…)도 같은 문서다 — 추출 파일 머리에 '# 원본: …'으로 남아 있다.
    그 밖의 값(투자설명서 KR….txt, 'doc', 10.5 …)은 그대로 돌려줘 꼬리표가 없는 것으로 처리된다."""
    if file is None:
        return ''
    s = str(file).strip().replace('\\', '/').rsplit('/', 1)[-1]
    if s.isascii() and s.isdigit():
        return f'doc{int(s)}.txt'
    m = _DOC_NAME.fullmatch(s)
    if m:
        return f'doc{int(m.group(1))}.txt'
    return s


def docs():
    """꼬리표가 있는 파일 목록(doc1, doc2, … 번호 순)."""
    return sorted(load()['docs'], key=lambda f: int(re.sub(r'\D', '', f) or 0))


def meta(file):
    """문서 꼬리표 사본. 모르는 파일(투자설명서 등)은 None — 지어내지 않는다.
    깊은 사본이다 — 부르는 쪽이 plan 목록을 고쳐도 캐시가 바뀌지 않게(ontology 가 사본에 label 을 덧붙인다)."""
    m = load()['docs'].get(_key(file))
    return copy.deepcopy(m) if m else None


def as_of(file):
    """'YYYY-MM-DD' | 'YYYY-MM' | 'YYYY' | '미상'. 문서가 스스로 적은 날짜만(모듈 설명 참고)."""
    m = meta(file)
    return (m or {}).get('as_of') or UNKNOWN


def audience(file):
    """'고객' | '직원' | '고객사 담당자' | '외부기관'. 모르는 파일은 ''."""
    m = meta(file)
    return (m or {}).get('audience', '')


def label(file):
    """'고객용 · 기준일 2026-02-21' / '직원용 · 기준일 미상(본문 규정 2024-01-01)' / '직원용 · 기준일 미상'."""
    m = meta(file)
    if not m:
        return ''
    a = _AUD_LABEL.get(m.get('audience', ''), m.get('audience', ''))
    if m.get('as_of') and m['as_of'] != UNKNOWN:
        return f"{a} · 기준일 {m['as_of']}"
    if m.get('rule_date'):
        return f"{a} · 기준일 {UNKNOWN}(본문 규정 {m['rule_date']})"
    return f"{a} · 기준일 {UNKNOWN}"


def tag(file):
    """근거 카드용 작은 묶음 — {'as_of', 'audience', 'label'}."""
    return {'as_of': as_of(file), 'audience': audience(file), 'label': label(file)}


# ---------------------------------------------------------------------------
# 상충·옛 기준
# ---------------------------------------------------------------------------
def stale_list():
    """상충·옛 기준 목록(정규식 객체는 뺀 깊은 사본)."""
    return [copy.deepcopy({k: v for k, v in s.items() if k != '_re'}) for s in load()['stale']]


def stale_for(file):
    """이 파일을 원문 근거로 확인한 항목 id 목록(files 기준). any_file 항목이 다른 파일 조각에 걸리는 경우는
    조각 글자에 달렸으므로 여기 넣지 않는다 — 그건 stale_notes 가 판단한다."""
    k = _key(file)
    return [s['id'] for s in load()['stale'] if k and k in s.get('files', [])]


def _note(s):
    """고객 답변에 붙는 짧은 주석. new 는 고객에게 보이는 문장, note 는 내부 설명이라 new 를 먼저 쓴다."""
    rid = f" — {', '.join(s['rule_ids'])}" if s.get('rule_ids') else ''
    kind = s['kind']
    if kind == '옛 기준':
        return f"(옛 기준: 현재는 {s['new']}{rid})"
    if kind == '상충':
        return f"(상충 주의: {s['new']}{rid})"
    if kind == '표기 차이':
        return f"(표기 주의: {s['new']}{rid})"
    return f"(확인 필요: {s.get('new') or s['note']}{rid})"


def stale_notes(file, text):
    """그 조각(text)에 옛 기준·상충 값이 있으면 주석 목록. 없으면 [].

    항목은 원문에서 확인한 파일(files)에만 건다 — 같은 숫자가 다른 뜻으로 쓰이는 문서가 있다
    (doc4·doc23 의 '1,200만 원'은 ISA 전환 공제 한도라 옛 기준이 아니다). 패턴이 충분히 좁은
    항목(any_file)만 파일과 관계없이 건다 — file=None 으로 답변 본문을 검사할 때도 쓴다.
    """
    k = _key(file)
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    elif not isinstance(text, str):
        text = '' if text is None else str(text)
    if not text:
        return []
    out = []
    for s in load()['stale']:
        if not (k and k in s.get('files', [])) and not s.get('any_file'):
            continue
        if s['_re'].search(text):
            out.append(_note(s))
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# 날짜 읽기 — 근거 문장에 기준일이 실제로 들어 있는지 테스트에서 확인하는 데 쓴다
# ---------------------------------------------------------------------------
_D_YMD = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)')   # 2024. 6. 28 · 2026-03-13
_D_KYMD = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일')           # 2024년 10월 31일
_D_SYMD = re.compile(r"(?<![\d.])['’]?(\d{2})\.(\d{1,2})\.(\d{1,2})(?!\d)")                     # 24.1.1 · ’17.7.26
_D_YM = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*[.\-/]\s*(\d{1,2})(?!\d)')                          # 2026.03
_D_SYM = re.compile(r"['’](\d{2})\s*년\s*(\d{1,2})\s*월")                                         # '23년12월
_D_Y = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*년')                                                 # 2020년


def parse_dates(text):
    """문장 속 날짜 → {'2024-06-28', '2026-03', '2020', …}. 월·일 범위를 벗어나면 버린다."""
    out = set()
    s = text or ''

    def ymd(y, m, d):
        y, m, d = int(y), int(m), int(d)
        if 1 <= m <= 12 and 1 <= d <= 31:
            out.add(f'{y:04d}-{m:02d}-{d:02d}')

    def ym(y, m):
        y, m = int(y), int(m)
        if 1 <= m <= 12:
            out.add(f'{y:04d}-{m:02d}')

    for x in _D_YMD.finditer(s):
        ymd(*x.groups())
    for x in _D_KYMD.finditer(s):
        ymd(*x.groups())
    for x in _D_SYMD.finditer(s):
        ymd(2000 + int(x.group(1)), x.group(2), x.group(3))
    for x in _D_YM.finditer(s):
        ym(*x.groups())
    for x in _D_SYM.finditer(s):
        ym(2000 + int(x.group(1)), x.group(2))
    for x in _D_Y.finditer(s):
        out.add(x.group(1))
    return out
