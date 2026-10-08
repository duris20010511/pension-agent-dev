# -*- coding: utf-8 -*-
"""
문서 대상 독자 정책 — 고객 답변의 근거로 쓰면 안 되는 부분을 검색에서 뺀다.

본선 1차 멘토링: "데이터에 트릭으로 사무담당자 문서도 섞여 있었다. 불필요한 건 걸러내라."
제도문서 58건을 전수로 읽어 대상 독자(개인고객 / 사무담당자 / 내부직원 / 혼합)를 판정하고,
제외 후보는 반박 검증을 한 번 더 거쳤다 (2026-10-07).

결과 — **통째로 뺄 문서는 없었다.**
  doc55 「퇴직연금 사무담당자 업무 가이드」가 멘토가 말한 문서다. 회사 인사·총무 담당자가
  부담금을 입금하고 가입자 명부를 쓰는 절차가 대부분인데, 그 사이에 근로자가 알아야 할
  규정(DB 비율지급, 계열사 전출 시 DC→DC 이전, 의료비 중도인출 요건)이 섞여 있다.
  평가셋 Q-071·072·075 의 근거가 여기에 있다. 그래서 **섹션 단위로** 회사 업무 절차만 뺀다.

  내부직원용 업무매뉴얼(doc1·2·4·5·6·22·33·34·57 등 18건)도 빼지 않는다. 화면번호·처리 단계
  사이에 한도·요건·세율 같은 고객용 규정이 있다. 저장된 답변 1,842개를 뒤져보니
  화면번호·시스템명 같은 내부 표현이 답변에 샌 경우는 없었다.

  doc27·28 은 과학기술인공제회(과기공) 퇴직연금 자료다. 공제회법을 따라 일반 퇴직연금과
  규칙이 다르다(예금자보호 비대상, 근퇴법 퇴직연금과 상호 이전 불가, 디폴트옵션 미적용).
  일반 고객 질문에 섞이면 오답이 되므로 질문에 공제회가 나올 때만 쓴다.

실제로 샌 사례 — Q-100 은 검색 상위 4개가 doc55 였고 5위가 doc27 이었다.
답변이 고객에게 "운용지시는 사무담당자 웹으로 변경"하라고 안내했다. 사무담당자 웹은
회사 담당자 전용 창구다. 대상 혼동이 실제로 일어난 것이다.
"""
import os
import re

# 섹션 = (시작 줄, 끝 줄). 시작 줄부터 끝 줄 직전까지. 끝이 None 이면 파일 끝까지.
# 끝이 ITEM 이면 같은 모양의 다음 항목 줄 직전까지 (FAQ 한 문항).
ITEM = object()
_FAQ_ITEM = re.compile(r'^[^|\n]{1,12} \| \[연금\]')

EXCLUDE = {
    # 회사 담당자의 업무 절차 — 부담금 입금, 명부 작성, 사무담당자웹 사용법
    'doc55.txt': [
        ('2. 부담금 납입 프로세스', '3. 부담금명부 작성요령(DC)'),
        ('4. 부담금 납입 관련 유의사항', '6. 가입자부담금 납입방법(DC)'),
        # 명부 작성 → 업무신청 간소화 협약 → 사무처리 온라인 서비스
        ('1. 가입자 명부의 중요성 & 작성방법', '[포함 이미지 전사]'),
    ],
    # 사내공유·AI 학습용 머리말. 본문 FAQ_100 은 고객 질문에 직접 답하므로 둔다.
    # 출처 목록 시트(Sources)는 FAQ 99·100번(디폴트옵션 원금보장 오해)과 한 조각에 묶여 있어
    # 빼면 그 문항까지 잃는다. URL 목록이라 검색에 걸려도 해가 없어 그대로 둔다.
    'doc29.txt': [
        ('[시트: README]', '[시트: FAQ_100]'),
    ],
    # 기관(사용자)의 제도 도입·신고 항목
    'doc27.txt': [
        ('신계약 | [연금] 퇴직연금제도 도입 시 노동부에 퇴직연금 규약 신고의', ITEM),
        ('신계약 | [연금] 퇴직연금제도를 도입하기 위해서는 노사합의를', ITEM),
        ('제도 | [연금] 하나의 사업장에서 퇴직금제도, 확정기여형, 확정급여형을 동시에', ITEM),
    ],
}

# 질문에 이 말이 있을 때만 근거로 쓴다
_KSTEF = re.compile(r'과학기술인|과기공|공제회|출연연')
SCOPE = {
    'doc27.txt': _KSTEF,
    'doc28.txt': _KSTEF,
}

# 조각 글자 중 이 비율 이상이 제외 섹션이면 조각을 뺀다.
# 조각(최대 2,400자)이 섹션 경계에 걸치면 고객용 내용도 함께 들어 있어서, 반쯤 걸친 것은 둔다.
CUT = 0.6
# doc55 는 낮춘다. 0.6 으로는 '부담금 납입 프로세스'(47%)·'납입 유의사항·처리결과 확인'(60% 미만)
# 조각이 남았고, Q-100(회사 메일을 붙여넣은 질문)에서 검색 상위 4개가 전부 doc55 였다.
# 대상 표시('회사 퇴직연금 담당자용')를 달아도 답변이 "사무담당자웹에서 처리 결과 확인"을
# 고객에게 안내했다. 두 조각에 섞인 고객용 내용(가입자부담금 한도·추가납입)은 doc6·doc41 에도 있다.
CUT_BY_FILE = {'doc55.txt': 0.45}


def _spans(lines, rules):
    """규칙을 파일 줄 번호 구간 [s, e) 로 바꾼다. 못 찾은 규칙은 이름을 돌려준다."""
    out, missing = [], []
    for start, end in rules:
        s = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith(start) and '∙∙∙' not in ln), None)   # 목차 줄은 건너뛴다
        if s is None:
            missing.append(start)
            continue
        if end is None:
            e = len(lines)
        elif end is ITEM:
            e = next((i for i in range(s + 1, len(lines)) if _FAQ_ITEM.match(lines[i])), len(lines))
        else:
            e = next((i for i in range(s + 1, len(lines)) if lines[i].strip().startswith(end)), None)
            if e is None:
                missing.append(f'{start} → {end}')
                continue
        out.append((s, e))
    return out, missing


def apply(chunks, extract_root):
    """조각에 정책 표시를 단다 (c['policy'] = 'exclude' / 'scope:파일').

    색인·임베딩은 그대로 두고 표시만 단다. 조각을 지우면 임베딩 행 번호가 어긋난다.
    돌려주는 값은 점검용 요약 {파일: (전체 조각, 뺀 조각)} 과 못 찾은 규칙 목록.
    """
    report, missing_all = {}, []
    for f, rules in EXCLUDE.items():
        path = os.path.join(extract_root, 'docs', f)
        if not os.path.exists(path):
            missing_all.append(f)
            continue
        lines = open(path, encoding='utf-8').read().split('\n')
        spans, missing = _spans(lines, rules)
        missing_all += [f'{f}: {m}' for m in missing]
        inside = [False] * len(lines)
        for s, e in spans:
            for i in range(s, e):
                inside[i] = True
        # 줄 내용 → 그 줄이 나오는 위치들의 안/밖. 같은 줄이 안팎에 다 있으면 판단을 미룬다.
        where = {}
        for i, ln in enumerate(lines):
            k = ln.strip()
            if k:
                where.setdefault(k, set()).add(inside[i])
        mine = [c for c in chunks if c['file'] == f]
        cut = 0
        for c in mine:
            w_in = w_out = 0
            for ln in c['text'].split('\n'):
                k = ln.strip()
                v = where.get(k)
                if not k or not v or len(v) == 2:
                    continue
                if True in v:
                    w_in += len(k)
                else:
                    w_out += len(k)
            if w_in + w_out and w_in / (w_in + w_out) >= CUT_BY_FILE.get(f, CUT):
                c['policy'] = 'exclude'
                cut += 1
        report[f] = (len(mine), cut)
    for c in chunks:
        if c['file'] in SCOPE and c.get('policy') != 'exclude':
            c['policy'] = 'scope'
    return report, missing_all


# 남겨 둔 조각이라도 모델에게 누구를 위한 자료인지 알린다.
# doc55 는 앞부분 '체크 포인트'에 지연이자·IRP 의무이전 같은 고객용 규정과
# '사무담당자 웹페이지 등록' 같은 회사 절차가 한 조각에 섞여 있어 조각을 뺄 수 없다.
# 대신 근거 머리말에 대상을 적어, 회사가 할 일을 고객이 할 일처럼 안내하지 않게 한다.
AUDIENCE_NOTE = {
    'doc55.txt': '회사 퇴직연금 담당자용 업무 안내',
    'doc27.txt': '과학기술인공제회 회원용',
    'doc28.txt': '과학기술인공제회 회원용',
}


def audience_note(c):
    return AUDIENCE_NOTE.get(c.get('file', ''), '')


def allowed(c, question):
    """이 조각을 이 질문의 근거로 써도 되는가."""
    p = c.get('policy')
    if p == 'exclude':
        return False
    if p == 'scope':
        return bool(SCOPE[c['file']].search(question or ''))
    return True
