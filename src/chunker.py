# -*- coding: utf-8 -*-
"""
① 자르기 단계.

두 가지 구현을 둔다.
  - baseline_chunks : 고정 크기 + 고정 겹침. 모델이 흔히 제안하는 기본값.
  - structured_chunks : 우리 문서 구조를 아는 자르기.

structured_chunks 가 지키는 규칙 (실측 근거는 ../평가설계.md):
  R1. 표행(| 포함 줄) 중간에서 자르지 않는다.
  R2. 연속된 표는 가능하면 한 조각에 유지한다.
  R3. 파일 끝의 [이미지 페이지 전사] 섹션을 원래 페이지 위치로 되돌린다.
  R4. 절차 단계 헤더([1] ...)는 뒤따르는 본문과 붙여둔다.
  R5. 겹침은 표행 P90(188자)을 덮도록 300자. 줄 경계에 스냅한다.
  R6. 모든 조각에 출처(파일/페이지)를 붙인다. 답변의 근거 표시에 쓴다.
"""
import os
import re

PAGE_RE = re.compile(r'^\[(?:페이지|슬라이드) (\d+)\]$')
TRANS_PAGE_RE = re.compile(r'^\[(?:페이지|슬라이드) (\d+) — 전사\]$')
TRANS_SECT_RE = re.compile(r'^\[(?:이미지 페이지 전사|포함 이미지 전사)')
STEP_RE = re.compile(r'^\[\d+\]\s')
HEADER_RE = re.compile(r'^#\s')


def _is_table(line):
    return '|' in line


# ---------------------------------------------------------------- 전처리
def load_pages(path):
    """파일을 [페이지 N] 단위로 쪼개고, 끝에 몰려 있는 전사 섹션을 제자리로 병합한다 (R3)."""
    text = open(path, encoding='utf-8').read()
    lines = text.split('\n')

    body = {}        # page -> [lines]
    trans = {}       # page -> [lines]
    order = []       # 등장 순서
    cur, bucket, in_trans = None, body, False

    for ln in lines:
        if HEADER_RE.match(ln):
            continue
        if TRANS_SECT_RE.match(ln):
            in_trans = True
            cur = None
            continue
        m = TRANS_PAGE_RE.match(ln)
        if m:
            cur = int(m.group(1))
            bucket = trans
            bucket.setdefault(cur, [])
            continue
        m = PAGE_RE.match(ln)
        if m:
            cur = int(m.group(1))
            bucket = trans if in_trans else body
            bucket.setdefault(cur, [])
            if not in_trans and cur not in order:
                order.append(cur)
            continue
        if cur is None:
            # 페이지 마커가 없는 파일(DOCX 파생) 또는 전사 섹션 머리말
            cur = 0
            bucket = trans if in_trans else body
            bucket.setdefault(0, [])
            if not in_trans and 0 not in order:
                order.append(0)
        bucket[cur].append(ln)

    for p in trans:
        if p not in order:
            order.append(p)

    out = []
    for p in order:
        seg = list(body.get(p, []))
        if p in trans:
            if seg:
                seg.append('')
            seg.extend(trans[p])           # 같은 페이지의 전사 내용을 이어붙인다
        joined = '\n'.join(seg).strip()
        if joined:
            out.append((p, joined))
    return out


# ---------------------------------------------------------------- 기준선
def baseline_chunks(path, size=1000, overlap=100):
    """페이지·표 구조를 무시하고 글자 수로만 자른다."""
    text = open(path, encoding='utf-8').read()
    text = HEADER_RE.sub('', text)
    fn = os.path.basename(path)
    chunks, i, idx = [], 0, 0
    step = max(1, size - overlap)
    while i < len(text):
        piece = text[i:i + size]
        if piece.strip():
            chunks.append({'file': fn, 'page': None, 'idx': idx, 'text': piece})
            idx += 1
        i += step
    return chunks


# ---------------------------------------------------------------- 구조 인식
def _blocks(lines):
    """줄을 블록으로 묶는다. 표 덩어리는 통째로 한 블록(R1, R2), 절차 헤더는 다음 줄들과 한 블록(R4)."""
    blocks, buf, mode = [], [], None

    def flush():
        nonlocal buf, mode
        if buf:
            blocks.append(('\n'.join(buf), mode))
            buf, mode = [], None

    for ln in lines:
        if _is_table(ln):
            if mode != 'table':
                flush()
                mode = 'table'
            buf.append(ln)
        elif STEP_RE.match(ln):
            flush()
            mode = 'step'
            buf.append(ln)
        else:
            if mode == 'table':
                flush()
                mode = 'text'
            elif mode is None:
                mode = 'text'
            buf.append(ln)
    flush()
    return blocks


def _tail_overlap(text, want):
    """겹침으로 쓸 꼬리를 줄 경계에 맞춰 잘라온다 (R5). 표행은 중간에서 끊지 않는다."""
    if want <= 0 or not text:
        return ''
    lines = text.split('\n')
    picked, total = [], 0
    for ln in reversed(lines):
        if total and total + len(ln) > want:
            break
        picked.insert(0, ln)
        total += len(ln) + 1
        if total >= want:
            break
    return '\n'.join(picked)


def structured_chunks(path, size=1200, overlap=300, max_size=2200):
    """구조를 지키며 자른다. 표 한 덩어리가 size 를 넘으면 max_size 까지 허용한다."""
    fn = os.path.basename(path)
    chunks, idx = [], 0

    for page, ptext in load_pages(path):
        for btext, mode in _blocks(ptext.split('\n')):
            btext = btext.strip('\n')
            if not btext.strip():
                continue

            # 이 페이지의 마지막 조각에 이어붙일 수 있으면 붙인다
            if chunks and chunks[-1]['page'] == page and chunks[-1]['file'] == fn:
                merged = chunks[-1]['text'] + '\n' + btext
                cap = max_size if mode == 'table' else size
                if len(merged) <= cap:
                    chunks[-1]['text'] = merged
                    continue

            if len(btext) <= (max_size if mode == 'table' else size):
                prev = chunks[-1]['text'] if (chunks and chunks[-1]['page'] == page and chunks[-1]['file'] == fn) else ''
                ov = _tail_overlap(prev, overlap)
                body = (ov + '\n' + btext) if ov else btext
                chunks.append({'file': fn, 'page': page, 'idx': idx, 'text': body})
                idx += 1
                continue

            # 블록 자체가 너무 큼 → 줄 단위로 나눈다 (줄=표행이므로 R1 유지)
            lines = btext.split('\n')
            cur = []
            for ln in lines:
                cand = '\n'.join(cur + [ln])
                if cur and len(cand) > size:
                    prev = '\n'.join(cur)
                    chunks.append({'file': fn, 'page': page, 'idx': idx, 'text': prev})
                    idx += 1
                    cur = _tail_overlap(prev, overlap).split('\n') + [ln]
                else:
                    cur.append(ln)
            if cur:
                chunks.append({'file': fn, 'page': page, 'idx': idx, 'text': '\n'.join(cur)})
                idx += 1

    for c in chunks:
        loc = f"{c['file']}" + (f" p{c['page']}" if c['page'] else "")
        c['source'] = loc
    return chunks


# ---------------------------------------------------------------- 절 단위 (v2, 제도문서)
# 질문마다 조각 8개(약 13,600자)를 넣는데 답변이 인용하는 건 평균 2.8개였다(2026-10-08).
# 페이지 안에서 2,400자까지 이어 붙이다 보니 한 조각에 서로 다른 절·FAQ 문항이 섞이고,
# 그중 하나만 맞아도 조각 전체가 들어간다. CLOVA 테스트 키는 '입력 + 출력 상한'을 분당
# 6만 토큰에서 빼므로, 안 쓰는 근거가 곧 처리량 손실이다.
# 그래서 제도문서는 제목·소제목·FAQ 문항을 경계로 자르고, 조각마다 절 경로를 붙인다.
# (투자설명서는 기존 조각 그대로 — 870만 자를 다시 임베딩하면 2시간이 넘는다.)
_H1 = re.compile(r'^\s*(?:\d{1,2}\s*\.|[IVX]{1,4}\.|제\s*\d+\s*[장절조])\s*\S')
# '1)' 꼴은 하위 항목·각주인 경우가 많아 장(1)이 아니라 절(2)로 본다
_H2 = re.compile(r'^\s*(?:\d{1,2}\s*\)|[가-하]\s*[.)]|[①-⑳]|[○■▣◆●□▶◎]|Q\s*[.:]|\[[^\]\n|]{2,30}\]\s*$)')
_FAQ_ROW = re.compile(r'^[^|\n]{1,12} \| \[연금\]')        # 과기공 FAQ 한 문항
_SENT_END = re.compile(r'(?:다|요|음|함|임|니다|세요|됨|능)[.!]?\s*$')
_NOT_HEAD = re.compile(r'^\s*(?:\[표\]|\[그림|\(이미지|\[포함|\[이미지|※)')


def heading_level(line):
    """제목 줄이면 1(장)·2(절), 아니면 None."""
    s = line.strip()
    if not s or _NOT_HEAD.match(s):
        return None
    if _FAQ_ROW.match(s):
        return 1
    if len(s) > 60 or '|' in s:
        return None
    if _H1.match(s) and not _SENT_END.search(s):
        return 1
    if _H2.match(s) and (len(s) <= 45 or s.endswith('?')):
        return 2
    if s.endswith('?') and len(s) <= 80:                    # FAQ 질문 줄
        return 2
    return None


def _clean_head(s):
    s = re.sub(r'\s+', ' ', s.strip())
    s = re.sub(r'\s*#\S+(?:,\s*#\S+)*\s*$', '', s)           # FAQ 끝 해시태그
    return s[:60]


def section_chunks(path, target=700, max_size=1100, table_max=1600):
    """절 단위로 자른다. 조각마다 'section'(절 경로)을 단다. 겹침은 두지 않는다 —
    절이 경계라 이어 붙일 필요가 적고, 겹친 글자는 두 조각이 함께 뽑힐 때 그대로 낭비다."""
    fn = os.path.basename(path)
    segs = []                       # (path tuple, page, [lines])
    h1 = h2 = None
    for page, ptext in load_pages(path):
        cur = None
        for ln in ptext.split('\n'):
            lv = heading_level(ln)
            if lv == 1:
                h1, h2 = _clean_head(ln), None
            elif lv == 2:
                h2 = _clean_head(ln)
            if lv or cur is None:
                cur = (tuple(x for x in (h1, h2) if x), page, [])
                segs.append(cur)
            cur[2].append(ln)

    chunks = []

    def emit(path_, page, text):
        text = text.strip('\n')
        if text.strip():
            chunks.append({'file': fn, 'page': page, 'idx': len(chunks), 'text': text,
                           'section': ' › '.join(path_)})

    for path_, page, lines in segs:
        text = '\n'.join(lines).strip('\n')
        if not text.strip():
            continue
        prev = chunks[-1] if chunks else None
        # 짧은 절은 같은 장·같은 페이지의 앞 조각에 붙인다 (제목 한 줄짜리 조각을 막는다)
        if (prev and prev['page'] == page and len(prev['text']) + len(text) + 1 <= target
                and prev['section'].split(' › ')[0] == (path_[0] if path_ else '')):
            prev['text'] += '\n' + text
            common = os.path.commonprefix([prev['section'].split(' › '), list(path_)])
            prev['section'] = ' › '.join(common)
            continue
        if len(text) <= max_size:
            emit(path_, page, text)
            continue
        # 긴 절 — 블록(표 덩어리는 통째) 단위로 채우고, 그래도 크면 줄 단위로 (표행은 안 끊는다)
        buf = ''
        for btext, mode in _blocks(lines):
            cap = table_max if mode == 'table' else max_size
            if buf and len(buf) + len(btext) + 1 > target:
                emit(path_, page, buf)
                buf = ''
            if len(btext) <= cap:
                buf = (buf + '\n' + btext) if buf else btext
                continue
            for ln in btext.split('\n'):
                if buf and len(buf) + len(ln) + 1 > (table_max if mode == 'table' else max_size):
                    emit(path_, page, buf)
                    buf = ''
                buf = (buf + '\n' + ln) if buf else ln
        if buf:
            emit(path_, page, buf)

    chunks = _merge_small(chunks, min_size=250, max_size=max_size)
    for i, c in enumerate(chunks):
        c['idx'] = i
        c['source'] = f"{c['file']}" + (f" p{c['page']}" if c['page'] else "")
    return chunks


def _merge_small(chunks, min_size, max_size):
    """너무 짧은 조각('[납입]' 같은 제목 한 줄, 각주 한 줄)을 같은 페이지의 다음 조각에 붙인다.
    처음 자른 결과는 조각 1,042개 중 454개가 120자 미만이었다."""
    out = []
    for c in chunks:
        prev = out[-1] if out else None
        if (prev and prev['page'] == c['page'] and len(prev['text']) < min_size
                and len(prev['text']) + len(c['text']) + 1 <= max_size):
            # 제목만 있던 앞 조각이면 뒤 조각의 절 이름을 쓴다
            sec = c['section'] if len(prev['text']) < 60 or not prev['section'] else prev['section']
            prev['text'] += '\n' + c['text']
            prev['section'] = sec
            continue
        out.append(dict(c))
    # 맨 끝에 남은 짧은 조각은 앞 조각에 붙인다
    if len(out) >= 2 and len(out[-1]['text']) < min_size and out[-2]['page'] == out[-1]['page'] \
            and len(out[-2]['text']) + len(out[-1]['text']) + 1 <= max_size:
        out[-2]['text'] += '\n' + out[-1]['text']
        out.pop()
    return out


def build_all(extract_root, mode='structured', **kw):
    """mode: 'structured'(v1, 전부 페이지 묶음) · 'sections'(v2, 제도문서만 절 단위, 투자설명서는 v1 그대로)."""
    out = []
    for sub in ('docs', 'prospectus'):
        d = os.path.join(extract_root, sub)
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if mode == 'sections' and sub == 'docs':
                out.extend(section_chunks(p))
            elif mode in ('structured', 'sections'):
                out.extend(structured_chunks(p, **kw))
            else:
                out.extend(baseline_chunks(p, **kw))
    return out
