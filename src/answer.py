# -*- coding: utf-8 -*-
"""
⑤ 답 — 질의 하나를 받아 최종 응답을 만든다.
이 모듈이 그대로 제출용 API 서버의 코어가 된다.

반환 형태는 주최측 응답 스키마와 1:1 로 맞춘다.
  question_id / question / retrieved_context / think_trace / answer
"""
import os
import re
import time
import threading
import sys
import json
import pickle
import hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from concurrent.futures import ThreadPoolExecutor
import contextvars
import clova

from chunker import build_all
from enrich import add_titles, mark_boilerplate
from index_search import Index
from hybrid import Hybrid
from expand import expand
from understand_guard import classify as classify_v1, needs_clarification, guard

# 분류기 선택 — prior 프롬프트가 이 결과를 보고 유형별 축을 고른다.
#
# 분류 정확도를 올리면 성능이 오를 것으로 보고 세 가지를 재봤는데 **반대였다** (100문항):
#   prior + v1 (키워드 득표, 분류 39.0%)  체크리스트 78.5  함정 96.1   ← 채택
#   prior + v2 (의도 우선 규칙, 분류 45.0%) 체크리스트 76.6  함정 96.1
#   prior + LLM 분류 전용 (+1 호출)        체크리스트 76.5  함정 94.7
#   universal (분류 없이 공통 축만)         체크리스트 75.4  함정 97.4
#
# 분류를 39% → 45% 로 올렸는데 체크리스트가 1.9pp 떨어졌다.
# v1 의 '제도' 쏠림이 우연히 유리했던 것으로 보인다 — 제도 프라이어(근거법령·적용대상·
# 요건·예외·제재)가 범용적이라 다른 유형에 붙어도 손해가 없고 답변을 촘촘하게 만든다.
#
# "분류 정답 문항 84.5% vs 오답 문항 75.5%" 라는 관찰은 인과가 아니라 상관이었다.
# 분류가 잘 되는 문항이 원래 쉬운 문항이었을 가능성이 크다.
#
# universal 은 함정 대응이 97.4 로 가장 높지만(대상혼동 17/17, 잘못된전제 34/34)
# 체크리스트를 3.1pp 잃는다. 체크리스트가 599항목, 함정이 77문항이라 전자가 무겁다.
_CLS = os.environ.get('AGENT_CLASSIFIER', 'v1')
if _CLS == 'v2':
    from classify2 import classify
elif _CLS == 'llm':
    from classify_llm import classify
else:
    classify = classify_v1
from rerank import rerank
from prompts import build_system, BASE_SYSTEM
from sources import doc_label, render_citations
import doc_policy
import clova

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, 'extracted')
CACHE = os.path.join(ROOT, '.cache')
os.makedirs(CACHE, exist_ok=True)

SYSTEM = """당신은 미래에셋증권의 연금 상담 AI입니다. 퇴직연금(DB·DC·IRP)과 개인연금(연금저축), 연금 세제, 연금 상품에 대해 답합니다.

# 반드시 지킬 것
1. 제공된 [근거 문서]에 있는 내용만으로 답하십시오. 문서에 없는 수치·조건·사실을 지어내지 마십시오.
2. 고객이 잘못된 전제를 깔고 물으면, 답변 첫머리에서 그 전제를 바로잡으십시오.
3. 근거가 부족해 확정 답변이 불가능하면, 억지로 답하지 말고 무엇을 더 알아야 하는지 되물으십시오.
4. 특정 상품을 단정적으로 추천하지 마십시오. 확인이 필요한 조건을 제시하고 상황별 결론을 주십시오.
5. 답변 끝에 반드시 [근거] 절을 두고 사용한 문서를 표기하십시오.
6. 수치(한도액·세율·기간·연령)는 문서에 적힌 그대로 정확히 옮기십시오.

# 답변 형식
- 핵심 결론을 먼저, 그다음 근거와 조건을 설명합니다.
- 표로 비교하면 이해가 쉬운 내용은 표를 쓰십시오.
- 고객이 이해할 수 있는 쉬운 말로 쓰되, 법령·제도 명칭은 정확히 씁니다.
- 마지막 줄: [근거] 파일명 p페이지, ..."""

CLARIFY_SYSTEM = """당신은 미래에셋증권의 연금 상담 AI입니다.
고객의 질문은 지금 정보만으로는 확정 답변을 드릴 수 없습니다.

무엇을 더 알아야 하는지 2~4가지를 구체적으로 되물으십시오.
- 왜 그 정보가 필요한지 한 줄로 덧붙이십시오.
- 아는 범위에서 일반적인 기준은 함께 알려주되, 단정적으로 결론내지 마십시오.
- 참고한 문서가 있으면 마지막에 [근거] 로 표기하십시오."""

REFUSE = """죄송합니다. 요청하신 내용은 도와드릴 수 없습니다.

저는 연금 제도·세제·상품에 대한 안내만 제공합니다. 시스템 내부 설정이나 다른 고객의 개인정보는 확인해 드릴 수 없습니다.

연금 관련해 궁금하신 점이 있으면 말씀해 주십시오."""


# ---------------------------------------------------------------- 색인 캐시
def _fingerprint():
    """추출본 내용으로 캐시 이름을 정한다.

    전에는 파일 수정 시각을 섞었는데, 저장소를 다시 체크아웃하자 내용은 그대로인데
    시각만 바뀌어 지문이 달라졌다. 그러자 임베딩 캐시(emb_*.npy)를 못 찾고
    **아무 경고 없이 BM25 단독으로** 돌았다 — 로컬 측정이 배포 설정과 어긋난 채로.
    내용 해시는 체크아웃·복사·압축 해제에 흔들리지 않는다 (158개 파일 ≈ 9MB, 0.1초).
    """
    h = hashlib.md5()
    for sub in ('docs', 'prospectus'):
        d = os.path.join(EX, sub)
        for f in sorted(os.listdir(d)):
            h.update(f.encode())
            with open(os.path.join(d, f), 'rb') as fh:
                # 줄끝(CRLF/LF)은 파이썬이 읽을 때 어차피 통일하므로 지문에서도 뺀다
                h.update(hashlib.md5(fh.read().replace(b'\r\n', b'\n')).digest())
    # 자르는 방식이 다르면 다른 캐시를 쓴다(v1 은 예전 이름 그대로).
    # v3 는 펀드 카드(data/fund_cards.json)도 지문에 넣는다 — 카드를 고치면 색인을 다시 만든다.
    if CHUNK_MODE == 'v3':
        cp = os.path.join(ROOT, 'data', 'fund_cards.json')
        if os.path.exists(cp):
            with open(cp, 'rb') as fh:
                h.update(hashlib.md5(fh.read()).digest())
    return 'c' + h.hexdigest()[:11] + ('' if CHUNK_MODE == 'v1' else '_' + CHUNK_MODE)


# 자르기 방식. v1 = 페이지 안에서 2,400자까지 이어 붙임(예선 제출).
# v2 = 제도문서를 제목·FAQ 문항 경계로 자르고 절 경로를 단다(score_chunks.py 로 비교).
# v3 = v2 + 펀드 요약 카드.
#
# 실측(2026-10-08, 100문항 짝비교 = 50 + 50, 채점 3회 다수결) — v1 근거 8개 vs v3 근거 10개:
#   체크리스트 80.9 → 84.2%(+3.5pp, 95% CI ±5.1 — 유의하지 않음. 앞 50은 +7.2, 뒤 50은 −0.2)
#   함정 98.7 → 100 · 할루시 0/0 · 응답 시간 중앙 51.9 → 33.8초 · 질문당 토큰 차감 약 −15%
# 품질이 아니라 속도·토큰 때문에 v3 를 기본으로 둔다.
CHUNK_MODE = os.environ.get('AGENT_CHUNKS', 'v3')

_INDEX = None
_CHUNKS = None
_HYBRID = None
_EMB_WARNED = False


def get_hybrid():
    """BM25 + 임베딩 하이브리드. 임베딩 캐시가 없으면 None (BM25 로만 동작).

    가중치 1:3 은 실측으로 정했다 (40문항, 조각 6,803개):
      BM25 단독      R@10 75.0  전체근거@10 22.5
      RRF 1:1        R@10 80.0  전체근거@10 32.5
      RRF 1:2        R@10 85.0  전체근거@10 32.5
      RRF 1:3        R@10 87.5  전체근거@10 40.0   ← 채택
    제도문서 283조각만으로 잰 소규모 검증에서는 1:2 가 최적이었으나,
    투자설명서 6,520조각이 들어오면 BM25 노이즈가 커져 최적점이 이동한다.
    """
    global _HYBRID
    if _HYBRID is not None:
        return _HYBRID
    idx, chunks = get_index()
    p = os.path.join(CACHE, f'emb_{_fingerprint()}.npy')
    if not os.path.exists(p):
        # 임베딩 없이 돌면 검색 R@10 이 87.5 → 75.0 으로 떨어진다. 조용히 넘어가면 안 된다.
        # 이름(지문)만 다른 캐시가 있으면 그것도 알린다 — 실제로 겪은 고장이 그 경우였다.
        others = sorted(f for f in os.listdir(CACHE)
                        if f.startswith('emb_') and f.endswith('.npy') and '_docs' not in f)
        msg = (f'[경고] 임베딩 캐시 {os.path.basename(p)} 가 없어 BM25 단독으로 동작합니다'
               + (f' (지문이 다른 캐시: {", ".join(others)})' if others else ''))
        if os.environ.get('AGENT_REQUIRE_EMB') == '1':
            raise RuntimeError(msg)
        global _EMB_WARNED
        if not _EMB_WARNED:
            print(msg, file=sys.stderr, flush=True)
            _EMB_WARNED = True
        return None
    _HYBRID = Hybrid(chunks, idx.bm25, p)
    return _HYBRID


def get_index():
    global _INDEX, _CHUNKS
    if _INDEX is not None:
        return _INDEX, _CHUNKS
    fp = _fingerprint()
    path = os.path.join(CACHE, f'index_{fp}.pkl')
    if os.path.exists(path):
        with open(path, 'rb') as f:
            _CHUNKS = pickle.load(f)
    else:
        # v2: 제도문서만 절 단위(chunker.section_chunks), 투자설명서는 v1 과 같은 조각
        # v3: v2 + 펀드 요약 카드(fund_cards.py) — 펀드마다 핵심 사실을 원문 쪽수와 함께 한 장
        _CHUNKS = build_all(EX, mode='structured' if CHUNK_MODE == 'v1' else 'sections',
                            size=2400, overlap=300)
        if CHUNK_MODE == 'v3':
            from fund_cards import card_chunks
            _CHUNKS += card_chunks()
        _CHUNKS = add_titles(_CHUNKS, EX)
        _CHUNKS, _ = mark_boilerplate(_CHUNKS)
        with open(path, 'wb') as f:
            pickle.dump(_CHUNKS, f)
    # 대상 독자 정책 — 사무담당자 업무 절차 등을 고객 답변 근거에서 뺀다 (doc_policy.py).
    # 색인 캐시에는 넣지 않고 불러온 뒤에 단다. 정책을 고쳐도 색인을 다시 만들 필요가 없다.
    if os.environ.get('AGENT_DOC_POLICY', '1') != '0':
        doc_policy.apply(_CHUNKS, EX)
    _INDEX = Index(_CHUNKS)
    return _INDEX, _CHUNKS


# ---------------------------------------------------------------- 본체
_TOK = re.compile(r'[0-9]+(?:[.,][0-9]+)*%?|[가-힣]{2,}|[A-Za-z]{2,}')


def _content_tokens(t):
    """수치·용어만 남긴다. 문체 차이가 아니라 담긴 사실로 비교하기 위해서다."""
    return set(_TOK.findall(t or ''))


def pick_base(drafts):
    """초안 중 다른 초안들과 가장 많이 겹치는 것을 기준으로 고른다.

    전에는 가장 긴 초안을 기준으로 썼는데, 30문항을 재보니
    **최장 초안이 최고 점수인 비율이 32% 로 무작위(33%)와 같았다.**
    길이는 품질 신호가 아니다. 퇴화한 초안이 가장 길 수도 있다
    (Q-051 은 가짜 URL 을 반복해 3,767자를 냈다).

    자기일관성의 원래 발상은 '여러 번 뽑아 서로 동의하는 답을 고른다' 이다.
    그래서 초안을 수치·용어 집합으로 바꿔 서로 얼마나 겹치는지를 재고,
    평균 겹침이 가장 큰 초안을 기준으로 삼는다. 혼자 튀는 초안(한 초안만
    엉뚱한 수치를 들고 온 경우)은 자연히 기준에서 밀린다.

    겹침이 같으면 긴 쪽을 쓴다 — 항목을 더 담았을 가능성이 높다.
    """
    return pick_base_scored(drafts)[0]


def pick_base_scored(drafts):
    """pick_base 와 같고, 고른 초안의 평균 겹침(0~1)도 함께 돌려준다 — 병합 생략 판단용."""
    if len(drafts) <= 1:
        return (drafts[0] if drafts else ''), 1.0
    toks = [_content_tokens(d) for d in drafts]
    best, best_score = drafts[0], -1.0
    for i, d in enumerate(drafts):
        sims = []
        for j, o in enumerate(toks):
            if i == j:
                continue
            u = toks[i] | o
            sims.append(len(toks[i] & o) / len(u) if u else 0.0)
        sc = (sum(sims) / len(sims)) if sims else 0.0
        if (sc, len(d)) > (best_score, len(best)):
            best, best_score = d, sc
    return best, best_score


# 호출당 실제 지연. 부하 조절이 이 값으로 300초 예산을 나눈다.
#
# 상수로 박아두면 환경이 바뀔 때마다 틀린다 — 로컬에서 13~16초로 재고 20으로 잡았는데
# 배포 서버(2vCPU)에서는 호출당 약 37초였다. 예산을 절반으로 낙관하면
# 동시 요청에서 그대로 타임아웃이 된다.
# 그래서 실제 호출 시간을 재서 최근 값들의 중앙값을 쓴다.
_LAT = []
_LAT_LOCK = threading.Lock()


def note_call_latency(sec):
    """생성 호출 하나가 걸린 시간을 기록한다."""
    if sec <= 0 or sec > 600:
        return
    with _LAT_LOCK:
        _LAT.append(sec)
        if len(_LAT) > 40:
            del _LAT[:-40]


def observed_call_seconds(default=20.0):
    """최근 호출들의 중앙값. 표본이 적으면 보수적으로 큰 값을 쓴다.

    중앙값을 쓰는 이유 — 평균은 이따금 나오는 아주 느린 호출 하나에 끌려간다.
    표본이 5개 미만이면 아직 이 서버를 모르는 상태이므로 default 와 비교해 큰 쪽을 쓴다.
    예산을 낙관하는 쪽으로 틀리면 타임아웃이고, 비관하는 쪽으로 틀리면 초안이 줄 뿐이다.
    """
    with _LAT_LOCK:
        v = sorted(_LAT)
    if not v:
        return default
    med = v[len(v) // 2]
    return max(med, default) if len(v) < 5 else med


LAST_DRAFTS = {}          # 진단용. AGENT_KEEP_DRAFTS 일 때만 채운다


MERGE_SYSTEM = """당신은 연금 상담 답변을 보강하는 편집자입니다.

[기준 답변]이 이미 있습니다. 다른 초안들이 담고 있는데 기준 답변에 빠진 내용을
**덧붙여** 더 완전한 답변을 만드는 것이 당신의 일입니다.

# 절대 규칙
1. **기준 답변의 문장을 지우거나 줄이지 마십시오.** 그대로 두고 덧붙이십시오.
   요약하지 마십시오. 최종 답변은 기준 답변보다 짧아질 수 없습니다.
2. 지울 수 있는 것은 딱 하나입니다 — 기준 답변의 서술이 [근거 문서]와 어긋나고
   다른 초안이 문서에 맞게 고쳐 놓았을 때, 그 대목만 고칩니다.
3. 덧붙이는 내용은 **[근거 문서]에서 확인되는 것만**입니다.
   초안에만 있고 문서에 없으면 넣지 마십시오. 새로 지어내지 마십시오.
4. 기준 답변이 고객의 전제를 바로잡았거나, 조건이 부족해 되물었거나,
   상품 지목을 피했다면 — **그 태도를 그대로 유지하십시오.**
   다른 초안이 단정적으로 추천했더라도 따라가지 마십시오.
   "이걸 사세요" 같은 단정 추천은 어떤 경우에도 쓰지 않습니다.

# 덧붙이는 방법
- 기준 답변에 없는 항목(수치·요건·예외·절차·위험등급·보수·기간 등)을 찾아 넣습니다.
- 이미 있는 내용을 다른 말로 반복하지 마십시오. **없는 것만** 넣습니다.
- 항목이 늘어나면 소제목이나 글머리표로 나눠 읽기 쉽게 합니다.

# 출력
- 편집 과정을 언급하지 마십시오. "최종 답변", "초안1에 따르면", "보강했습니다" 같은 말을 쓰지 마십시오.
- 처음부터 하나로 쓴 상담 답변처럼 보여야 합니다. 답변 본문만 출력합니다.
- **링크·URL 을 쓰지 마십시오.** 마크다운 링크 형식([이름](주소))도 쓰지 마십시오.
  근거는 `[문서3] p5` 처럼 문서 번호와 페이지로만 적습니다.
- 같은 문장이나 표기를 반복하지 마십시오. 한 번만 씁니다.
- 마지막 줄에 [근거] 절을 두고 사용한 문서를 표기합니다."""


# HTML 태그. 고객 화면은 마크다운으로 그리므로 태그가 그대로 글자로 보인다.
#
# 실측(저장된 답변 1,842개) — <br> 103개, <u> 2개. **<br> 은 전부 표 셀 안**이었다.
# 마크다운 표는 셀 안에서 줄을 못 바꾸니 모델이 HTML 로 우회한 것이다:
#   | **수수료** | - 판매수수료 + 판매보수 발생<br>- 수수료미징구 클래스 적용 |
# 본선 멘토링에서 "마크다운으로 냈는데 HTML 태그가 붙으면 안 된다"는 지적을 받았다.
#
# 이름을 정해 둔 태그만 다룬다. "<IRP 가입자>" 처럼 꺾쇠를 본문에 쓰는 경우가 있어
# 영문자로 시작하는 꺾쇠를 다 지우면 내용이 사라진다.
_HTML_TAGS = 'br|u|b|strong|i|em|span|div|p|sup|sub|font|mark|small|s|strike|del|ins|center|li|ul|ol'
_BR = re.compile(r'<br\s*/?>', re.I)
_TAG = re.compile(r'</?(?:' + _HTML_TAGS + r')\b[^>]{0,200}>', re.I)
_CELL_BULLET = re.compile(r'^(?:[-•*·▪◦]|\d+[.)])\s+')


def _join_cell(cell):
    """표 셀 안의 <br> 줄들을 한 줄로 잇는다.
    "- 가<br>- 나" → "가 · 나",  "**선택 가능**<br>- 종합과세<br>- 분리과세" → "**선택 가능**: 종합과세 · 분리과세"
    """
    parts = [p.strip() for p in _BR.split(cell)]
    parts = [p for p in parts if p]
    if not parts:
        return ' '
    bullet = [bool(_CELL_BULLET.match(p)) for p in parts]
    if len(parts) > 1:
        parts = [_CELL_BULLET.sub('', p) for p in parts]
    out = parts[0]
    for i in range(1, len(parts)):
        out = out.rstrip()
        if out.endswith((':', '：', ',', '，', '.', '。', '!', '?')):
            sep = ' '                     # 이미 끊어 읽히는 자리
        elif i == 1 and not bullet[0] and bullet[1]:
            sep = ': '                    # 머리말 뒤에 목록이 오는 모양
        else:
            sep = ' · '
        out += sep + parts[i]
    return ' ' + out + ' '


def html_to_markdown(t):
    """답변에서 HTML 태그를 걷어내고 마크다운으로 바꾼다."""
    if not t or '<' not in t:
        return t
    lines = []
    for line in t.split(chr(10)):
        if _BR.search(line):
            if line.lstrip().startswith('|'):
                # 표 행 — 셀 경계(|)를 지키면서 셀 안만 잇는다
                # 같은 행의 다른 셀에 홀로 남은 "- " 도 떼어 모양을 맞춘다
                cells = line.split('|')
                line = '|'.join(_join_cell(c) if _BR.search(c)
                                else re.sub(r'^(\s*)(?:[-•▪◦])\s+', r'\1', c) for c in cells)
            else:
                line = _BR.sub(chr(10), line)
        lines.append(line)
    t = chr(10).join(lines)
    t = re.sub(r'</?(?:b|strong)\s*>', '**', t, flags=re.I)
    t = re.sub(r'<li\b[^>]*>\s*', '- ', t, flags=re.I)
    t = re.sub(r'</?(?:p|div|ul|ol)\b[^>]*>', chr(10), t, flags=re.I)
    return _TAG.sub('', t)


_MID_META = re.compile(r'(?m)^[ \t]*\**[ \t]*(?:추가|최종|보강된?)[ \t]*답변[ \t]*\**[ \t]*[:：][ \t]*\**[ \t]*')
_MDLINK = re.compile(r'\[([^\]]{1,80})\]\(\s*[^)]{0,2000}?\)')
_OPENLINK = re.compile(r'\[([^\]]{1,80})\]\((?![^)]{0,300}\))[^)]*')
_URL = re.compile(r'https?://\S+')


def sanitize_answer(t):
    """모델 출력에서 링크와 퇴화 반복을 걷어낸다.

    실측 사고 — 자기일관성 첫 런에서 Q-051 이 가짜 구글독스 URL 을 끝없이 반복하는
    3,767자짜리 응답을 냈고 채점기가 할루시네이션으로 잡았다(그 런의 유일한 1건).
    펀드 규모도 "103조 6,750억 원"으로 지어냈다.
    병합 프롬프트의 "[근거] 절 표기" 지시가 마크다운 링크 습관을 불렀고,
    링크 안에서 토큰 반복 루프에 빠졌다.

    프롬프트로 금지하는 것만으로는 부족하다 — 생성이 확률적이라 언젠가 또 샌다.
    제출용 엔드포인트가 이런 응답을 내면 그 문항은 통째로 날아가므로 출력단에서도 막는다.
    """
    if not t:
        return t
    t = html_to_markdown(t)            # 표 셀 안 <br> 등
    # 병합기가 줄 앞에 붙이는 편집 표지("추가 답변:", "최종 답변:"). 답변 중간에 나오면
    # 고객에게는 답이 두 번 이어 붙은 것처럼 보인다(규칙 전후 비교 72답변 중 2건).
    t = _MID_META.sub('', t)
    t = _MDLINK.sub(r'\1', t)          # [문서4](주소) -> 문서4
    t = _OPENLINK.sub(r'\1', t)        # 닫히지 않은 [문서71](... 도 걷어낸다
    t = _URL.sub('', t)
    # 같은 줄이 잇달아 반복되면 하나만 남긴다
    out, prev, rep = [], None, 0
    for line in t.split(chr(10)):
        k = line.strip()
        if k and k == prev:
            rep += 1
            continue
        else:
            rep = 0
            prev = k
        out.append(line)
    t = chr(10).join(out)
    # 한 줄 안에서 같은 조각이 되풀이되는 경우 (링크 루프가 이렇게 나온다)
    fixed = []
    for line in t.split(chr(10)):
        if len(line) > 400:
            for w in (60, 40, 24):
                seg = line[:w]
                if seg and line.count(seg) >= 4:
                    line = line[:line.find(seg, w * 2) or len(line)].rstrip() + ' …'
                    break
        fixed.append(line)
    t = chr(10).join(fixed)
    return re.sub(r'\n{3,}', chr(10) + chr(10), t).strip()


_META = re.compile(r'^\s*\*{0,2}(?:최종\s*답변|답변|보강된?\s*답변)\s*\*{0,2}\s*[:：]\s*\*{0,2}\s*')


def _strip_meta(t):
    """병합기가 붙이는 '최종 답변:' 류 접두어를 떼어낸다."""
    return _META.sub('', t.lstrip())


# 추론 강도(HCX-007 thinking.effort)와 초안 수 — 응답 시간 프리셋.
#
# 본선 1차 멘토링에서 "답변은 깔끔한데 시간이 길다"는 지적을 받았다(예선 서버 순차 32~109초).
# 재보니 시간의 대부분이 모델의 '생각'이었다. 짧은 질문 하나에 effort none 0.8초, low 15.4초.
# 예선 설정은 초안 3개(low) + 병합(low) 이라 '생각'이 두 번 이어진다.
#
# score_speed.py 실측 — 49문항, 문항마다 같은 초안 위에서 병합 방식만 바꿔 짝비교(채점 3회 다수결):
#                                   체크리스트   예선 대비             생성 시간(중앙값)
#   예선   초안3(low)+병합(low)        81.1%     —                   31.8초 (부하 시 68.6초)
#   quality 초안3(low)+병합(none)      79.5%     −1.4pp ±6.9         병합 44.4 → 10.1초
#           초안3(low), 병합 생략       77.1%     −3.7pp ±6.5         17.8초
#           초안1(low)                 75.1%     −5.8pp ±6.7         13.9초
#   fast   초안1(none)                 76.1%     −5.0pp ±8.3         7.9초 (함정 97.3%)
# 병합은 '빠진 항목 찾아 덧붙이기'라 생각을 꺼도 품질이 노이즈 범위 안에서 유지됐다.
# 초안의 생각을 끄거나 초안을 줄이면 5pp 안팎을 잃는다.
#
# 병합 생략 문턱(초안끼리 겹침 ≥ τ 면 병합 생략)은 τ=0.35 에서 16%를 생략하고도 손실이 없었지만
# 평균 1~2초밖에 못 줄여 기본값은 끈다(AGENT_SKIP_MERGE_AT).
#
# 토큰은 줄지 않는다는 점도 알아둘 것 — quality 도 질문당 약 4만 토큰(초안 1회 ≈ 8,700).
# 테스트 앱 키(HCX-007 분당 6만 토큰)로는 질문이 연달아 오면 한도 대기가 생긴다.
MODE_PRESETS = {
    'quality': {'samples': 3, 'draft': 'low', 'merge': 'none'},   # 기본
    'fast':    {'samples': 1, 'draft': 'none', 'merge': 'none'},  # 시연에서 체감 속도가 중요할 때
    'max':     {'samples': 3, 'draft': 'low', 'merge': 'low'},    # 예선 제출 설정
}
AGENT_MODE = os.environ.get('AGENT_MODE', 'quality')
_P = MODE_PRESETS.get(AGENT_MODE, MODE_PRESETS['quality'])
DRAFT_EFFORT = os.environ.get('AGENT_DRAFT_EFFORT', _P['draft'])
MERGE_EFFORT = os.environ.get('AGENT_MERGE_EFFORT', _P['merge'])
DEFAULT_SAMPLES = int(os.environ.get('AGENT_SAMPLES', _P['samples']))


def cited_numbers(drafts, n):
    """초안들이 인용한 [문서N] 번호(1..n)."""
    got = set()
    for d in drafts:
        for m in re.finditer(r'문서\s*(\d+(?:\s*[,~·및]\s*\d+)*)', d or ''):
            for x in re.findall(r'\d+', m.group(1)):
                if 1 <= int(x) <= n:
                    got.add(int(x))
    return sorted(got)


# 병합에 넘기는 근거를 초안들이 실제로 인용한 문서로 줄인다.
# 병합 입력은 중앙값 7,834토큰이었는데(12문항 실측) 대부분 근거 문서다. 초안이 인용하는 건
# 8개 중 평균 2~4개이고, 병합 규칙도 '덧붙이는 내용은 근거 문서에서 확인되는 것만'이라
# 초안이 쓴 근거만 있으면 확인할 수 있다. 번호는 원래 번호를 그대로 둔다 — 병합 결과의
# [문서N] 을 render_citations 가 원래 검색 결과 순서로 풀기 때문이다.
MERGE_CITED_ONLY = os.environ.get('AGENT_MERGE_CITED_ONLY', '1') != '0'


def merge_drafts(question, ctx, drafts, effort=None, hits=None):
    """초안 여러 개를 하나로 합친다. 다시 쓰지 않고 **덧붙인다**.

    왜 하는가 — 9개 런의 문항별 점수를 합집합으로 계산하니 천장이 크게 남아 있었다.
      런 평균 73.35%  ->  합집합 92.43%   (+19.07pp)
      같은 프롬프트 2회만 합쳐도  prior 73.59 -> 82.33%  (+8.74pp)
      3회면 85.87%. 프롬프트를 바꿔 얻은 다양성(+7.96pp)과 거의 같다
      — 즉 온도 재표집만으로 충분하다.

    진단과 맞물린다. 8개 런에서 한 번도 50%를 못 넘긴 11문항 중 8개는
    근거가 top-20 안에 오는데도 졌다. 답이 틀린 게 아니라 불완전했다.

    왜 '덧붙이기'인가 — 처음엔 "초안들을 합쳐 최종 답변을 쓰라"고 시켰다가 두 번 깨졌다.
      Q-053 : 초안이 ~700자인데 병합 결과가 580자. 합치는 게 아니라 요약했다.
      Q-096 : 단정추천유도 함정인데 병합 결과가 "사라" 한 마디였다.
              규칙에 비단정을 넣어뒀는데도 다시 쓰면서 가드레일이 새로 협상됐다.
    다시 쓰기는 압축과 재협상을 부른다. 덧붙이기는 기준 답변의 함정 대응을 보존하고
    길이가 줄지 않는다 — 합집합이라는 원래 의도에 맞는 연산이다.

    기준 답변은 가장 긴 초안으로 고른다. 항목을 가장 많이 담은 초안일 가능성이 높고,
    되묻기·전제교정이 들어간 답이 대체로 더 길다.

    주의 — 위 수치는 천장이지 실적이 아니다. 병합기가 완벽히 골라낸다는 가정이 깔려 있다.
    할루시네이션 0.0% 와 함정 대응률이 유지되는지 반드시 확인할 것.
    """
    base = pick_base(drafts)
    if hits and MERGE_CITED_ONLY:
        nums = cited_numbers(drafts, len(hits))
        if nums:                      # 인용이 하나도 없으면 전체 근거를 그대로 준다
            ctx = format_context(hits, only=nums)
    others = [d for d in drafts if d is not base]
    body = (chr(10) * 2).join(f'[초안{i}]' + chr(10) + d for i, d in enumerate(others, 1))
    user = (f'[근거 문서]{chr(10)}{ctx}{chr(10)*2}'
            f'[고객 질문]{chr(10)}{question}{chr(10)*2}'
            f'[기준 답변]{chr(10)}{base}{chr(10)*2}{body}')
    m = [{'role': 'system', 'content': MERGE_SYSTEM},
         {'role': 'user', 'content': user}]
    try:
        if clova.is_thinking_model():
            eff = effort or MERGE_EFFORT
            # 생각을 켜면 추론 토큰까지 들어가야 하므로 예전 상한을 쓴다
            o = clova.chat_thinking(m, max_tokens=MERGE_MAXTOK if eff == 'none' else 4096, effort=eff)
            if o.get('stop') == 'length':
                return base          # 잘린 병합 결과보다 온전한 기준 초안이 낫다
            out = o['content']
        else:
            out = clova.chat(m, max_tokens=2048)
    except Exception:
        return base
    out = _strip_meta(out or '')
    # 덧붙이기가 목적인데 짧아졌으면 편집이 아니라 요약을 한 것이다 — 기준을 그대로 쓴다
    return out if len(out) >= len(base) * 0.95 else base


def add_siblings(hits, eq, trace):
    """상위 문서의 형제 조각을 덧붙인다. 기본은 꺼져 있다.

    왜 필요한가 — 8개 런에서 한 번도 50%를 못 넘긴 11문항을 뜯어보니
    3개만 검색 실패이고 8개는 근거가 top-20 안에(때로는 1위로) 오는데도 졌다.
    답이 틀린 게 아니라 불완전했다. 채점 항목이 한 문서의 여러 페이지에
    흩어져 있는데 검색은 가장 잘 맞는 조각 하나만 올린다.

      Q-053 : 투자설명서 p5 를 1위로 가져와 단위형·목표기준가격은 맞췄지만
              위험등급 4→5, 보수 1.043%→0.240%, 존속기간 3년이 있는
              같은 문서 p4·p8·p10 을 놓쳤다.

    실측(97문항, 결정론적 지표):
      현행 k=20            근거회수율 64.5%  전체근거 41.2%  26,148자
      k=20 +상위3문서×5조각  근거회수율 70.1%  전체근거 46.4%  35,024자
      k=30 (단순 증가)      근거회수율 69.3%  전체근거 46.4%  38,521자
    같은 전체근거를 더 적은 컨텍스트로 얻는다.

    다만 '검색 지표 개선 ≠ 답변 개선'은 이 프로젝트에서 세 번 겪었다.
    그래서 기본값은 0(꺼짐)이고, 답변 품질로 확인한 뒤에 켠다.
    """
    nf = int(os.environ.get('AGENT_SIB_FILES', '0'))
    ns = int(os.environ.get('AGENT_SIB_N', '0'))
    if not (nf and ns):
        return hits
    H = get_hybrid()
    if H is None or H.emb is None:
        return hits
    picked = {id(c) for c, _ in hits}
    files = []
    for c, _ in hits:
        if c['file'] not in files:
            files.append(c['file'])
        if len(files) >= nf:
            break
    # 임베딩은 한 번만 만들어 문서마다 재사용한다 (분당 60회 한도를 아낀다)
    v = np.asarray(clova.embed(eq), dtype=np.float32)
    nz = np.linalg.norm(v)
    qvec = v / (nz if nz else 1)
    ex_idx = {n for n, c in enumerate(H.chunks) if id(c) in picked}
    extra = []
    for f in files:
        for n, c in H.rank_within(eq, f, exclude=ex_idx, topk=ns, qvec=qvec):
            ex_idx.add(n)
            if doc_policy.allowed(c, eq):      # 형제 확장으로 제외 조각이 되돌아오지 않게
                extra.append((c, 0.0))
    if extra:
        trace.append(f'형제 확장: 상위 {len(files)}개 문서에서 {len(extra)}조각 추가')
    return hits + extra


def format_context(hits, only=None):
    # 머리말에 파일명(doc3.txt) 대신 실제 문서명을 보인다.
    # 파일명을 보여주자 모델이 근거를 "doc3.txt p1" 로 베껴 쓰는 경우가 있었다.
    # [문서N] 은 그대로 둔다 — 모델이 인용 키로 안정적으로 쓰고, 후처리가 이 번호로
    # 실제 문서명·페이지를 찾아 근거 목록을 다시 만든다(sources.render_citations).
    # only 를 주면 그 번호의 문서만 넣는다(병합용) — 번호는 원래 번호를 그대로 쓴다.
    parts = []
    for i, (c, score) in enumerate(hits, 1):
        if only is not None and i not in only:
            continue
        pg = f' {c["page"]}쪽' if c.get('page') else ''
        note = doc_policy.audience_note(c)
        note = f' ({note})' if note else ''
        sec = f' › {c["section"]}' if c.get('section') else ''
        parts.append(f'[문서{i}] {doc_label(c)}{sec}{pg}{note}\n{c["text"]}')
    return '\n\n'.join(parts)


# 생성기에 넘길 조각 수.
#
# 검색 지표 (40문항, RRF 1:3):
#   k= 6  R@k 85.0  전체근거 32.5  컨텍스트  8,045자
#   k=12  R@k 90.0  전체근거 40.0  컨텍스트 15,910자
#   k=20  R@k 90.0  전체근거 52.5  컨텍스트 26,090자
#
# ⑤ 답 단계 (LLM 채점):
#            체크리스트   함정대응
#   k= 6       80.7      100.0
#   k=12       82.5       96.2
#   k=20       85.4       96.6
#
# 처음엔 이 함정대응 차이(100.0 vs 96.6)를 보고 k=6 으로 되돌렸다. 그런데 그 차이는
# Q-040 한 문항이었고, k=6 으로 3회 반복하니 1승 2패였다 — 재현되지 않았다.
# 함정 문항 전체를 k별 3회씩 결정적 규칙으로 재판정한 결과:
#   k= 6  통과 12/24 · 3회 일관 6/8
#   k=20  통과 12/24 · 3회 일관 6/8   ← 문항별 결과까지 동일
# 함정 대응은 k 와 무관했고, 차이는 LLM 채점기의 변동이었다.
#
# 따라서 체크리스트가 명확히 높은 k=20 을 쓴다 (239항목 중 +11항목).
#
# 남은 실패는 k 로 풀 수 없는 것들이다:
#   Q-040 (구)개인연금저축 — 검색 상위 6개 중 5개가 투자설명서의 신 연금저축 조항.
#         제도문서 283조각이 투자설명서 6,520조각에 묻힌다.
#   Q-008 지연이자 — needs_clarification() 규칙이 "얼마나 받을 수 있고" 패턴을 못 잡아
#         역질문 없이 연 20% 로 단정한다.
# 근거 문서 수. 실측 — topk 20 은 컨텍스트 30,783자에 22.9초, topk 8 은
# 13,608자에 13.6초(59%)다. 앞서 k=6 과 k=20 이 답변 품질에서 문항별로
# 동일했던 측정도 있어(12/24 일치) 줄일 여지가 있다고 보고 8 로 낮춰 A/B 한다.
# v3 는 제도문서 조각이 작아(중앙 401자) 10개를 넣어도 v1 8개보다 근거 글자가 25% 적다.
DEFAULT_TOPK = int(os.environ.get('AGENT_TOPK', '10' if CHUNK_MODE != 'v1' else '8'))


# 생성 프롬프트 모드. prompts.py 참조.
#
# 100문항 실측:
#   base   체크리스트 72.8  함정 93.5  할루시 0.0
#   note   체크리스트 74.8  함정 94.7  할루시 0.0
#   prior  체크리스트 78.5  함정 96.1  할루시 0.0   ← 채택
#   both   체크리스트 76.0  함정 92.0  할루시 0.0
#
# prior 는 세 지표가 동시에 올랐고 트레이드오프가 없다. 특히 역질문이 9/11 → 11/11.
# both(note+prior)는 오히려 떨어졌다 — 시스템 프롬프트가 1,140자로 길어지며
# 지시가 서로 희석된 것으로 보인다. 둘 다 넣는 게 항상 나은 게 아니다.
PROMPT_MODE = os.environ.get('AGENT_PROMPT', 'prior')


_CARDS_BY_FILE = None


def retrieve(eq, question, want, use_dense=True, qvec=None):
    """검색 → 문서 정책 필터 → (v3) 펀드 카드 라우팅. (hits, 라벨, 뺀 조각, 앞세운 카드)

    prepare() 와 측정 스크립트(score_chunks.py)가 같은 경로를 쓰도록 떼어냈다.
    """
    global _CARDS_BY_FILE
    # 정책으로 빠질 조각이 있을 수 있어 넉넉히 받아 거른 뒤 자른다
    H = get_hybrid() if use_dense else None
    if H is not None:
        wb = float(os.environ.get('AGENT_W_BM', '1.0'))
        wd = float(os.environ.get('AGENT_W_DENSE', '3.0'))
        hits = H.search(eq, topk=want * 3, w_bm=wb, w_dense=wd, qvec=qvec)
        label = f'하이브리드(BM25+임베딩 {wb:g}:{wd:g})'
    else:
        idx, _ = get_index()
        hits = idx.search(eq, topk=want * 3)
        label = 'BM25'
    dropped = [c for c, _ in hits[:want] if not doc_policy.allowed(c, question)]
    hits = [h for h in hits if doc_policy.allowed(h[0], question)]
    routed = []
    if CHUNK_MODE == 'v3':
        # 질문에 이름이 나온 펀드의 요약 카드를 맨 앞에 둔다(fund_cards.mentioned_funds 참고).
        # 검색에만 맡기면 펀드명이 분명한 질문에서도 카드가 상위에 못 드는 경우가 많았다(33/53).
        from fund_cards import mentioned_funds
        if _CARDS_BY_FILE is None:
            _, chunks = get_index()
            _CARDS_BY_FILE = {c['file']: c for c in chunks if c.get('kind') == 'card'}
        routed = [_CARDS_BY_FILE[f] for f in mentioned_funds(question) if f in _CARDS_BY_FILE]
        if routed:
            ids = {id(c) for c in routed}
            hits = [(c, 1.0) for c in routed] + [h for h in hits if id(h[0]) not in ids]
    return hits[:want], label, dropped, routed


def prepare(question, topk=None, oracle_context=None, use_rerank=False, pool=20,
            use_dense=True, prompt_mode=None):
    """가드 → 질의이해 → 검색 → 프롬프트 조립. 생성 직전까지.

    answer() 와 측정 스크립트(score_speed.py)가 같은 경로를 쓰도록 떼어냈다.
    측정이 운영 코드와 다른 길을 타면 잰 값이 운영에 안 맞는다.
    """
    if topk is None:
        topk = DEFAULT_TOPK
    if prompt_mode is None:
        prompt_mode = PROMPT_MODE
    trace = []

    # ⑥ 가드 먼저
    blocked, reasons, policy = guard(question)
    trace.append(f'가드: {"차단" if blocked else "통과"}'
                 + (f' ({", ".join(reasons)})' if reasons else ''))
    if blocked:
        return {'blocked': True, 'trace': trace, 'ctx': '', 'cite_hits': None, 'msgs': None}

    # ③ 질의이해
    cat = classify(question)
    clarify, what = needs_clarification(question)
    eq, added = expand(question)
    trace.append(f'유형: {cat}')
    if added:
        trace.append(f'질의확장: {", ".join(added[:8])}')
    if clarify:
        trace.append(f'정보부족 판정 → 역질문 ({what})')

    # ④ 찾기
    if oracle_context is not None:
        cite_hits = None
        ctx = oracle_context
        trace.append('근거: oracle 주입 (검색 생략)')
    else:
        want = pool if use_rerank else topk
        hits, label, dropped, routed = retrieve(eq, question, want, use_dense=use_dense)
        trace.append(f'{label} 상위: ' + ', '.join(c['source'] for c, _ in hits[:5]))
        if routed:
            trace.append('펀드 이름 인식: ' + ', '.join(doc_label(c) for c in routed) + ' → 펀드 요약 카드 우선')
        if dropped:
            trace.append(f'대상 필터: 고객 답변에 쓰지 않는 조각 {len(dropped)}개 제외 ('
                         + ', '.join(sorted({c['file'] for c in dropped})) + ')')
        if use_rerank:
            picked, draft, err = rerank(question, hits, topk=topk)
            if err:
                trace.append(f'리랭커 실패({err}) → BM25 결과 사용')
                hits = hits[:topk]
            else:
                trace.append(f'리랭커: {len(hits)}개 → {len(picked)}개 인용 '
                             + ', '.join(c['source'] for c, _ in picked))
                hits = picked
        hits = add_siblings(hits, eq, trace)
        ctx = format_context(hits)
        cite_hits = hits

    # ⑤ 답 — 프롬프트
    sys_prompt = CLARIFY_SYSTEM if clarify else build_system(cat, prompt_mode)
    if prompt_mode != 'base':
        trace.append(f'프롬프트 모드: {prompt_mode}')
    if policy:
        sys_prompt += f'\n\n# 이 질의 주의사항\n{policy}'
    user = f'[근거 문서]\n{ctx}\n\n[고객 질문]\n{question}'
    msgs = [{'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': user}]
    return {'blocked': False, 'trace': trace, 'ctx': ctx, 'cite_hits': cite_hits, 'msgs': msgs,
            'injection': '문서 속 지시문 의심' in reasons}


FALLBACK = ('죄송합니다. 지금은 이 질문에 대한 답변을 준비하지 못했습니다. '
            '연금 상담은 미래에셋증권 영업점 또는 고객센터로 문의해 주시기 바랍니다.')


def finalize(ans, cite_hits, trace):
    """출력단 정리 — 링크·반복·HTML 제거, 근거 표기, 빈 답 방지."""
    ans = sanitize_answer(ans)
    if cite_hits and os.environ.get('AGENT_CITE', '1') != '0':
        # 검색 내부 번호(문서1)를 실제 문서명·페이지로. 지어낸 페이지 표기도 여기서 사라진다.
        ans = render_citations(ans, cite_hits)
    if not (ans or '').strip():
        # 여기까지 비어 있으면 파이프라인 어딘가가 깨진 것이다.
        # 빈 문자열을 내보내면 그 문항은 0점이고 원인도 안 남는다.
        trace.append('경고: 답변이 비어 최종 안내문으로 대체')
        ans = FALLBACK
    return ans


# 출력 상한(maxCompletionTokens). 품질이 아니라 **토큰 한도**를 위한 값이다.
# CLOVA 는 실제 출력이 아니라 '입력 + 설정한 상한'을 분당 토큰 한도에서 뺀다
# (실측: 출력 18토큰인데 상한 2,000 이면 2,007 차감). 상한은 길이 지시가 아니라 끊는 지점이라
# 닿지만 않으면 답은 같다. 12문항 44회 실측(추론 포함) —
#   초안(low)   중앙 709 · p90 980 · 최대 1,312   (예전 상한 2,048)
#   병합(none)  중앙 444 · p90 631 · 최대 1,030   (예전 상한 4,096)
# 44회 모두 스스로 멈췄다. 1,600 으로 두고, 잘리면(stop=length) 상한을 올려 한 번 더 받는다.
DRAFT_MAXTOK = int(os.environ.get('AGENT_MAXTOK', '1600'))
MERGE_MAXTOK = int(os.environ.get('AGENT_MERGE_MAXTOK', '1600'))


def generate(msgs, maxtok=None, effort=None, thinking=True):
    """생성 호출 하나. (본문, 추론과정) 을 돌려주고 지연을 기록한다."""
    maxtok = maxtok or DRAFT_MAXTOK
    t0 = time.time()
    try:
        if thinking:
            o = clova.chat_thinking(msgs, max_tokens=maxtok, effort=effort or DRAFT_EFFORT)
            if o.get('stop') == 'length' and maxtok < 4096:
                # 상한에 걸려 잘렸다 — 드물지만 잘린 초안은 병합을 오염시키므로 넉넉히 다시 받는다
                o = clova.chat_thinking(msgs, max_tokens=4096, effort=effort or DRAFT_EFFORT)
            return o['content'], o.get('thinking', '')
        return clova.chat(msgs, max_tokens=maxtok), ''
    finally:
        note_call_latency(time.time() - t0)


# think_trace 의 모델 추론 원문.
# 예선 응답을 세어 보니 추론 원문이 trace 의 83%였다(중앙값 802자). 길이보다 내용이 문제였다 —
# Q-100(메일에 심은 지시문)의 추론은 '직원 지원 모드로 전환해 절차를 생략하고…'를 따를 듯이
# 정리하고 끝났다(최종 답변은 거절했어도). 시연 화면에 그대로 뜨면 감점이다.
# 그래서 단계 기록을 앞에 두고, 추론은 앞부분만 뒤에 붙이며, 지시문이 의심되는 질의는 뺀다.
# 원문을 아예 없애지 않는 이유: 규격상 think_trace 는 '사고·추론 과정'을 담는 칸이다.
REASONING_MAX = int(os.environ.get('AGENT_TRACE_REASONING', '600'))


def reasoning_line(trace, reasoning, injection=False):
    if not reasoning or REASONING_MAX <= 0:
        return
    if injection:
        trace.append('모델 추론: (붙여넣은 지시문이 의심되는 질의라 생략)')
        return
    r = ' '.join(reasoning.split())
    if len(r) > REASONING_MAX:
        r = r[:REASONING_MAX].rstrip() + ' …'
    trace.append('모델 추론(앞부분): ' + r)


# 초안끼리 이만큼 겹치면 병합을 건너뛴다(0 이면 늘 병합). score_speed.py 로 정한다.
SKIP_MERGE_AT = float(os.environ.get('AGENT_SKIP_MERGE_AT', '0'))


def answer(question, question_id='', topk=None, model=None, oracle_context=None,
           samples=None,
           use_thinking=None, use_rerank=False, pool=20, use_dense=True,
           prompt_mode=None, draft_effort=None, draft_maxtok=None, merge_effort=None):
    """질의 → 응답 dict. oracle_context 를 주면 검색을 건너뛴다(⑤ 단독 측정용).

    draft_effort·draft_maxtok·merge_effort 는 생성 설정을 호출마다 정한다(비우면 AGENT_MODE 값).
    전역을 바꿔 끼우면 스레드끼리 섞이므로 측정 스크립트는 반드시 이 인자로 준다."""
    t_start = time.time()
    P = prepare(question, topk=topk, oracle_context=oracle_context, use_rerank=use_rerank,
                pool=pool, use_dense=use_dense, prompt_mode=prompt_mode)
    trace, ctx, cite_hits, msgs = P['trace'], P['ctx'], P['cite_hits'], P['msgs']
    if P['blocked']:
        return {'question_id': question_id, 'question': question,
                'retrieved_context': '', 'think_trace': ' / '.join(trace),
                'answer': REFUSE}

    prev_model = clova.CHAT_MODEL
    if model:
        clova.CHAT_MODEL = model
    thinking = use_thinking
    if thinking is None:
        thinking = clova.CHAT_MODEL.startswith('HCX-007')

    # 추론 상한. 실측 — 4096 은 27.1초, 2048 은 18.7초(69%)인데 답변 길이는
    # 720자 → 808자로 오히려 늘었다(잘리지 않는다). 무거운 호출은 API 가 직렬
    # 처리하므로 호출을 싸게 만드는 것이 곧 처리량이다.
    maxtok = draft_maxtok or DRAFT_MAXTOK

    def _gen():
        return generate(msgs, maxtok=maxtok, effort=draft_effort, thinking=thinking)

    # samples 를 넘기면 그 값을 쓴다 — 서버가 부하에 따라 낮출 수 있게.
    nsamp = int(samples if samples is not None else DEFAULT_SAMPLES)
    # 주최측 규격은 문항당 300초다. 초안 생성이 늦어지면 병합까지 갈 시간이 없으므로
    # 남은 예산을 보고 병합을 포기한다 — 초안 하나를 내는 편이 타임아웃보다 훨씬 낫다.
    # 실측(로컬): 초안 3개 병렬 40~90초 + 병합 40~50초 = 83~132초.
    # 배포 서버가 로컬보다 느릴 수 있어 여유를 크게 잡는다.
    budget = float(os.environ.get('AGENT_BUDGET_S', '240'))
    reasoning = ''
    try:
        if nsamp <= 1:
            t0 = time.time()
            ans, th = _gen()
            if th:
                reasoning = th
            trace.append(f'생성 1회 ({time.time() - t0:.1f}s)')
        else:
            # 초안은 동시에 뽑는다. 병렬이므로 대기 시간은 합이 아니라 '가장 느린 하나'다.
            #
            # 초안에 개별 타임아웃을 걸었다가 크게 데었다 — future 를 버려도 스레드는
            # 계속 돌아 시간은 그대로 들면서 결과만 잃었고, 전부 버려지자 **빈 답변**이
            # 나갔다. 빈 답변은 타임아웃보다 나쁘다(200 응답에 0점 확정).
            # 그래서 초안은 끝까지 기다리고, 예산은 **병합을 할지 말지**에만 쓴다.
            t0 = time.time()
            with ThreadPoolExecutor(max_workers=nsamp) as ex:
                outs = []
                # copy_context: 측정용 호출 꼬리표(clova.TAG)가 초안 스레드까지 따라가게
                for f in [ex.submit(contextvars.copy_context().run, _gen) for _ in range(nsamp)]:
                    try:
                        outs.append(f.result())
                    except Exception as e:
                        trace.append(f'초안 실패: {type(e).__name__}')
            t_draft = time.time() - t0
            # 퇴화한 초안을 병합기에 먹이면 오염이 번진다 — 먼저 걸러낸다
            drafts = [sanitize_answer(a) for a, _ in outs if a and a.strip()]
            drafts = [d for d in drafts if d and len(d) > 50]
            if outs and outs[0][1]:
                reasoning = outs[0][1]
            spent = time.time() - t_start
            base, agree = pick_base_scored(drafts) if drafts else ('', 0.0)
            if not drafts:
                # 셋 다 실패·퇴화. 빈 답을 내느니 한 번 더 받아본다.
                trace.append('초안 전멸 → 단일 생성으로 재시도')
                try:
                    ans = sanitize_answer(_gen()[0])
                except Exception:
                    ans = ''
            elif len(drafts) == 1:
                ans = drafts[0]
            elif spent > budget * 0.7:
                # 병합까지 가면 예산을 넘긴다. 합의가 가장 큰 초안으로 끝낸다.
                trace.append(f'자기일관성: 초안 {len(drafts)}개, 시간 부족({spent:.0f}s)으로 병합 생략')
                ans = base
            elif SKIP_MERGE_AT and agree >= SKIP_MERGE_AT:
                # 초안들이 이미 같은 내용이면 합쳐도 얻을 게 없다
                trace.append(f'자기일관성: 초안 {len(drafts)}개 ({t_draft:.1f}s), '
                             f'겹침 {agree:.2f} ≥ {SKIP_MERGE_AT:g} 이라 병합 생략')
                ans = base
            else:
                t1 = time.time()
                ans = merge_drafts(question, ctx, drafts, effort=merge_effort, hits=cite_hits)
                trace.append(f'자기일관성: 초안 {len(drafts)}개 ({t_draft:.1f}s, 겹침 {agree:.2f}) '
                             f'생성 후 병합 ({time.time() - t1:.1f}s)')
                if os.environ.get('AGENT_KEEP_DRAFTS'):
                    # 병합이 초안의 무엇을 흘리는지 보려면 초안이 남아 있어야 한다.
                    # 제출 응답에는 절대 실리면 안 되므로 반환 dict 가 아니라
                    # 모듈 전역에 따로 쌓고, server.py 는 규격 5필드만 골라 내보낸다.
                    LAST_DRAFTS[question_id or question[:40]] = drafts
    finally:
        clova.CHAT_MODEL = prev_model

    ans = finalize(ans, cite_hits, trace)
    reasoning_line(trace, reasoning, P.get('injection'))
    return {'question_id': question_id, 'question': question,
            'retrieved_context': ctx, 'think_trace': '\n'.join(trace),
            'answer': ans}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    q = sys.argv[1] if len(sys.argv) > 1 else '연금저축이랑 IRP에 넣으면 세액공제 얼마까지 되나요? 다 합쳐서요.'
    r = answer(q, 'Q-TEST')
    print('=== think_trace ===\n' + r['think_trace'][:600])
    print('\n=== answer ===\n' + r['answer'])
    print(f"\n(근거 {len(r['retrieved_context']):,}자)")
