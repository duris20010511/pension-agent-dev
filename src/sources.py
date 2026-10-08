# -*- coding: utf-8 -*-
"""
근거 표기 — 검색 내부 번호("문서1")를 고객이 알아볼 수 있는 문서명으로 바꾼다.

왜 필요한가 (본선 1차 멘토링 이후 점검):
  예선 답변은 끝에 "[근거] 문서1(p. 해외이주 관련), 문서3 …" 처럼 적혔다.
  - "문서1" 은 검색 결과의 순번일 뿐 고객에게 아무 의미가 없다.
  - "p. 해외이주 관련", "S5, S96" 처럼 **모델이 지어낸 페이지 표기**가 섞여 있었다.
  - 표기 형식이 제각각이었다 — 200개 답변에서 머리말만 12가지
    ([근거], **근거**:, [근거: …], **참고 문서**: …), 문장 중간에 끼는 경우 14건.

프롬프트로 형식을 강제하면 생성 품질이 같이 흔들리므로(재측정 필요),
생성은 그대로 두고 **후처리로** 바꾼다.
  1) 모델이 쓴 근거 블록은 걷어낸다
  2) 답변이 인용한 문서 번호를 모아, **실제 검색된 조각의 제목·페이지로** 근거 목록을 새로 만든다
     → 페이지는 검색 결과에 있는 값만 쓰므로 지어낸 페이지가 원천 차단된다
  3) 본문의 [문서4] 는 각주 [1] 로, "문서1에 따르면" 은 "「제목」에 따르면" 으로 바꾼다
"""
import re

# 문서 안 첫 줄에서 뽑은 제목이 쓸 수 없는 경우만 손으로 바로잡는다.
# (검색창 캡처, 배경 사진 설명, 엑셀 시트명, 빈 제목, 같은 제목이 여러 문서에 붙은 경우 등)
# 나머지는 내용을 보고 정한 이름이다 — 파일 본문 첫 부분 기준.
TITLE_OVERRIDE = {
    'doc6.txt': '퇴직연금 부담금 입금 안내',
    'doc7.txt': '퇴직연금 장외채권 매수 모바일 신청 가이드',
    'doc11.txt': '퇴직연금제도 기본',
    # 중도인출 문서 6건이 모두 제목이 「중도인출」이라 근거 목록에서 한 각주로 뭉쳤다.
    # 근거 목록은 제목으로 합치므로 문서마다 이름이 달라야 한다.
    'doc22.txt': 'IRP 업무 안내(중도인출·해지·이체·연금인출)',
    'doc24.txt': 'M-STOCK 퇴직연금 모바일 유상청약 가이드',
    # 과학기술인공제회 자료. 일반 퇴직연금 FAQ 로 읽히면 안 된다(doc_policy.SCOPE 참조).
    'doc27.txt': '과학기술인연금 퇴직연금 자주 묻는 질문',
    'doc28.txt': '과학기술인연금 운용방법 변경 자주 묻는 질문',
    'doc29.txt': '디폴트옵션 자주 묻는 질문 100선',
    'doc30.txt': '디폴트옵션 자주 묻는 질문 TOP7',
    'doc31.txt': '디폴트옵션 안내',
    'doc32.txt': '디폴트옵션 자주 묻는 질문(가입자 안내용)',
    'doc34.txt': '실물이전 불가사유 안내',
    'doc35.txt': '실물이전제도 안내',
    'doc37.txt': '미래에셋증권 연금 인출 가이드',
    'doc46.txt': '중도인출 안내(6개월 이상 요양)',
    'doc47.txt': '중도인출 안내(개인회생·파산)',
    'doc48.txt': '중도인출 안내(무주택자 전월세보증금)',
    'doc49.txt': '중도인출 안내(무주택자 주택구입)',
    'doc50.txt': '중도인출 안내(재난 피해)',
    'doc53.txt': '퇴직연금 ETF·ETN·TDF 개요',
    'doc56.txt': '퇴직연금 장내상품(ETF·ETN·리츠) 매매 안내',
    # 첫 줄에서 뽑힌 이름이 「모자형투자신탁」(펀드 구조 명칭)으로 같은 서로 다른 펀드 7종.
    # 투자설명서의 '집합투자기구 명칭'으로 바로잡는다.
    'KR5127420034.txt': 'KB스타 중기 국공채 증권 자투자신탁(채권)',
    'KR5127420039.txt': 'KB스타 중기 국공채 증권 자투자신탁(채권)',
    'KR5127420045.txt': 'KB스타 단기 국공채 증권 자투자신탁(채권)',
    'KR5127420083.txt': 'KB 장기국채 플러스 증권 자투자신탁(채권)',
    'KR5127450117.txt': 'KB 그로스 포커스 증권 자투자신탁(주식)',
    'KR5127450215.txt': 'KB스타 코스닥150 인덱스 증권 자투자신탁(주식-파생형)',
    'KR5127450217.txt': 'KB스타 코스닥150 인덱스 증권 자투자신탁(주식-파생형)',
    'KR5127450473.txt': 'KB 코리아 밸류업 액티브 증권 자투자신탁(주식)',
    'KR516702010M.txt': '에셋플러스 코리아 리치투게더 연금 증권 자투자신탁 1호(주식)',
    # 첫 줄에서 다른 펀드(모펀드·비교 펀드)의 이름이 뽑힌 경우. 100종을 '집합투자기구 명칭'과
    # 대조해 6건을 찾았다. 이 이름은 모델이 읽는 근거 머리말에도 들어가므로 틀리면 답이 흔들린다
    # (단기국공채 펀드가 「2배레버리지인덱스…」로 소개되고 있었다).
    'KR5116501001.txt': '브이아이 퇴직연금 증권 자투자신탁 1호(채권)',
    'KR5129420025.txt': '한화내일받는단기국공채증권 자투자신탁(채권)',
    'KR5129420031.txt': '한화코리아밸류채권증권 자투자신탁(채권)',
    'KR5157450017.txt': '마이다스 책임투자 증권 투자신탁(주식)',
    'KR5157450090.txt': '마이다스 거북이 90 증권 자투자신탁 1호(주식)',
    'KR5174420011.txt': '한국밸류 10년투자 연금 증권전환형자투자신탁 1호(채권)',
    'KR5157420003.txt': '마이다스우량채권증권자투자신탁제1호(채권)',
    'KR555202013M.txt': '베어링고배당증권투자회사(주식)',
}


_CARD_NAMES = None


def _card_name(file):
    """펀드 카드(data/fund_cards.json)의 펀드명 — '1. 집합투자기구 명칭'을 원문 대조로 확인한 이름.
    첫 줄에서 뽑은 이름은 100종 중 10종이 잘리거나 회사명이 빠져 있었다
    ('NH-Amundi USD 초단기채권 증권자투자신탁(UH)[채권]' → '증권자투자신탁(UH)')."""
    global _CARD_NAMES
    if _CARD_NAMES is None:
        import os
        import json
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'fund_cards.json')
        try:
            _CARD_NAMES = {c['file']: c['fund_name'] for c in json.load(open(p, encoding='utf-8'))}
        except Exception:
            _CARD_NAMES = {}
    return _CARD_NAMES.get(file, '')


def doc_title(c):
    """조각이 속한 문서의 사람이 읽을 이름."""
    t = TITLE_OVERRIDE.get(c.get('file', ''))
    if t:
        return t
    t = (_card_name(c.get('file', '')) or c.get('title') or '').strip()
    t = re.sub(r'^[\s:：\-·*]+', '', t)          # ": 교보악사 …", "- 퇴직급여제도 …"
    t = re.sub(r'\s+', ' ', t)
    t = re.sub(r'(\d)\s+호', r'\1호', t)          # "1 호" → "1호"
    t = re.sub(r'\s*[∙·…]{2,}\s*\d*\s*$', '', t)  # 목차 표기 "체크 포인트 ∙∙∙ 1"
    return t or c.get('file', '').rsplit('.', 1)[0]


def doc_kind(c):
    """투자설명서는 펀드명만으로는 무슨 문서인지 모호하므로 종류를 붙인다."""
    return '투자설명서' if c.get('file', '').startswith('KR') else ''


def doc_label(c):
    k = doc_kind(c)
    return f'「{doc_title(c)}」' + (f' {k}' if k else '')


# ---------------------------------------------------------------- 인용 파싱

# 문서 참조 + 뒤따르는 페이지 표기(모델이 쓴 것 — 신뢰하지 않으므로 함께 지운다)
#   문서1 · [문서4] · 문서 4, 5, 7 · 문서5~8 · 문서1(p1-p2) · 문서1 p2 · 문서3(S92)
_NUMS = r'\d+(?:\s*(?:,|，|、|~|∼|–|-|및|와|과)\s*(?:문서\s*)?\d+)*'
_PAGE = (r'(?:\s*\(\s*(?:[pP]\.?|S\d|페이지|쪽)[^)\n]{0,40}\)'
         r'|\s*[pP]\.?\s*\d+(?:\s*[-~∼–]\s*[pP]?\.?\s*\d+)?'
         r'|\s+S\d+(?:\s*[,~∼]\s*S?\d+)*)')
# 공백은 여는 괄호 뒤에서만 먹는다. 괄호 없이 앞 공백까지 먹으면
# "입니다. 문서2에 따르면" 이 "입니다.「…」에 따르면" 처럼 붙는다(팀 검토에서 발견).
REF = re.compile(r'(?:(?P<open>\[)\s*)?문서\s*(?P<nums>' + _NUMS + r')(?:\s*(?P<close>\]))?(?P<pg>' + _PAGE + r')*')

# 뒤에 조사가 붙으면 문장 속 주어/출처로 쓰인 것 → 문서명으로 풀어 쓴다
PARTICLE = re.compile(r'^(에 따르면|에 의하면|에서는|에서|에는|에|의|을|를|은|는|와|과)')

# 근거 블록 머리말 — 줄 처음이나 문장 처음에 올 때만 인정한다.
# 앞에 단어가 붙은 "관련 법적 근거" 를 머리말로 오인해 진짜 블록을 놓친 적이 있다.
HEAD = re.compile(
    r'(?m)(?:^|(?<=[.。!?]))[ \t]*'
    r'(?:\*\*)?\[?\s*(?:근거(?:\s*문서)?|참고\s*문서|참고|출처)\s*\]?(?:\*\*)?[ \t]*[:：]?(?:\*\*)?')

# 모델이 번호 대신 파일명으로 인용한 경우 ("[근거] doc3.txt p1, doc6 p1~p2")
FILEREF = re.compile(r'\b(doc\d+|KR[0-9A-Z]{8,12})(?:\.txt)?')

# 블록 줄이 '인용 줄'인지 판정 — 문장이면 블록이 아니라 본문이다.
# "**근거**: 문서2에 따르면 DC형은 IRP로만 이전 가능합니다" 같은 줄을 지우면 내용 손실이다.
_SENTENCE = re.compile(r'니다|에 따르면|에 의하면|합니다|입니다|하세요|십시오')


def _parse_nums(s):
    out = []
    for m in re.finditer(r'(\d+)(?:\s*[~∼–-]\s*(?:문서\s*)?(\d+))?', s):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b < a or b - a > 20:
            b = a
        out.extend(range(a, b + 1))
    return out


def _is_cite_line(line):
    """참조가 있고, 참조를 빼고 남은 게 짧은 꼬리표뿐인 줄."""
    if not REF.search(line):
        return False
    rest = REF.sub('', line)
    rest = re.sub(r'[\s\-*•·:：,，.()\[\]]', '', rest)
    return len(rest) <= 60 and not _SENTENCE.search(rest)


def _find_blocks(text):
    """모델이 쓴 근거 블록들의 [(시작, 끝)]. 답변 어디에 있든 찾는다.

    병합 결과가 두 답변을 이어 붙여 근거 블록이 중간에 끼는 경우가 있었다(Q-003, Q-031).
    블록은 머리말 + 이어지는 '인용 줄' 들이고, 인용 줄이 아닌 줄을 만나면 멈춘다
    — 블록 뒤 본문을 지키기 위해서다.
    """
    spans = []
    for m in HEAD.finditer(text):
        if spans and m.start() < spans[-1][1]:
            continue
        after = text[m.end():m.end() + 3]
        # 단어의 일부인 경우 제외: "참고하시기", "근거 분석:", "[근거 문서]에서 확인된"
        if re.match(r'\s?[가-힣]', after) and not re.match(r'\s?문서', after):
            continue
        if re.match(r'\s*에', after) and '문서' in text[m.start():m.end()]:
            continue
        line_end = text.find('\n', m.end())
        line_end = len(text) if line_end < 0 else line_end
        head_rest = text[m.end():line_end]
        cites = 0
        if head_rest.strip():
            if not _is_cite_line(head_rest):
                continue               # 머리말 같은 줄에 문장이 이어지면 본문이다
            cites = 1
        pos = line_end
        blank = 0
        while pos < len(text):
            nxt = text.find('\n', pos + 1)
            nxt = len(text) if nxt < 0 else nxt
            line = text[pos + 1:nxt]
            if not line.strip():
                blank += 1
                if cites == 0 and blank <= 1:
                    pos = nxt
                    continue
                break
            if _is_cite_line(line):
                cites += 1
                blank = 0
                pos = nxt
                continue
            break
        if cites == 0:
            continue
        s0 = m.start()
        while s0 > 0 and text[s0 - 1] in ' \t':
            s0 -= 1
        spans.append((s0, pos))
    return spans


def _find_block(text):
    """하위 호환 — 첫 블록만."""
    b = _find_blocks(text)
    return b[-1] if b else None


def render_citations(text, hits):
    """답변 속 '문서N' 을 실제 문서명·페이지로 바꾸고 근거 목록을 새로 붙인다.

    hits: 생성에 넘긴 [(chunk, score), ...] — [문서N] 은 hits[N-1] 이다.
    """
    if not text or not hits:
        return text
    n = len(hits)

    # 0) 파일명 인용("doc3.txt p1")을 번호 인용으로 바꿔 둔다 — 검색 결과에 있는 파일만
    file_idx = {}
    for i, (c, _) in enumerate(hits, 1):
        file_idx.setdefault(c.get('file', '').rsplit('.', 1)[0], i)
    text = FILEREF.sub(lambda m: f'문서{file_idx[m.group(1)]}' if m.group(1) in file_idx else m.group(0), text)

    # 1) 모델이 쓴 근거 블록을 떼어 내되, 그 안의 참조 번호는 기억한다
    block_nums = []
    for s, e in reversed(_find_blocks(text)):
        for m in REF.finditer(text[s:e]):
            block_nums.extend(_parse_nums(m.group('nums')))
        tail = text[e:].lstrip('\n')
        text = text[:s].rstrip() + ('\n\n' + tail if tail.strip() else '')
    text = text.rstrip()

    # 2) 본문 참조를 각주 번호로 바꾼다. 같은 문서(제목)는 같은 번호.
    order = {}                     # 제목 → 각주 번호
    pages = {}                     # 제목 → 실제 페이지들
    label_of = {}

    def note_for(i):
        c = hits[i - 1][0]
        key = doc_label(c)
        if key not in order:
            order[key] = len(order) + 1
            label_of[key] = c
        pages.setdefault(key, set())
        if c.get('page'):
            pages[key].add(int(c['page']))
        for p in c.get('card_pages') or []:          # 펀드 요약 카드 — 각 사실의 원문 쪽
            pages[key].add(int(p))
        return key

    def repl(m):
        nums = [i for i in _parse_nums(m.group('nums')) if 1 <= i <= n]
        if not nums:
            return ''              # 범위를 벗어난 번호(모델이 지어낸 '문서11' 등)는 지운다
        keys = list(dict.fromkeys(note_for(i) for i in nums))
        tail = text_ref[m.end():m.end() + 8]
        # 괄호 없이 문장 속에 쓰인 단일 참조 — 뒤에 한글이 이어지면 문서명으로 풀어 쓴다.
        #   "문서1에 따르면" → "「…」에 따르면",  "문서8 기준" → "「…」 기준"
        # 조사 목록만 보다가 "기준", "주1에 따르면" 같은 경우 "[1]기준" 이 된 적이 있다.
        prose = (not m.group('open') and not m.group('pg')
                 and (PARTICLE.match(tail) or re.match(r'\s*[가-힣]', tail)))
        if len(keys) == 1 and prose:
            return keys[0]
        return ''.join(f'[{order[k]}]' for k in keys)

    text_ref = text
    text = REF.sub(repl, text)

    # 블록에만 있던 번호도 목록에 올린다 (본문에서 안 짚었어도 근거로 쓴 문서)
    for i in block_nums:
        if 1 <= i <= n:
            note_for(i)

    # 3) 정리
    #   "[1], [2]" → "[1][2]", 같은 번호 연속 "[1], [1]" → "[1]" (다른 조각이 같은 문서인 경우)
    text = re.sub(r'\[(\d+)\]\s*[,，]\s*(?=\[\d+\])', r'[\1]', text)
    text = re.sub(r'\[(\d+)\](?:\[\1\])+', r'[\1]', text)
    #   "([1])" → "[1]", "([1] 참조)" → "[1]", 빈 괄호
    text = re.sub(r'\(\s*((?:\[\d+\])+)\s*(?:참조|참고)?\s*\)', r'\1', text)
    text = re.sub(r'\(\s*[,，]?\s*\)', '', text)
    #   겹친 공백은 **줄 가운데서만** 줄인다. 줄 앞 들여쓰기(하위 목록)와
    #   줄 끝 공백 두 칸(마크다운 줄바꿈)을 지우면 화면에서 목록이 풀린다.
    text = re.sub(r'(?<=\S)[ \t]{2,}(?=\S)', ' ', text)
    text = re.sub(r'(?<=\S) +([.,。])', r'\1', text)
    text = text.rstrip()
    in_body = bool(re.search(r'\[\d+\]', text))

    # 4) 근거 목록. 인용이 하나도 없으면 상위 검색 결과를 참고 자료로 단다.
    if order:
        head, keys = '**근거**', sorted(order, key=order.get)
    else:
        head = '**참고 자료**'
        for i in range(1, n + 1):
            note_for(i)
            if len(order) >= 3:
                break
        keys = sorted(order, key=order.get)
    lines = []
    for k in keys:
        pg = sorted(pages.get(k) or [])
        pg_s = ''
        if pg:
            pg_s = ' p.' + ', '.join(map(str, pg[:4])) + (' 외' if len(pg) > 4 else '')
        # 본문에 각주 표시가 하나도 없으면 목록 번호는 가리킬 데가 없으니 뺀다
        numbered = head == '**근거**' and in_body
        lines.append(f'- [{order[k]}] {k}{pg_s}' if numbered else f'- {k}{pg_s}')
    return text + '\n\n' + head + '\n' + '\n'.join(lines)
