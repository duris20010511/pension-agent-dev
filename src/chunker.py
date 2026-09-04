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


def build_all(extract_root, mode='structured', **kw):
    fn = structured_chunks if mode == 'structured' else baseline_chunks
    out = []
    for sub in ('docs', 'prospectus'):
        d = os.path.join(extract_root, sub)
        for f in sorted(os.listdir(d)):
            out.extend(fn(os.path.join(d, f), **kw))
    return out
