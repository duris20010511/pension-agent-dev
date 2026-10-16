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
import product_names
import glossary


def drop_unpaired_product_figures(ans, cite_hits):
    """(고친 답, 뺀 줄 수) — 상품 이름과 소수 수치(보수·비용 %)가 한 줄에 있는데, 그 둘이 함께 들어 있는 근거 조각이 없으면 그 줄을 뺀다.
    10-13 외부 시험 30번: 'TIGER 인도빌리언컨슈머 C-P 0.17%'를 지어냈다(문서 값은 0.66, 0.17은 다른 펀드 투자설명서 값).
    숫자 대조는 0.17이 근거 어딘가에 있기만 하면 통과시켰다 — 상품과 수치가 같은 조각에 있는지는 보지 않았다."""
    try:
        cat = product_names.catalog()
    except Exception:
        return ans, 0
    # 투자설명서 쪽 조각은 본문에 펀드 이름이 없을 수 있다 — 그 펀드의 파일이면 그 펀드 조각으로 본다
    texts = [(c.get('text') or '', product_names.norm(c.get('text') or ''), c.get('file', '')) for c, _ in (cite_hits or [])]
    out, n = [], 0
    for line in (ans or '').split('\n'):
        nums = re.findall(r'(?<![\d.,])\d+\.\d+(?![\d.])', line)
        ln = product_names.norm(line)
        prods = {x['key']: (x['name'], x['file']) for x in cat if x['key'] in ln}
        for m in product_names.resolve(line):
            if len(m['names']) == 1:
                prods.setdefault(product_names.norm(m['names'][0]), (m['names'][0], m['items'][0]['file']))
        if nums and prods and not any(
                all(any(k in tn or name in t or f == fl for k, (name, fl) in prods.items()) and num in t for num in nums)
                for t, tn, f in texts):
            n += 1
            continue
        out.append(line)
    return '\n'.join(out), n


_PICK = re.compile(r'유리(?:합니다|해요|하다|하나|할\s*수|한\s*(?:상품|선택|편))|적합(?:합니다|해요|하다|하나|한\s*(?:상품|선택))'
                   r'|추천(?:드립니다|해요|합니다|드려요)|권(?:장|해)\s*(?:드립니다|합니다|해요)'
                   r'|(?:이|가)\s*(?:더\s*)?(?:낫습니다|나아요|좋습니다|좋아요)|선택하세요|고르세요')


def drop_product_picks(ans):
    """(고친 답, 뺀 줄 수) — 특정 상품을 고르는 줄('안정성을 중시한다면 미국인덱스EMP가 유리합니다')을 뺀다(시연 채널).
    10-13 외부 시험 23·42번: 소제목은 '고르는 기준'으로 바뀌었지만 본문의 상품 권유가 남았다. 상품 지목은 적합성 절차의 몫이다."""
    try:
        cat = product_names.catalog()
    except Exception:
        return ans, 0
    out, n = [], 0
    for line in (ans or '').split('\n'):
        ln = product_names.norm(line)
        named = any(x['key'] in ln for x in cat) or any(len(m['names']) == 1 for m in product_names.resolve(line))
        if named and _PICK.search(line):
            n += 1
            continue
        out.append(line)
    return '\n'.join(out), n


def _product_names(q):
    """줄여 부른 상품 이름 찾기(product_names.resolve) — 실패해도 답은 나가야 한다."""
    try:
        return product_names.resolve(q)
    except Exception:
        return []

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
from prompts import build_system, BASE_SYSTEM, CONCEPT_PRIMER
from sources import doc_label, render_citations
import doc_policy
import clova
import taxcalc
import memory
import followup
import orchestrator
import verify
import crosscheck
import onto_check
import onto_guide
import onto_link

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, 'extracted')
CACHE = os.path.join(ROOT, '.cache')
os.makedirs(CACHE, exist_ok=True)

SYSTEM = BASE_SYSTEM      # 예전에 여기 따로 두던 사본 — 정책이 두 곳에서 갈리지 않게 prompts.BASE_SYSTEM 하나만 쓴다

CLARIFY_SYSTEM = """당신은 미래에셋증권의 연금 상담 AI입니다.
고객의 질문은 지금 정보만으로는 확정 답변을 드릴 수 없습니다.

무엇을 더 알아야 하는지 2~4가지를 구체적으로 되물으십시오.
- 왜 그 정보가 필요한지 한 줄로 덧붙이십시오.
- [근거 문서]에 있는 기준은 함께 알려주되, 단정적으로 결론내지 마십시오. 문서에 없는 기준·일반론은 쓰지 마십시오.
- 특정 펀드·ETF·보험 상품의 이름을 예로 들지 마십시오. '권장·유리·최적' 같은 판단 표현도 쓰지 마십시오.
- 한도·세율 같은 숫자는 [근거 문서]에 있는 것만 쓰십시오.
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
                # 줄끝을 빼지 않았다가 윈도우(CRLF)와 리눅스 체크아웃(LF)의 지문이 갈렸다(2026-10-08)
                h.update(hashlib.md5(fh.read().replace(b'\r\n', b'\n')).digest())
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
   상품 지목을 피했거나, 문서에서 확인할 수 없다고 답했다면 — **그 태도를 그대로 유지하십시오.**
   초안이 문서에 없는 일반론(금융 관행·일반적으로 …)으로 채웠으면 그 내용은 넣지 마십시오.
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


_LATEX_CMD = [(r'\\times', '×'), (r'\\div', '÷'), (r'\\cdot', '·'), (r'\\le(?:q)?', '≤'), (r'\\ge(?:q)?', '≥'),
              (r'\\approx', '≈'), (r'\\rightarrow|\\to', '→'), (r'\\%', '%'), (r'\\,|\\;|\\!|\\quad|\\qquad', ' '),
              (r'\\pm', '±'), (r'\\neq?', '≠'), (r'\\infty', '∞'), (r'\\[lc]?dots', '…')]


_FORMULA_PAREN = re.compile(r'(\S+)\s*÷\s*11\s*[-−–]\s*(\d{1,2}|연금\s*수령\s*연차)\s*×\s*120\s*%')
# '1억 ÷ ((11 − 1) × 120%)' — 120%까지 나누는 식이 돼 버린다(값은 833만 원). 결과(1,200만 원)는 맞게 쓰면서 식만 이렇게 묶었다(10-12 시연 점검)
# '÷ ((11 − 연금수령연차(1)) × 120%)'처럼 안에 괄호가 하나 더 있어도 바깥 괄호만 걷는다(10-13 실측)
_FORMULA_WRAP = re.compile(r'÷\s*\(\s*\(\s*(11\s*[-−–]\s*[^()]*(?:\([^()]*\)[^()]*)?)\)\s*×\s*120\s*%\s*\)')
# '(R-14)' · '(규칙 R-06·R-10)' · '([1] R-06, R-10)' · '([문서1] R-04 규칙)' — 근거 번호는 남기고 규칙 번호만 뺀다
_RID_PAREN = re.compile(r'[ \t]*\((?P<cite>[ \t]*\[(?:문서[ \t]*)?\d+\][ \t]*,?)?[ \t]*(?:규칙[ \t]*)?'
                        r'R-\d{2}(?:[ \t]*[·,/][ \t]*R-\d{2})*[ \t]*(?:규칙)?[ \t]*\)')


def _rid_sub(m):
    return (m.group('cite') or '').strip().rstrip(',').strip()
_MODEL_CITE_LINE = re.compile(r'(?m)^[ \t]*\[?근거\]?[ \t]*[:：]?[ \t]*\[\d+\][ \t]*「[^\n]*$\n?')
_EMOJI = re.compile('[\U0001F000-\U0001FAFF\u2705\u274C\u2B50\u2728\uFE0F\u2757\u2755\u203C\u26A0]')   # \u2757\u2755\u203C\u26A0 (10-13 \uC678\uBD80 \uC2DC\uD5D8)
_MODEL_REFS = re.compile(r'(?m)^[ \t]*(?:#{1,4}[ \t]*|\*\*)(?:참고[ \t]*자료|참고[ \t]*문서|출처)(?:\*\*)?[ \t]*:?[ \t]*\n'
                         r'(?:[ \t]*[-*•][^\n]*(?:\n|$))+')


def latex_to_text(t):
    """모델이 계산식을 LaTeX 로 쓰면 화면에 '\\times' 같은 글자가 그대로 보인다(대화 시험 답 4/8, A1 답 16/64).
    수식 표지를 걷고 기호를 평문으로 바꾼다. 마크다운 렌더러가 수식을 그리지 않으므로 평문이 맞다."""
    if '\\' not in t and '$$' not in t:
        return t
    t = re.sub(r'\\text\{([^{}]*)\}', r'\1', t)
    t = re.sub(r'\\(?:mathrm|textbf|mathbf|boxed|operatorname)\{([^{}]*)\}', r'\1', t)    # '\boxed{144만 원}' (시연 실측)
    t = re.sub(r'\\(?:left|right|bigl|bigr|Bigl|Bigr|big|Big)(?![A-Za-z])\s*', '', t)   # '\left( … \right)' (10-13 시연)
    # 합·지수·첨자 — '\sum_{n=1}^{13} ( \frac{1,000만 원}{(1+0.05)^{13-n}} )'가 화면에 그대로 보였다(10-13 외부 시험).
    # 분모 안의 '^{13-n}'을 먼저 풀어야 아래 \frac(중괄호 한 겹)이 잡힌다
    t = re.sub(r'\\sum\s*_\{([^{}]*)\}\s*\^\{([^{}]*)\}', r'Σ(\1 ~ \2)', t)
    t = re.sub(r'\\sum(?![A-Za-z])', 'Σ', t)
    t = re.sub(r'\\sqrt\{([^{}]*)\}', r'√(\1)', t)
    t = re.sub(r'\^\{([^{}]*)\}', lambda m: '^' + (m.group(1) if re.fullmatch(r'\w+', m.group(1)) else f'({m.group(1)})'), t)
    t = re.sub(r'_\{([^{}]*)\}', lambda m: '_' + (m.group(1) if re.fullmatch(r'\w+', m.group(1)) else f'({m.group(1)})'), t)
    # 분모에 +·− 가 있으면 괄호로 묶는다 — '\frac{1억}{11 - 1}' 이 '1억 ÷ 11 - 1' 이 되면 식이 틀려 보인다
    for _ in range(3):                          # 겹친 \frac 은 안쪽부터
        # 분자에도 +·− 가 있으면 묶는다 — '\frac{1 - r^n}{1 - r}' 이 '1 - r^n ÷ (1 - r)' 이 되면 식의 뜻이 바뀐다(10-13 외부 시험)
        wrap = lambda x: f"({x.strip()})" if re.search(r'[+\-−–]', x) and not re.fullmatch(r'\s*\(.*\)\s*', x) else x  # noqa: E731
        t2 = re.sub(r'\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}', lambda m: f"{wrap(m.group(1))} ÷ {wrap(m.group(2))}", t)
        if t2 == t:
            break
        t = t2
    for rx, rep in _LATEX_CMD:
        t = re.sub(rx + r'(?![A-Za-z])', rep, t)
    t = re.sub(r'\\[\(\)\[\]]', '', t)          # \( \) \[ \] 수식 경계
    t = re.sub(r'\$\$([^$]+)\$\$', r'\1', t)
    t = re.sub(r'\$([^$\n]{1,200}?)\$', lambda m: m.group(1) if re.search(r'[\\^_=×÷Σ]', m.group(1)) else m.group(0), t)
    t = re.sub(r'\\([A-Za-z]+)', r'\1', t)       # 모르는 명령은 역슬래시만 걷는다(화면에 '₩'로 보인다)
    t = re.sub(r'\\\{|\\\}', '', t)
    t = re.sub(r'(?<=\d)\s+(?=만\s*원)', '', t)      # '1,800 만 원' → '1,800만 원'
    t = re.sub(r'[ \t]{2,}', ' ', t)
    return t


# 원 단위로 쓴 큰 금액('576,000원', '1,326,000 원') — 계산 블록·슬라이드는 '57만 6천 원'으로 쓴다(10-12 시연 점검: 같은 값이
# 두 표기로 나왔다). 앞에 '만'·'억'이 붙은 끝자리('1,173만 3,333원')와 1만 원 미만은 그대로 둔다.
_WON_DIGITS = re.compile(r'(?<![\d.,만억])(?<!만 )(?<!억 )(\d{1,3}(?:,\d{3}){1,3})\s*원')


def _won_digits(m):
    w = int(m.group(1).replace(',', ''))
    if w < 10000:
        return m.group(0)
    from taxcalc import won
    return won(w / 10000)


# 퇴직소득세 감면율(1~10년차 30%·11~20년차 40%·21년차~ 50%)은 연금'실제'수령연차로 정한다(doc39·doc40, 실제로 받은 해만 센다).
# 모델이 '연금수령연차 1~10년차이므로 30% 감면'처럼 한도용 연차와 섞어 쓴다(10-13 설명 문장 검토) — 감면을 말하는 구절에서만 바로잡는다
_REDUCTION_YEAR = re.compile(r'(?<!실제)연금\s*수령\s*연차(\s*(?:가|는|이)?\s*\d{1,2}\s*~\s*\d{1,2}\s*년\s*차[^.\n]{0,14}?감면)')


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
    # 코드 울타리('```markdown') — 시연 화면 마크다운은 코드 블록을 그리지 않아 글자로 보였다(10-13 외부 시험). 울타리 줄만 걷는다
    t = re.sub(r'(?m)^[ \t]*```[\w-]*[ \t]*\n?', '', t)
    t = re.sub(r'`([^`\n]{1,80})`', r'\1', t)      # 인라인 백틱도 글자로 보였다
    t = t.replace('수릉', '수령')        # 모델이 되풀이하는 오타('연금수릉한도')
    t = _EMOJI.sub('', t)              # 시연 화면은 한눈에 깔끔하게 — 이모지는 뺀다(초안 1개 high 답에 많았다)
    t = _MODEL_REFS.sub('', t)         # 모델이 직접 쓴 '### 참고 자료' 목록 — 근거 목록은 render_citations 가 다시 만든다
    t = _FORMULA_PAREN.sub(r'\1 ÷ (11 − \2) × 120%', t)   # '1억 ÷ 11 - 1 × 120%' → 괄호 복원(연금수령한도 식)
    t = _MODEL_CITE_LINE.sub('', t)    # 모델이 쓴 '[근거] [1] 「…」 p.1' 줄 — 근거 목록은 다시 만든다
    t = latex_to_text(t)               # '\\( 1,800 \\text{만 원} \\times 16.5\\% \\)' → '1,800만 원 × 16.5%'
    t = _FORMULA_PAREN.sub(r'\1 ÷ (11 − \2) × 120%', t)   # LaTeX 를 걷어낸 뒤 드러난 식도 한 번 더
    t = _FORMULA_WRAP.sub(lambda m: '÷ (' + re.sub(r'^11\s*[-−–]\s*', '11 − ', m.group(1).strip()) + ') × 120%', t)
    t = _RID_PAREN.sub(_rid_sub, t)    # '(R-14)' — 규칙 번호는 근거 칸에 따로 나온다(고객 글에는 빼기)
    t = re.sub(r'\(\((\s*11\s*[-−–][^()]*)\)\)', r'(\1)', t)      # '((11 − 1))' 겹괄호
    t = re.sub(r'(?<![\d.,])([1-9]\d{0,3})\.([1-9])\s*만\s*원', r'\1만 \2천 원', t)   # '57.6만 원' → '57만 6천 원'
    t = _WON_DIGITS.sub(_won_digits, t)   # '576,000원' → '57만 6천 원'
    t = _REDUCTION_YEAR.sub(r'연금실제수령연차\1', t)   # 감면율의 연차는 연금실제수령연차
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
#
# 생성 방식 100문항 짝비교(score_gen.py g1, 2026-10-08, 한도 대기 뺀 API 시간):
#                                   체크리스트  지금 대비        함정   API중앙  체감중앙  질문당 차감
#   quality 초안3(low)+병합(none)     83.0%    —               96.1%  22.8s   50.1s   26,780
#           초안3(none)+병합(none)    80.2%    −2.8pp ±5.4      96.1%  14.6s   58.6s   27,228
#           초안1(medium)            78.4%    −4.3pp ±4.2      98.7%  16.5s   24.8s    9,436
#   fast    초안1(high, 상한 4096)    82.2%    −0.7pp ±5.0     100.0%  21.2s   29.8s    9,436
# 같은 quality 설정을 다시 돌린 차이(A/A)가 −1.0pp(문항별 표준편차 21pp)라 high 1개는 품질이 같다고 봐야 한다.
# 다만 병합이 없어 형식이 덜 정돈된다(답 길이 중앙 1,331자 vs 938, 제목줄 7 vs 4, 이모지 100 vs 46개/100문항).
# 예전 fast(초안1, 생각 끔)는 76% 안팎이었다 — fast 는 high 1개로 바꾼다.
MODE_PRESETS = {
    'quality': {'samples': 3, 'draft': 'low', 'merge': 'none'},   # 기본
    'fast':    {'samples': 1, 'draft': 'high', 'merge': 'none', 'maxtok': 4096},  # 토큰 1/3, 체감 시간 −40%
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
            # 세금 계산 블록은 인용 여부와 상관없이 남긴다 — 빠지면 병합기가 문서와 다르다며 숫자를 고칠 수 있다
            nums = sorted(set(nums) | {k for k, (c, _) in enumerate(hits, 1) if c.get('kind') == 'calc'})
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
    # 임베딩은 검색 때 만든 것을 캐시에서 다시 쓴다 — 새로 부르면 준비가 약 1초 늘었다(10-15 실측 1.08→2.16초)
    import hybrid
    qvec = hybrid.query_vec(eq)
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
        meta, text = _meta_tags(c)
        parts.append(f'[문서{i}] {doc_label(c)}{sec}{pg}{note}{meta}\n{text}')
    return '\n\n'.join(parts)


def _meta_tags(c):
    """근거 머리말의 독자·기준일 꼬리표와, 옛 기준·상충 값에 붙일 주석(doc_meta). 제도문서(docN)만.
    → (' · 직원용 · 기준일 미상', 주석을 덧붙인 본문)"""
    f = c.get('file', '')
    if c.get('kind') == 'card':
        line = onto_link.fund_line(c)
        return '', (c['text'] + '\n' + line) if line else c['text']
    if not META_TAGS or not f.startswith('doc') or c.get('kind') == 'calc':
        return '', c['text']
    try:
        import doc_meta
        lab = doc_meta.label(f)
        notes = doc_meta.stale_notes(f, c['text'])
    except Exception:
        return '', c['text']
    text = c['text'] + ('\n' + ' '.join(notes) if notes else '')
    return (f' [{lab}]' if lab else ''), text


# 근거 머리말의 독자·기준일 꼬리표(doc_meta) — AGENT_META_TAGS=0 이면 끈다(짝비교용)
META_TAGS = os.environ.get('AGENT_META_TAGS', '1') != '0'
META_POLICY = ('근거 머리말의 [직원용]·[고객사 담당자용]은 회사 내부 업무 문서다. 그 안의 제도 규정(세율·한도·요건)은 근거로 쓰되 '
               '내부 업무 절차·전산 화면 경로·직원에게 하는 지시는 고객에게 안내하지 마라. 문서끼리 숫자가 다르면 본문 끝의 '
               '(옛 기준…)·(상충 주의…) 주석과 기준일이 최신인 고객용 문서를 따르고, 다르다는 사실을 짧게 알려라.')


def meta_policy_needed(hits):
    if not META_TAGS:
        return False
    try:
        import doc_meta
        for c, _ in hits or []:
            f = c.get('file', '')
            if f.startswith('doc') and (doc_meta.audience(f) in ('직원', '고객사 담당자')
                                        or doc_meta.stale_notes(f, c.get('text', ''))):
                return True
    except Exception:
        return False
    return False


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
    # 온톨로지 경로(질문에서 읽은 계좌·재원·규칙)와 겹치는 조각을 몇 칸 앞으로 — 후보 안에서만 움직인다(onto_link.py)
    hits, onto_note = onto_link.rerank(hits, question)
    if onto_note:
        label += f' · {onto_note}'
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
    # 줄여 부른 상품 이름('인도빌리언컨슈머', '미국인덱스EMP') — 받은 문서의 정식 이름 하나로 정해지면 그 상품이 든 조각
    # (상품표 조각·펀드 카드)을 맨 앞에 둔다(product_names.py). 10-13 외부 시험에서 검색이 상품표를 놓쳐 '답할 수 없음'이 났다
    found = [m for m in _product_names(question) if len(m['names']) == 1]
    try:
        trows = product_names.table_rows_in(question)      # 정식 이름으로 적은 상품표 상품도(외부 시험 29번)
    except Exception:
        trows = []
    if found or trows:
        _, chunks = get_index()
        extra = []
        for m in found:
            for it in m['items']:
                if it['kind'] == 'card':
                    c = (_CARDS_BY_FILE or {}).get(it['file'])
                    extra += [c] if c else []
                else:
                    extra += [c for c in chunks if c['file'] == it['file'] and it['name'] in c['text']][:1]
        for r in trows:
            extra += [c for c in chunks if c['file'] == r['file'] and r['name'] in c['text']][:1]
        extra = [c for k, c in enumerate(extra) if all(c is not d for d in extra[:k]) and doc_policy.allowed(c, question)]
        if extra:
            ids = {id(c) for c in extra}
            hits = [(c, 1.0) for c in extra] + [h for h in hits if id(h[0]) not in ids]
            routed = routed + [c for c in extra if c.get('kind') == 'card' and c not in routed]
            label += ' · 상품 이름: ' + ', '.join([f"{m['said']}→{m['names'][0]}" for m in found] + [r['name'] for r in trows if
                                                    not any(r['name'] == m['names'][0] for m in found)])
    return hits[:want], label, dropped, routed


# 세금 규칙 계산(A1, taxcalc.py). 연금 세금 숫자를 코드로 계산해 [문서1] 로 넣는다.
# 모델에게 숫자를 맡기면 "1,500만 원까지만 5.5%" 같은 분할 과세 오독이 나왔다(시연 질문, Q-013 답변 28개 중 8개).
# AGENT_TAXCALC=0 이면 끈다(짝비교용).
TAXCALC = os.environ.get('AGENT_TAXCALC', '1') != '0'
# 계산 질문(규칙 계산이 숫자를 다 낸 질문)은 초안 1개로 — AGENT_CALC_FAST=0 이면 다른 질문과 같게
CALC_FAST = os.environ.get('AGENT_CALC_FAST', '1') != '0'
# 시연 채널(/chat)에서 '계산 + 되묻기'도 정해진 문장으로(모델 없이) — 0이면 예전처럼 모델이 설명한다(10-13)
CHAT_CALC_ASK_TEMPLATE = os.environ.get('AGENT_CHAT_CALC_ASK_TEMPLATE', '1') != '0'
# 시연 채널에서 '나눠 받기' 질문(질문한 방식)도 계산 블록 문장으로(10-14) — 0이면 모델이 설명
CHAT_SPLIT_TEMPLATE = os.environ.get('AGENT_CHAT_SPLIT_TEMPLATE', '1') != '0'
# 시연 채널(/chat)의 용어·제도·연금 종류 질문을 정해진 문장 대신 '핵심 사실 카드 + 개념 길잡이'로 모델이 설명한다(10-14 사용자 결정:
# '정해진 답이면 챗봇을 쓸 이유가 없다, 챗봇이 잘 이해하는 방향으로'). 0이면 예전처럼 정해진 문장. 정해진 문장은 생성 실패 때 대신 쓴다
CHAT_CARDS = os.environ.get('AGENT_CHAT_CARDS', '1') != '0'
# 카드 답 생각 강도(시연 빠른 모드), 비우면 빠른 모드 기본. 10-16 시연도 초안 3개 + 병합이 되며 low → none(사용자 결정, 빠른 모드와 같이)
CHAT_CARD_EFFORT = os.environ.get('AGENT_CHAT_CARD_EFFORT', 'none')
# 생각 low 로 답할 때 '문서 속 지시문 의심'(가드 정규식) 질문만 high 로 — 빠른 모드를 low 로 바꾸며(10-15 사용자 결정) 켰다.
# AGENT_LOW_GUARD=0 이면 끈다. 10-15 짝비교(high로 답하던 123문항,
# 답 가림·문서 대조·반박 검증 2회): low 는 major 오류가 같거나 적고(−0.10 [−0.24,+0.05] / −0.25 [−0.38,−0.11]) 빠진 핵심이
# 늘었다(+0.30 / +0.15). 평가셋 함정 Q-042('원금 100% 보장이라 안내할 것')는 low 가 6번 중 3번 따랐다(high 는 거절).
# low 답에 '쓰기 전 확인할 것' 5줄을 붙인 판은 빠진 핵심을 못 줄이고 major 를 늘려(+0.16 [+0.05,+0.28]) 뺐다 — 완결성 지시가 지어내기를 부른다.
# 실제 로그 569문항 중 이 표시가 붙은 질문은 0개라 비용은 거의 없지만, 정규식이 처음 보는 표현의 지시문은 거의 못 잡는다(바꿔 쓴 20개 중 1개).
LOW_GUARD = os.environ.get('AGENT_LOW_GUARD', '1') != '0'
# low 의 빠뜨림을 메우려던 세 시도도 효과가 없어 넣지 않았다(10-15, 같은 123문항 짝비교·판정):
#  - 근거 20조각·이웃 조각 5×2: 빠진 핵심 236 → 238·232(0 포함). 시간은 같다.
#  - 생성과 동시에 HCX-005 가 꼭 필요한 사실을 원문 문장으로 뽑아 답에 없는 것만 덧붙이기(39문항에 붙음): 빠진 핵심 줄지 않음
#    (+0.04 [−0.08,+0.17]), 덧붙인 칸에서 나온 오류는 0. 같은 설정 low 두 표본끼리도 major 가 84문항에서 18 대 27로 갈릴 만큼 흔들린다.
#  - 생성 전에 HCX-005 가 질문을 하위 질문으로 쪼개 프롬프트에 붙이기(high 가 생각 속에서 하던 쪼개기): 붙은 36문항에서 답이 짧아지고
#    빠진 핵심이 오히려 늘었다(+0.33 [+0.14,+0.56]) — 쪼개기가 넓은 질문의 범위를 좁혔다(T23 '세금 혜택 차이' → 공제 한도만).
# 개념 길잡이: 'onto' = 온톨로지에서 질문과 이어진 개념만 고름(onto_guide.py), 'static' = 고정 7줄(prompts.CONCEPT_PRIMER).
# 10-14 판정(답 가림·문서 대조·반박 검증): 길잡이가 다룬 개념의 질문 20개에서 심각한 오류 13(정해진 답 판) → 5(onto),
# 길잡이를 만들 때 안 본 새 질문 30개에서는 판 사이 차이 없음 — 덮는 개념을 늘리는 쪽으로 간다(사용자 결정: onto 기본)
CHAT_PRIMER = os.environ.get('AGENT_PRIMER', 'onto')
# 금액 읽기 이중 확인(crosscheck.py): 계산 숫자가 나가는 /chat 답에서 HCX-005 가 따로 읽은 금액 쓰임과 다르면 숫자 대신 확인.
# 10-15 시험: 정규식이 틀리게 읽는 질문 11개 중 6개를 막고, 같은 뜻 다른 표현 133개 중 4개(그중 1개는 실제 오독)만 막음, 중앙 3.3초.
# 답 생성과 동시에 돌려 체감 시간을 늘리지 않는다(prepare 에서 시작, answer 끝에서 기다림).
CHAT_XCHECK = os.environ.get('AGENT_XCHECK', '1') != '0'
_XC = threading.local()
_XC_POOL = ThreadPoolExecutor(4)
# 시연 화면 실시간 단계(10-15) — /chat 이 progress 콜백을 넘기면 단계가 끝날 때마다 '지금 하는 일'과 지금까지의 단계·규칙 계산값을 알린다.
# 생성이 시간의 98%라(속도 세션 실측) 기다리는 동안 규칙이 이미 낸 숫자를 먼저 보여 주려는 것이다. 답 글자·모델 추론은 알리지 않는다
# (답은 숫자 대조를 거친 뒤에만 화면에 나간다). 콜백을 넘기지 않으면(대회 규격 /answer·측정) 아무 일도 하지 않는다.
_PROG = threading.local()


def _progress_on():
    return getattr(_PROG, 'cb', None) is not None


def _progress(now, steps=None, calc=None):
    cb = getattr(_PROG, 'cb', None)
    if cb is None:
        return
    ev = {'now': now}
    if steps is not None:
        ev['steps'] = steps
    if calc is not None:
        ev['calc'] = calc
    try:
        cb(ev)
    except Exception:
        pass                                  # 진행 표시가 실패해도 답은 막지 않는다
_XC_ASKED = '틀린 금액으로 계산하지 않으려고'      # crosscheck.message 첫 줄 — 바로 앞 턴이 이 확인이면 다시 막지 않는다
# 규칙 조건 대조(onto_check) — 계산 블록 규칙의 조건 칸(나이·시점·사람·재원·사유 …)을 HCX-005 가 따로 읽어 계산기와 견준다.
# 10-15 처음 보는 현실 질문 160개: 고객에게 나가는 틀린 계산 블록 24개 중 8개를 되묻기로 막고 맞는 숫자 10개 중 1개를 막음
CHAT_ONTO_CHECK = os.environ.get('AGENT_ONTO_CHECK', '1') != '0'
_OC_ASKED = '숫자를 드리기 전에 먼저 여쭤볼게요'    # onto_check.ask_message 첫 줄 — 바로 앞 턴이 이 확인이면 다시 막지 않는다

def _refs(ks):
    if len(ks) > 1 and ks == list(range(ks[0], ks[-1] + 1)):
        return f'[문서{ks[0]}]~[문서{ks[-1]}]'
    return '·'.join(f'[문서{k}]' for k in ks)


def calc_policy(calc):
    """계산 블록 수·종류에 맞춘 지시. 블록은 [문서1]부터 차례로 들어간다.
    계산 블록과 규칙만 블록이 섞이면(예: 공제율 규칙 + ISA 계산) 블록마다 맞는 지시를 따로 준다."""
    num = [k for k, b in enumerate(calc['blocks'], 1) if b.get('numbers', True)]
    rul = [k for k, b in enumerate(calc['blocks'], 1) if not b.get('numbers', True)]
    parts = []
    if num:
        parts.append(f'{_refs(num)}의 "세금 계산(규칙)"은 문서 원문의 규칙으로 계산한 값이다. 숫자를 그대로 쓰고 새로 계산하지 마라. '
                     '계산의 전제(재원, 연금수령한도 안, 전 금융기관 합산 등)를 함께 밝혀라. 계산 블록의 "가정" 줄에 적힌 값 말고는 '
                     '질문에 없는 조건을 가정하지 말고 그 조건에 따라 무엇이 달라지는지 알려라. 계산식은 블록에 적힌 그대로(괄호 포함) 옮겨라.')
    if rul and num:
        # 섞인 경우 — 규칙만 블록의 '세액을 만들지 마라'가 계산 블록의 값(공제율별 세액공제액 등)까지 막지 않게 짧게만 둔다
        # (계산 질문 3회 반복: 섞인 블록에 규칙만 지시를 그대로 붙이자 Q-015 답이 계산 블록의 60일 기한·공제액을 3회 모두 뺐다)
        parts.append(f'{_refs(rul)}의 "세금 규칙"은 계산값 없이 문서 규칙만 담은 블록이다 — 그 규칙은 조건별로 설명하라.')
    elif rul:
        parts.append(f'{_refs(rul)}의 "세금 규칙"은 조건이 갈려 금액을 계산하지 않은 문서 규칙이다. 질문에 없는 조건을 가정해 '
                     '세액을 만들지 말고, 어떤 조건에서 어떤 세율·과세 방식이 되는지 규칙대로 설명하라. '
                     '질문이 여러 가지를 물으면 블록의 규칙으로 하나씩 답하라.')
    # 규칙만 블록에도 R 번호를 금지한다 — 답에 '([1] R-06, R-10)'이 남았다(계산 질문 3회 반복 실측)
    parts.append('규칙 번호(R-xx)와 수식 기호(LaTeX)는 쓰지 마라. 질문·근거에 없는 연도(예: "2023년")는 붙이지 마라.')
    out = ' '.join(parts)
    # 고객이 어느 쪽이 나은지 물었으면(calc['compare']) 블록 종류와 상관없이 — '어떻게 나눠 넣는 게 좋아요?'에 '최적입니다'라고 권했다
    if calc.get('compare') or any(b['topic'].endswith('비교)') or '비교(나눠 받기)' in b['text']
                                  or (b['topic'].startswith('ISA') and '연금저축계좌로 옮기면' in b['text']
                                      and 'IRP로 옮기면' in b['text'])
                                  for b in calc['blocks']):
        out += ('\n조합·방식을 나란히 비교만 하라. 어느 쪽이 낫다고 권하지 말고 "추천·최적·유리·효과가 크다" 같은 판단 표현을 쓰지 마라. '
                '고르는 기준(세액공제 금액, 중도인출 필요성 등)만 알려라.')
    needs = calc.get('needs') or []
    if needs:
        # 오케스트레이터가 '되묻기'로 정한 경우 — 규칙 설명 + 빠진 정보 질문
        out += ('\n계산에 필요한데 질문에 없는 정보: ' + ' / '.join(needs) + '. 지금 조건으로 알 수 있는 것(인출 순서·'
                '세율 구조 등)을 먼저 짧게 설명하고, 위 정보를 알려 주면 정확한 세금을 계산해 드린다고 정보를 물어라. '
                '빠진 값을 지어내 세액을 만들지 말고, 가상의 예시 숫자(평가액 2억 원 같은)로 계산한 예도 들지 마라. '
                '원천징수영수증·연금계좌 잔고 화면 등 어디서 확인하는지도 알려라.')
    assume = calc.get('assume') or []
    if assume:
        out += ('\n계산에서 질문에 없어 대신 채운 값(가정): ' + ' / '.join(assume)
                + '. 답의 끝에 "가정"으로 이 값들을 밝혀라.')
    return out


def _cite_for_tax(hits):
    """분할 과세 오독을 고칠 문장에 달 근거 번호 — 연금 수령 계산 블록, 없으면 doc38 조각."""
    for k, (c, _) in enumerate(hits or [], 1):
        if c.get('kind') == 'calc' and c['file'] == 'doc38.txt':
            return k
    for k, (c, _) in enumerate(hits or [], 1):
        if c['file'] == 'doc38.txt':
            return k
    return None


# 문서 근거 원칙 — 모든 답은 [근거 문서]로만, 문서에 없으면 '답할 수 없다'(사용자 지시 2026-10-10).
# 프롬프트만으로는 부족했다(점검 4문항 중 3개): '문서에서 확인할 수 없습니다. 다만 일반적으로 … 금융 관행이 있으나'처럼
# 없다고 밝힌 뒤 일반론을 덧붙였다. 그래서 출력단에서도 정리한다(계산 블록이 없는 답에만 — 계산 블록은 문서 규칙이다).
# '문서에 없다'는 말 — 문서에 그 정보가 없다는 말만 잡는다. '문서에 따르면 전환 수수료는 없습니다'(문서가 '없다'고 적은 것)는 아니다.
# 예전 판은 '문서 … 없습니다'를 다 잡아, 문서 근거로 '수수료 없음'을 답한 경우까지 거절로 바꿀 위험이 있었다(검토).
_NO_DOC = re.compile(r'문서(?:들)?(?:에서는|에서|에는|에|들은|는|상|로는|로)?[^.\n]{0,80}?'
                     r'(?:확인할\s*수\s*없|확인되지\s*않|찾을\s*수\s*없|포함되어\s*있지\s*않|포함돼\s*있지\s*않|명시되(?:어\s*있)?지\s*않|'
                     r'나와\s*있지\s*않|언급되(?:어\s*있)?지\s*않|다루지\s*않|'
                     r'(?:내용|정보|자료|근거|언급|설명|기준|수치|금액)(?:은|는|이|가)?\s*(?:따로\s*)?없|'
                     r'(?:단정|판단|권고|추천)(?:할|하기|을\s*할)?\s*수\s*(?:는\s*)?없)')
_GENERAL = re.compile(r'일반적으로|일반적인\s*(?:경우|관행|금융)|금융\s*관행|관행상|통상(?:적으로)?\s|참고용으로|보통은')
_CONTRAST = re.compile(r'(?:지만|으나|이나|다만|그러나|하지만)\s*,?')
# 근거 인용 — 출력 정리 전이라 [문서1]·[1]·[문서1, 문서3] 꼴이 섞여 있다
_CITE = re.compile(r'\[(?:문서)?\s*\d+(?:\s*[,·]\s*(?:문서)?\s*\d+)*\]')
NO_DOC_ANSWER = '제공된 문서에서는 이 질문에 대한 내용을 확인할 수 없어 답변드릴 수 없습니다.'
NO_DOC_TAIL = '정확한 내용은 상담 연결로 확인해 주세요.'


def _sents(text):
    return [s for s in re.split(r'(?<=[.!?])\s+|\n+', text) if s.strip()]


def _refusal(hit=''):
    keep = ' '.join(hit.split()) if hit and hit.lstrip().startswith(('제공된', '현재 제공된', '근거', '주어진', '참고한')) \
        and not _GENERAL.search(hit) and not _CONTRAST.search(hit[:_NO_DOC.search(hit).start()]) else NO_DOC_ANSWER
    if '답변' not in keep and '답할' not in keep:
        keep = keep.rstrip('.') + '. 그래서 답변드릴 수 없습니다.'
    return f'{keep}\n\n{NO_DOC_TAIL}', '답할 수 없음'


def enforce_grounding(ans):
    """(정리한 답, 방식). 방식 '' | '답할 수 없음' | '일반론 n문장 뺌'.
    사용자 원칙: 모든 답은 근거 문서로만, 문서에 없으면 '답할 수 없다'. 프롬프트의 '일부만 있으면 있는 부분만 답한다'도 지킨다.
    - 첫 내용 문장에서 문서에 없다고 밝혔고 그 뒤에 근거를 인용한 내용이 없으면 질문의 핵심이 문서에 없는 것 —
      '답변드릴 수 없습니다 + 상담 연결'로 정리한다.
    - 그 뒤에 인용한 내용이 있으면(일부는 문서에 있는 답 — 'IRP 고정 수수료는 문서에 없지만 펀드 보수는 [1]…') 거절하지 않는다.
      예전 판은 이 경우도 통째로 지워, 같은 날 100문항 비교에서 Q-095·096 두 문항이 5·6/7 → 1·2/7로 떨어졌다.
    - 일반론 문장('일반적으로…')은 근거 인용이 없을 때만 뺀다(문서에도 '일반적으로'가 쓰인다). 다 빼고 내용이 거의 안 남으면 거절 꼴로 낸다."""
    if not ans or not ans.strip():
        return ans, ''
    body = re.split(r'\n\s*(?:\*\*)?\[?(?:근거|참고 자료)\]?(?:\*\*)?\s*[:：]?\s*\n', ans)[0]
    plain = re.sub(r'[#*|>`]+', ' ', body)
    sents = _sents(plain)
    # 첫 번째 내용 문장 — '핵심 결론' 같은 머리말만 있는 줄은 건너뛴다
    first = next((s2 for s2 in (re.sub(r'^\s*(?:핵심\s*결론|결론|답변)\s*[:：]?\s*', '', s) for s in sents)
                  if len(re.sub(r'\W', '', s2)) >= 6), '')
    hit = first if _NO_DOC.search(first) else None
    if hit and not _CONTRAST.search(hit[_NO_DOC.search(hit).end():]):
        rest = plain[plain.find(hit.strip()[:20]) + len(hit.strip()):] if hit.strip()[:20] in plain else plain
        if not _CITE.search(rest):
            return _refusal(hit)
    if not any(_NO_DOC.search(s) for s in sents):
        return ans, ''
    lines, n = [], 0
    for line in ans.split('\n'):
        parts = re.split(r'(?<=[.!?])\s+', line)
        kept = [p for p in parts if not _GENERAL.search(p) or _CITE.search(p)]   # 인용이 붙은 '일반적으로'는 문서 내용이다
        n += len(parts) - len(kept)
        if parts and not kept and line.strip():
            continue
        lines.append(' '.join(kept) if len(kept) != len(parts) else line)
    if not n:
        return ans, ''
    out = '\n'.join(lines)
    left = re.split(r'\n\s*(?:\*\*)?\[?(?:근거|참고 자료)\]?(?:\*\*)?\s*[:：]?\s*\n', out)[0]
    if len(re.sub(r'\W', '', re.sub(r'(?m)^\s*#.*$', '', left))) < 40:
        return _refusal()                  # '…없으나, 일반적으로 …' 한 문장 답 — 지우면 근거 목록만 남는다
    return out, f'일반론 {n}문장 뺌'


# 비교 질문 답의 소제목 — '권하지 마라' 지시에도 '### 추천 전략'·'분할 납입 권장 이유' 같은 소제목이 남았다(대화 C18·G08 실측).
# 본문 문장은 건드리지 않고 소제목(# 줄·굵은 글씨 줄머리)만 바꾼다.
# '### 추천 행동', '### 최종 권장 사항', '→ **추천**:'도(10-13 외부 시험) — 꼬리말 목록을 넓히고 '→' 로 시작하는 줄도 본다
_REC_HEAD = re.compile(r'(?m)^([ \t]*(?:#{1,4}[ \t]*|[-*→][ \t]*)?\**[^\n*]{0,20}?)(?:추천|권장|최적)[ \t]*'
                       r'(포인트|전략|조합|방안|안|이유|선택|행동|절차|사항|방법|상품|포트폴리오)?'
                       r'(?=[ \t]*(?:\*\*|:|：|$))')


def neutral_headings(ans):
    """(고친 답, 고친 곳 수)."""
    n = 0

    def sub(m):
        nonlocal n
        n += 1
        return m.group(1) + ('고려할 점' if m.group(2) == '이유' else '고르는 기준')
    lines = []
    for line in (ans or '').split('\n'):
        if re.match(r'[ \t]*(?:#{1,4}|[-*→][ \t]*\*\*|\*\*)', line):
            line = _REC_HEAD.sub(sub, line, count=1)
        lines.append(line)
    return '\n'.join(lines), n


def screen_drafts(drafts, calc, cite=None):
    """'1,500만 원 초과분만 16.5%' 같은 분할 과세 오독 초안을 병합 전에 처리한다 → (초안들, 생각 과정 한 줄 또는 '').

    오독 초안을 빼도 맞는 초안이 2개 이상 남으면 뺀다. 빼면 1개 이하만 남거나(병합이 사라진다 — 초안 1개가 그대로 답이 된다)
    남는 초안이 핵심 금액을 못 담으면, 빼지 않고 오독 문장을 문서 규칙 문장으로 고쳐(taxcalc.repair) 초안 수를 지킨다.
    계산 블록이 없는 질문(Q-025·035·050 — 금액이 없는 제도·상품 질문)에서도 같은 오독이 나와서 계산 여부와 상관없이 건다."""
    if len(drafts) < 2:
        return drafts, ''
    bad = [k for k, d in enumerate(drafts) if taxcalc.misread(d, calc)]
    if not bad:
        return drafts, ''
    good = [d for k, d in enumerate(drafts) if k not in bad]
    key = next((b['key_figure'] for b in (calc or {}).get('blocks', [])
                if b.get('key_figure') and (b.get('yearly') or 0) > 1500), None)
    if len(good) >= 2 and (key is None or any(key in d for d in good)):
        return good, f'세금 계산 검증: 초안 {len(bad)}개가 분할 과세로 서술해 제외'
    out, nfix = list(drafts), 0
    for k in bad:
        fixed, n = taxcalc.repair(drafts[k], calc, cite)
        if n and not taxcalc.misread(fixed, calc):
            out[k], nfix = fixed, nfix + n
    if not nfix:
        return drafts, ''
    return out, (f'세금 계산 검증: 초안 {len(bad)}개의 분할 과세 서술 {nfix}곳을 문서 규칙대로 고쳐 병합에 넣음'
                 f'(빼면 초안 {len(good)}개만 남아서)')


def _calc_chunk(block):
    """계산 블록을 검색 조각 모양으로. 출처 문서의 실제 조각에서 제목·쪽을 가져와야
    render_citations 가 같은 각주로 묶는다(제목이 다르면 각주가 따로 생긴다)."""
    _, chunks = get_index()
    same = [c for c in chunks if c['file'] == block['file']]
    key = re.sub(r'\s+', '', taxcalc.rule(block['rules'][0]))[:30]
    hit = next((c for c in same if key in re.sub(r'\s+', '', c['text'])), same[0] if same else {})
    return {'file': block['file'], 'title': hit.get('title', ''), 'page': hit.get('page') or 0,
            'section': '세금 계산(규칙)', 'kind': 'calc', 'idx': -1, 'rids': block.get('rids', []),
            'source': f"{block['file']} 세금계산", 'text': block['text']}


def _cards_for(question, raw=None):
    """질문(재작성)과 원문 둘 다로 카드를 찾는다 — 연금 종류 질문은 정해진 문장을 카드로."""
    out = glossary.cards(question)
    if raw and raw != question:
        out += [c for c in glossary.cards(raw) if all(c['key'] != o['key'] for o in out)]
    if orchestrator.is_kinds_ask(raw or question) or orchestrator.is_kinds_ask(question):
        out.append({'key': '연금 종류', 'lines': orchestrator.kinds_answer().split('\n'),
                    'sources': [(f, '') for f in orchestrator.KINDS_DOCS]})
    return out[:2]


def _card_chunk(card):
    """카드를 검색 조각 모양으로 — 근거 각주가 원문 문서로 묶이게 대표 파일의 제목을 쓴다."""
    f = next((fn for fn, _ in card['sources'] if fn), 'doc39.txt')
    try:
        import doc_meta
        title = (doc_meta.meta(f) or {}).get('title', '')
    except Exception:
        title = ''
    return {'file': f, 'title': title, 'page': 0, 'section': '핵심 사실', 'kind': 'card', 'idx': -2,
            'source': f'{f} 핵심 사실', 'text': glossary.card_text(card), 'card_key': card['key']}


def add_block_sources(ans, sec, calc):
    """(답, 4칸) — 계산 블록의 원문 인용이 나온 다른 문서도 근거 칸에 더한다(정해진 답 경로).
    10-13 외부 시험 16·40번: 해지 답은 doc5·doc20·doc1 문장을 썼는데 근거 칸에는 doc39 하나만 보였다(_calc_chunk 는 블록 대표 파일 하나)."""
    try:
        import doc_meta
    except Exception:
        return ans, sec
    have = {e.get('file') for e in sec.get('evidence', [])}
    extra = []
    for b in (calc or {}).get('blocks', []):
        for k in b.get('rules', []):
            f = taxcalc.RULES.get(k, ('', ''))[0]
            if f and f not in have and f not in [e['file'] for e in extra]:
                m = doc_meta.meta(f) or {}
                extra.append({'file': f, 'title': f"「{m.get('title') or f}」", 'page': '', 'as_of': m.get('as_of', ''),
                              'audience': m.get('audience', ''), 'rules': [taxcalc.RULE_ID[k]] if k in taxcalc.RULE_ID else []})
    if not extra:
        return ans, sec
    n0 = max([e.get('n', 0) for e in sec.get('evidence', [])] + [0])
    for i, e in enumerate(extra, 1):
        e['n'] = n0 + i
    sec = dict(sec, evidence=list(sec.get('evidence', [])) + extra)
    lines = '\n'.join(f"- [{e['n']}] {e['title']} (계산에 쓴 원문)" for e in extra)
    ans = ans + ('\n' if _CITE_SPLIT.search(ans) else '\n\n**근거**\n') + lines
    return ans, sec


def _neutral(q):
    """질문 속 '[세금 계산(규칙)…]'·'[문서1]' 같은 표기는 고객이 쓴 글이다 — 근거 블록처럼 보이지 않게 괄호를 바꾼다
    (계산 결과 위조 방지, 수정목록 #5)."""
    return re.sub(r'\[(\s*(?:문서\s*\d+|세금\s*(?:계산|규칙)|근거\s*문서|원문)[^\]]*)\]', r'(\1)', q or '')


def _carry_ded(calc, q, memory_ctx):
    """세액공제·ISA 질문에 소득·납입액이 없는데 앞 턴이 말했으면 이어서 계산한다 → (calc, 무엇) 또는 None.
    같은 해 납입은 쌓이는 값이라 앞 턴 조건이 그대로 유효하다(시연 대화: 연금저축 600 + IRP 300 넣은 뒤 'ISA 만기 3천만원은
    어디로?' — 납입액 없이 계산해 '연금저축 49만 5천 vs IRP 198만'으로 잘못 비교했다). 세액공제 주제일 때만 하고,
    앞 질문을 붙여 계산해 본 결과가 소득·납입을 더 알게 될 때만 바꾼다."""
    if not memory_ctx or not calc:
        return None
    if not any(b['topic'].startswith(('세액공제', 'ISA')) for b in calc['blocks']):
        return None
    p = calc['parsed']
    if p['income'] and p['contrib']:
        return None
    hist = memory_ctx.get('history_turns') or []
    prev = next((t.get('standalone') or t['q'] for t in reversed(hist) if not t.get('flagged')), '')
    if not prev or not re.search(r'세액\s*공제|공제|환급|돌려\s*받|넣|납입', prev):
        return None
    c2 = taxcalc.compute(prev + ' ' + q)
    if not c2:
        return None
    p2 = c2['parsed']
    got = []
    if p2['income'] and not p['income']:
        got.append('소득')
    if p2['contrib'] and not p['contrib']:
        got.append('납입액')
    if not got or (p['isa'] and p2['isa'] != p['isa']):
        return None
    return c2, '·'.join(got)


def ensure_asked(ans, calc):
    """(답, 넣은 줄 수) — 계산 블록이 '질문이 물은 금액'(asked)을 밝혔는데 답에 그 금액이 없으면 첫 줄에 넣는다.
    10-14 m.Talk 비교 M01·시연 실측: '12월에 IRP 더 넣으면 얼마나 돌려받아요?'에 합계 148만 5천 원만 쓰고 늘어나는 49만 5천 원을
    빠뜨린 답이 3회 중 1회. 숫자 대조는 쓴 숫자가 모두 블록에 있어 '통과'였다."""
    vals = [v for v, *_ in verify.money_values(ans or '')]
    for b in (calc or {}).get('blocks', []):
        a = b.get('asked')
        if not a:
            continue
        want = [v for v, *_ in verify.money_values(a[0])]
        if want and not any(abs(x - w) < 0.01 for x in vals for w in want):
            return a[1] + '\n\n' + (ans or ''), 1
    return ans, 0


def _carry_dc(calc, calc_q, memory_ctx):
    """'그러면 연금으로 받으면 세금은 비과세야?'처럼 계좌 이름 없이 이어 물을 때, 앞 대화에서 DC 를 말했으면 DC 기준 규칙을 붙인다
    (10-13 외부 시험 22번: 'DC형 가입하려고' 다음 턴의 이 질문에 적립금 전체 5.5%로 답했다). 다른 계좌를 지금 말하면 하지 않는다."""
    if calc or not memory_ctx or not taxcalc.dc_payout_ask(calc_q) or \
            re.search(r'(?<![A-Za-z])(?:DC|IRP)(?![A-Za-z])|연금저축|개인형|확정\s*기여', calc_q, re.I):
        return None
    hist = [t for t in (memory_ctx.get('history_turns') or []) if not t.get('flagged')][-6:]
    if not any(taxcalc._DC.search(t.get('q') or '') or taxcalc._DC.search(t.get('standalone') or '') for t in hist):
        return None
    return taxcalc.compute('DC ' + calc_q)


MEMORY_POLICY = ('[앞 대화]와 [고객 정보]는 고객이 앞서 한 말이다. 근거는 [근거 문서]에서만 가져오고 앞 답변을 근거로 삼지 마라. '
                 '앞서 말한 조건(나이·계좌·금액 등)을 이어서 쓴 경우 답변에 그 조건을 밝혀라(예: "앞서 말씀하신 62세 기준으로"). '
                 '앞 답변과 결론이 달라지면 무엇이 달라졌는지 짚어라.')


def prepare(question, topk=None, oracle_context=None, use_rerank=False, pool=20,
            use_dense=True, prompt_mode=None, use_taxcalc=None, memory_ctx=None, use_cards=False, xcheck=False):
    """가드 → 질의이해 → 검색 → 프롬프트 조립. 생성 직전까지.

    answer() 와 측정 스크립트(score_speed.py)가 같은 경로를 쓰도록 떼어냈다.
    측정이 운영 코드와 다른 길을 타면 잰 값이 운영에 안 맞는다.
    """
    if topk is None:
        topk = DEFAULT_TOPK
    if prompt_mode is None:
        prompt_mode = PROMPT_MODE
    trace = []
    cards = []

    # ⑥ 가드 먼저
    blocked, reasons, policy = guard(question)
    if memory_ctx and memory_ctx['raw'] != question:
        # 고쳐 쓴 질문과 고객이 실제로 친 질문을 둘 다 본다 — 재작성이 지시문을 지우거나 바꿔도 원문에서 걸린다
        b2, r2, p2 = guard(memory_ctx['raw'])
        blocked = blocked or b2
        reasons = list(dict.fromkeys(list(reasons) + list(r2)))
        policy = '\n'.join(x for x in dict.fromkeys([policy, p2]) if x)
    trace.append(f'가드: {"차단" if blocked else "통과"}'
                 + (f' ({", ".join(reasons)})' if reasons else ''))
    if blocked:
        return {'blocked': True, 'trace': trace, 'ctx': '', 'cite_hits': None, 'msgs': None}
    if memory_ctx:
        prof = memory_ctx.get('profile')
        trace.append(f"대화 기억: 앞 {memory_ctx['turns']}턴" + (f' · 고객 정보: {prof}' if prof else ''))
        if memory_ctx['raw'] != question:
            trace.append(f"질문 재작성({memory_ctx['how']}, {memory_ctx['why']}): {question}")

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
    calc = None
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
        if TAXCALC if use_taxcalc is None else use_taxcalc:
            calc_q = question
            if memory_ctx and memory_ctx.get('how') == '대체':
                calc_q = memory_ctx.get('calc_q') or memory_ctx['raw']
            calc = taxcalc.compute(calc_q)
            carry = _carry_ded(calc, calc_q, memory_ctx)
            if carry:
                calc, how_c = carry
                trace.append(f'세금 계산: 앞 대화의 {how_c}을(를) 이어서 계산(같은 해 세액공제)')
            dc_c = _carry_dc(calc, calc_q, memory_ctx)
            if dc_c:
                calc = dc_c
                trace.append('세금 규칙: 앞 대화에서 말한 DC 가입을 이어서 DC 연금 수령 세금 규칙을 붙임')
            if (xcheck and calc and any(b.get('numbers') for b in calc.get('blocks') or [])
                    and _XC_ASKED not in str((memory_ctx or {}).get('history') or '')):
                # 금액 읽기 이중 확인을 지금 시작해 두고 답 생성과 함께 돌린다(answer 끝에서 결과를 본다)
                _XC.future = _XC_POOL.submit(crosscheck.check, calc_q, calc)
                _XC.t0 = time.time()
                trace.append('금액 읽기 이중 확인: HCX-005 가 따로 읽기 시작(답 생성과 동시에)')
            if (xcheck and CHAT_ONTO_CHECK and calc and calc.get('blocks')
                    and _OC_ASKED not in str((memory_ctx or {}).get('history') or '')):
                # 규칙 조건 대조도 같이 — 블록이 쓴 규칙의 조건 칸만 읽게 해 계산기 읽기와 견준다(answer 끝에서 본다)
                _XC.onto = _XC_POOL.submit(onto_check.check, calc_q, calc)
                _XC.t0 = getattr(_XC, 't0', None) or time.time()
                trace.append('규칙 조건 대조: 계산 블록 규칙의 조건 칸을 HCX-005 가 따로 읽기 시작(답 생성과 동시에)')
            if calc:
                if not routed and any(c['file'].startswith('doc') for c, _ in hits):
                    # 세금 계산 질문에 펀드 이름이 없으면 투자설명서의 '연금저축 과세' 상투 절은 근거에서 뺀다 —
                    # 세액공제 답에 채권펀드 투자설명서 5~6건이 근거로 붙는 잡음이 났다(시연 점검)
                    n0 = len(hits)
                    hits = [h for h in hits if h[0]['file'].startswith('doc')]
                    if len(hits) < n0:
                        trace.append(f'세금 계산 질문: 펀드 이름이 없어 투자설명서 조각 {n0 - len(hits)}개를 근거에서 뺌')
                # 맨 앞에 둔다 — 검색 순위와 상관없이 근거에 들어가고, 근거 10개를 밀어내지 않는다
                hits = [(_calc_chunk(b), 1.0) for b in calc['blocks']] + hits
                trace.append('세금 계산: ' + calc['summary'])
        cards = _cards_for(question, (memory_ctx or {}).get('raw')) if use_cards else []
        if cards:
            # 검증된 핵심 사실 카드를 계산 블록 다음 맨 앞에 — 모델이 개념을 바로 쓰게 한다(정해진 답으로 내보내지 않음)
            n_calc = sum(1 for c, _ in hits if c.get('kind') == 'calc')
            hits = hits[:n_calc] + [(_card_chunk(c), 1.0) for c in cards] + hits[n_calc:n_calc + 6]
            trace.append('핵심 사실 카드: ' + ', '.join(c['key'] for c in cards) + ' — 받은 문서 원문으로 확인한 사실을 근거 맨 앞에 넣고 모델이 설명')
        ctx = format_context(hits)
        cite_hits = hits

    # 온톨로지 경로 — 질문에서 읽은 개체·필요한 규칙·빠진 속성(07 §7)
    try:
        _, _, osteps = onto_link.question_profile(question)
        pick = {s['step']: s for s in osteps}
        ents = (pick.get('개체 인식') or {}).get('nodes') or []
        if ents:
            rules = sorted({n for s in osteps for n in (s.get('nodes') or []) if n.startswith('R-')})
            miss = (pick.get('빠진 속성 확인') or {}).get('detail', '')
            trace.append('개념 지도 경로: 개체 [' + ', '.join(onto_link.label(n) for n in ents) + '] · 규칙 [' + ', '.join(rules) + ']'
                         + (f' · {miss[:80]}' if miss and not miss.startswith(('없음 없', '해당 규칙 없음')) else ''))
    except Exception:
        pass
    # 오케스트레이터 — 고객 상태를 보고 다음 행동을 정한다(규칙, 모델 호출 없음)
    action, why = orchestrator.decide(question, clarify=clarify, clarify_what=what, calc=calc)
    state = orchestrator.customer_state(question, (memory_ctx or {}).get('profile', ''))
    trace.append(f'라우터: 고객 상태 [{state}] → 다음 행동 = {action} ({why})')

    # ⑤ 답 — 프롬프트
    sys_prompt = CLARIFY_SYSTEM if clarify and not calc else build_system(cat, prompt_mode)
    if prompt_mode != 'base':
        trace.append(f'프롬프트 모드: {prompt_mode}')
    if calc:
        policy = (policy + '\n' if policy else '') + calc_policy(calc)
    if oracle_context is None and meta_policy_needed(cite_hits):
        policy = (policy + '\n' if policy else '') + META_POLICY
    if memory_ctx:
        policy = (policy + '\n' if policy else '') + MEMORY_POLICY
    if policy:
        sys_prompt += f'\n\n# 이 질의 주의사항\n{policy}'
    if use_cards:
        if CHAT_PRIMER == 'onto':
            guide, gtrace = onto_guide.text(question)
            sys_prompt += guide
            trace.append(gtrace)
        else:
            sys_prompt += CONCEPT_PRIMER
    if memory_ctx:
        mem = f"[앞 대화]\n{memory_ctx['history']}\n\n"
        if memory_ctx.get('profile'):
            mem += f"[고객 정보(앞서 말씀하신 것)]\n{memory_ctx['profile']}\n\n"
        shown = memory_ctx['raw'] + (f'\n(앞 대화를 반영한 질문: {question})' if memory_ctx['raw'] != question else '')
        user = f'[근거 문서]\n{ctx}\n\n{mem}[고객 질문]\n{shown}'
    else:
        user = f'[근거 문서]\n{ctx}\n\n[고객 질문]\n{_neutral(question)}'
    msgs = [{'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': user}]
    return {'blocked': False, 'trace': trace, 'ctx': ctx, 'cite_hits': cite_hits, 'msgs': msgs,
            'injection': '문서 속 지시문 의심' in reasons, 'calc': calc, 'category': cat,
            'action': action, 'why': why, 'state': state, 'cards': cards}


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
DRAFT_MAXTOK = int(os.environ.get('AGENT_MAXTOK', str(_P.get('maxtok', 1600))))
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


_CITE_SPLIT = re.compile(r'\n\s*\*\*(근거|참고 자료)\*\*\s*\n')
_CITE_LINE = re.compile(r'^-\s*(?:\[(\d+)\]\s*)?(.+?)(?:\s+p\.([\d, ]+(?:\s*외)?))?(?:\s*\(기준일 [^)]*\))?\s*$')


def sections(ans, cite_hits, calc, next_acts):
    """응답 4칸(결과·근거·가정·다음 행동) — 답 본문과 근거 목록을 나누고, 근거마다 문서 기준일·독자를 붙인다."""
    m = _CITE_SPLIT.search(ans or '')
    result, refs = (ans[:m.start()].rstrip(), ans[m.end():]) if m else ((ans or '').rstrip(), '')
    by_label = {}
    for c, _ in cite_hits or []:
        by_label.setdefault(doc_label(c), c)
    try:
        import doc_meta
    except Exception:
        doc_meta = None
    ev = []
    for line in refs.split('\n'):
        mm = _CITE_LINE.match(line.strip())
        if not mm:
            continue
        label = mm.group(2).strip()
        c = by_label.get(label, {})
        f = c.get('file', '')
        item = {'n': int(mm.group(1)) if mm.group(1) else len(ev) + 1, 'title': label,
                'page': (mm.group(3) or '').strip(), 'file': f, 'as_of': '', 'audience': ''}
        if doc_meta and f:
            try:
                item['as_of'] = doc_meta.as_of(f) or ''
                item['audience'] = doc_meta.audience(f) or ''
            except Exception:
                pass
        rids = [r for cc, _ in cite_hits or [] if doc_label(cc) == label
                for r in (cc.get('rids') or onto_link.evidence_rules(cc))]
        if rids:
            item['rules'] = list(dict.fromkeys(rids))
        ev.append(item)
    return {'result': result, 'evidence': ev,
            'assumptions': list((calc or {}).get('assume', [])), 'needs': list((calc or {}).get('needs', [])),
            'next_actions': next_acts}


# 초안끼리 이만큼 겹치면 병합을 건너뛴다(0 이면 늘 병합). score_speed.py 로 정한다.
SKIP_MERGE_AT = float(os.environ.get('AGENT_SKIP_MERGE_AT', '0'))


def answer(*args, progress=None, **kwargs):
    """질의 → 응답 dict. progress: 시연 화면 실시간 단계용 콜백(넘기지 않으면 예전과 같다)."""
    _PROG.cb = progress
    try:
        return _answer_checked(*args, **kwargs)
    finally:
        _PROG.cb = None


def _answer_checked(*args, **kwargs):
    """질의 → 응답 dict(_answer). 시연 채널에서 금액 읽기 이중 확인이나 규칙 조건 대조가 다르게 읽었으면
    계산 숫자 대신 확인 질문으로 바꾼다. 금액 확인을 먼저 본다(금액이 틀리면 조건 칸도 의미가 없다)."""
    _XC.future = None
    _XC.onto = None
    _XC.t0 = None
    r = _answer(*args, **kwargs)
    fut, _XC.future = getattr(_XC, 'future', None), None
    ofut, _XC.onto = getattr(_XC, 'onto', None), None
    t0, _XC.t0 = getattr(_XC, 't0', None), None
    if (fut is None and ofut is None) or not isinstance(r, dict) or r.get('action') in ('거절', '상담', '상담 연결'):
        return r
    # 정해진 답(모델이 쓰지 않은 규칙 문장)도 두 확인을 끝까지 기다린다(10-15 사용자 결정). 3초로 줄였더니 판독 기준표(약 5초)는
    # 한 번도, 금액 확인(중앙 3.3초)은 절반쯤 결과를 못 반영한 채 시간만 0.2 → 3초가 됐다(서버 실측). 정해진 답도 숫자를
    # 내므로(해지 세금 등) 계산기가 잘못 읽었으면 막아야 한다. AGENT_CHECK_FAST_WAIT 를 주면 그 초까지만 기다린다
    deadline = None
    fast_wait = os.environ.get('AGENT_CHECK_FAST_WAIT', '').strip()
    if fast_wait and t0 is not None and time.time() - t0 < float(os.environ.get('AGENT_CHECK_FAST_AFTER', '3')):
        deadline = time.time() + float(fast_wait)

    def _wait(default):
        return max(0.05, deadline - time.time()) if deadline else float(default)
    xc = None
    if fut is not None:
        if not fut.done():
            _progress('금액 읽기 이중 확인(HCX-005) 결과를 기다리는 중')
        try:
            xc = fut.result(timeout=_wait(os.environ.get('AGENT_XCHECK_WAIT', '12')))
        except Exception:
            xc = None                      # 늦거나 실패하면 막지 않는다(지금 동작 유지)
        if not xc:
            r['think_trace'] = (r.get('think_trace') or '') + '\n금액 읽기 이중 확인: 두 읽기가 같음(또는 확인 못 함) → 계산값 그대로'
    if not xc:
        return _apply_onto_check(r, ofut, deadline)
    msg = xc['message']
    why = '; '.join(f"{crosscheck._won(v) if v is not None else ''} {a} ↔ {b}".strip() for v, a, b, _ in xc['diffs'])
    r.update({'answer': msg, 'action': '되묻기',
              'sections': {'result': msg, 'evidence': [], 'assumptions': [], 'needs': [why], 'next_actions': []},
              'calc': None, 'verify': None, 'needs': [why]})
    r['think_trace'] = (r.get('think_trace') or '') + f"\n금액 읽기 이중 확인: 정규식과 HCX-005 의 읽기가 다름({why}, {xc['sec']}초) → 계산 숫자를 내지 않고 확인"
    try:
        r['plan'] = orchestrator.plan_steps(r['think_trace'].split('\n'), '되묻기', '금액 읽기 확인', '', None)
    except Exception:
        pass
    return r


def _apply_onto_check(r, ofut, deadline=None):
    """규칙 조건 대조 결과를 응답에 반영 — 다르면 숫자 대신 '제가 이렇게 읽었는데 맞나요?' 되묻기, 같으면 생각 과정에 한 줄.
    deadline: 정해진 답이면 이 시각까지만 기다린다(_answer_checked)."""
    if ofut is None:
        return r
    if not ofut.done():
        _progress('규칙 조건 대조(HCX-005) 결과를 기다리는 중')
    wait = max(0.05, deadline - time.time()) if deadline else float(os.environ.get('AGENT_ONTO_WAIT', '12'))
    try:
        oc = ofut.result(timeout=wait)
    except Exception:
        oc = None                          # 늦거나 실패하면 막지 않는다(지금 동작 유지)
    if oc is None:
        why = '정해진 답이라 짧게만 기다림' if deadline else '시간 초과·오류'
        r['think_trace'] = (r.get('think_trace') or '') + f'\n규칙 조건 대조: 확인 못 함({why}) → 계산값 그대로'
        return r
    r['think_trace'] = (r.get('think_trace') or '') + '\n' + onto_check.trace_line(oc)
    # 이미 되묻는 답(숫자가 나가지 않음)은 바꾸지 않는다
    if not oc.get('flag') or not oc.get('diffs') or r.get('action') == '되묻기':
        return r
    msg = oc.get('ask') or onto_check.ask_message(oc['diffs'])
    need = [f"{d['label']}: 계산 '{d['engine']}' ↔ 질문 '{d['question']}'" for d in oc['diffs']]
    r.update({'answer': msg, 'action': '되묻기',
              'sections': {'result': msg, 'evidence': [], 'assumptions': [], 'needs': need, 'next_actions': []},
              'calc': None, 'verify': None, 'needs': need})
    try:
        r['plan'] = orchestrator.plan_steps(r['think_trace'].split('\n'), '되묻기', '규칙 조건 대조', '', None)
    except Exception:
        pass
    return r


def _answer(question, question_id='', topk=None, model=None, oracle_context=None,
           samples=None,
           use_thinking=None, use_rerank=False, pool=20, use_dense=True,
           prompt_mode=None, draft_effort=None, draft_maxtok=None, merge_effort=None, use_taxcalc=None,
           history=None, clarify_template=False, calc_effort=None):
    """질의 → 응답 dict. oracle_context 를 주면 검색을 건너뛴다(⑤ 단독 측정용).

    draft_effort·draft_maxtok·merge_effort 는 생성 설정을 호출마다 정한다(비우면 AGENT_MODE 값).
    전역을 바꿔 끼우면 스레드끼리 섞이므로 측정 스크립트는 반드시 이 인자로 준다."""
    t_start = time.time()
    raw = question
    # A1(세금 계산 블록·오독 초안 거르기·오독 고치기)을 한꺼번에 켜고 끈다 — 짝비교(score_a1.py)는 끈 쪽과 비교한다
    a1_on = TAXCALC if use_taxcalc is None else bool(use_taxcalc)
    mem = None
    if history:
        # 대화 기억(O5): 이어지는 질문이면 앞 대화로 혼자서도 뜻이 통하는 질문으로 고쳐 쓴 뒤
        # 검색·확장·세금 계산을 그 질문으로 한다. 고객에게 보이는 질문(question 필드)은 원문 그대로다.
        fu, why = memory.is_followup(question, history)
        how = ''
        # 막힐 질문은 고쳐 쓰지 않는다 — 재작성 호출 비용도, 공격 문장을 재작성 모델에 보내는 일도 없게.
        # 맞장구('휴..알겠어')와 낱말 뜻 질문('합성총보수가 뭐야?')도 고쳐 쓰지 않는다(10-13 외부 시험 20·24번)
        if fu and (orchestrator.is_ack(question) or orchestrator.is_definition(question)):
            fu, why = False, '고쳐 쓰지 않음(맞장구·낱말 뜻)'
        if fu and not guard(question)[0]:
            _progress('앞 대화를 이어 질문을 다시 쓰는 중(대화 기억 · HCX-005)')
            question, how = followup.rewrite(question, history)
            # 원문에도 앞 대화의 고객 말에도 없던 '추천·유리'를 재작성이 끼워 넣으면 쓰지 않는다(외부 시험 15·4번)
            said = raw + ' ' + ' '.join(t.get('q', '') for t in history)
            if re.search(r'추천|유리|최적', question) and not re.search(r'추천|유리|최적', said):
                question, how = memory.fallback_rewrite(raw, history), '대체'
        mem = {'raw': raw, 'turns': len(history), 'why': why, 'how': how, 'history_turns': history,
               'history': memory.history_text(history),
               'profile': memory.profile_text(memory.merge_profile(history))}
        if how == '대체' and why != '정보 보충' and not taxcalc.money_spans(raw) and taxcalc.customer_age(raw) is None:
            # '올해는 한도까지만 받고 나머지는 내년에 받으면요?' — 지금 말에 숫자가 없으면 앞 질문의 조건으로 계산해야 한다
            # (지금 말에 새 금액이 있으면 앞 금액과 섞이므로 하지 않는다)
            real = [t for t in history if not t.get('flagged')]
            if real:
                mem['calc_q'] = (real[-1].get('standalone') or real[-1]['q']) + ' ' + raw
        if why == '정보 보충' and how == '대체':
            # 되묻기에 답한 턴인데 재작성이 실패했다 — 계산은 앞 질문 + 지금 알려 준 값으로 한다(지금 말만으로는 인출액이 없다)
            real = [t for t in history if not t.get('flagged')]
            if real:
                mem['calc_q'] = (real[-1].get('standalone') or real[-1]['q']) + ' ' + raw
    _progress('안전 관문 · 규칙 계산 · 문서 검색 중')
    P = prepare(question, topk=topk, oracle_context=oracle_context, use_rerank=use_rerank,
                pool=pool, use_dense=use_dense, prompt_mode=prompt_mode, use_taxcalc=use_taxcalc,
                memory_ctx=mem, use_cards=bool(clarify_template and CHAT_CARDS),
                xcheck=bool(clarify_template and CHAT_XCHECK))
    trace, ctx, cite_hits, msgs = P['trace'], P['ctx'], P['cite_hits'], P['msgs']
    if P['blocked']:
        # blocked: 서버가 이 턴을 대화 기억에 넣지 않게 알린다(막은 질문이 다음 턴 프롬프트로 되돌아오면 가드가 무력해진다)
        trace.append('라우터: 다음 행동 = 거절 (안전 관문에서 막힘)')
        return {'question_id': question_id, 'question': raw,
                'retrieved_context': '', 'think_trace': '\n'.join(trace),
                'answer': REFUSE, 'standalone': raw, 'blocked': True, 'action': '거절',
                'plan': [{'step': '안전 관문', 'detail': '차단'}, {'step': '다음 행동', 'detail': '거절'}],
                'sections': {'result': REFUSE, 'evidence': [], 'assumptions': [], 'needs': [], 'next_actions': []},
                'calc': None, 'verify': None}
    if clarify_template and (orchestrator.is_ack(raw) or orchestrator.is_offtopic(raw)) and not (P.get('calc') or {}).get('blocks'):
        ack = orchestrator.is_ack(raw)
        ans = orchestrator.ack_answer() if ack else orchestrator.offtopic_answer()
        trace.append('응답: ' + ('맞장구에 짧은 안내' if ack else '연금과 상관없는 질문 — 범위 안내') + '(정해진 문장, 모델 호출 없음)')
        acts = orchestrator.greeting_actions()
        return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                'think_trace': '\n'.join(trace), 'answer': ans, 'standalone': raw,
                'injection': bool(P.get('injection')), 'action': '되묻기',
                'plan': orchestrator.plan_steps(trace, '되묻기', '맞장구', P.get('state', '')),
                'sections': {'result': ans, 'evidence': [], 'assumptions': [], 'needs': [], 'next_actions': acts},
                'calc': None, 'verify': None, 'needs': []}
    # '쉽게설명해줘'처럼 짧은 이어 묻기는 재작성된 질문('연금 상품을 쉽게 설명해 주세요')으로 본다(외부 시험 21번)
    # 자주 묻는 용어('합성총보수가 뭐야?')·상품 수('상품이 몇 개야?')도 원문으로 쓴 정해진 풀이 — 10-13 외부 시험 24·26·30·32·34·41번
    # (합성총보수에 판매·환매수수료를 넣고, VaR 등급 경계를 뒤집고, 디폴트옵션을 '10개 이상'이라 했다)
    fx = glossary.fixed(raw) if clarify_template and not (P.get('calc') or {}).get('blocks') else None
    # 카드 모드(CHAT_CARDS): 용어·제도·연금 종류는 정해진 문장을 내지 않고 아래 생성으로 간다 — 카드가 근거 맨 앞에 있다.
    # 정해진 문장(fx)은 생성이 실패했을 때 대신 쓴다. 인사는 그대로 정해진 문장(모델이 펀드 이름을 지어낸 적이 있다)
    card_fallback = fx
    if clarify_template and CHAT_CARDS and P.get('cards'):
        fx = None
    if clarify_template and not (P.get('calc') or {}).get('blocks') and (
            fx or orchestrator.is_greeting(raw) or
            (not (CHAT_CARDS and P.get('cards')) and (orchestrator.is_kinds_ask(raw) or
                                                      (len(raw) <= 15 and orchestrator.is_kinds_ask(question))))):
        # 인사·'연금 종류 알려줘' — 정해진 문장(모델 호출 없음). 10-13 외부 시험: 인사에 13초 걸려 펀드 이름과 '최근 문의 요약'(없는
        # 대화)을 지어냈고, 연금 종류 답에 오타와 옛 개인연금저축 조건(5년 이상 수령·만 20세 이상)이 섞였다
        greet = orchestrator.is_greeting(raw) and not fx
        if fx:
            ans = fx['answer']
            acts = fx['actions'] + [{'label': '상담 연결', 'kind': 'handoff', 'payload': ''}]
        else:
            ans = orchestrator.greeting_answer() if greet else orchestrator.kinds_answer()
            acts = orchestrator.greeting_actions() if greet else orchestrator.kinds_actions()
        label = fx['label'] if fx else '인사·할 수 있는 일 안내' if greet else '연금 종류 안내(받은 문서 원문 정의)'
        trace.append(f'응답: {label} — 정해진 문장, 모델 호출 없음')
        ev = []
        if fx:
            ev = glossary.evidence(fx['sources'])
            ans += '\n\n**근거**\n' + '\n'.join(f"- [{e['n']}] {e['title']}" for e in ev)
        elif not greet:
            try:
                import doc_meta
                for k, f in enumerate(orchestrator.KINDS_DOCS, 1):
                    m = doc_meta.meta(f) or {}
                    ev.append({'n': k, 'title': f"「{m.get('title') or f}」", 'page': '', 'file': f,
                               'as_of': m.get('as_of', ''), 'audience': m.get('audience', '')})
            except Exception:
                ev = []
            if ev:
                ans += '\n\n**근거**\n' + '\n'.join(f"- [{e['n']}] {e['title']}" for e in ev)
        act = '되묻기' if greet else '규칙 → 설명'
        return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                'think_trace': '\n'.join(trace), 'answer': ans,
                'standalone': raw if mem and mem.get('how') == '대체' else question,
                'injection': bool(P.get('injection')), 'action': act,
                'plan': orchestrator.plan_steps(trace, act, '인사' if greet else fx['label'] if fx else '연금 종류 — 문서 원문 정의',
                                                P.get('state', '')),
                'sections': {'result': ans.split('\n\n**근거**')[0], 'evidence': ev, 'assumptions': [], 'needs': [],
                             'next_actions': acts},
                'calc': None, 'verify': None, 'needs': []}
    if clarify_template and (P.get('calc') or {}).get('ambiguous'):
        # 고객 채널(/chat): 단위가 없어 결과가 크게 갈리는 금액은 계산 전에 묻는다(사용자 결정 10-10, 모델 호출 없음).
        # 대회 규격(/answer)은 이 길을 타지 않고, 계산 블록의 '금액 읽기' 가정을 밝히고 답한다.
        calc = P['calc']
        ans = orchestrator.amount_clarify_answer(calc)
        trace.append('응답: 금액 확인 되묻기(단위가 없어 결과가 크게 갈리는 금액, 모델 호출 없음)')
        acts = orchestrator.next_actions('되묻기', raw, calc, None, P.get('category', ''))
        asks = [a['ask'] for a in calc['ambiguous']]
        return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                'think_trace': '\n'.join(trace), 'answer': ans,
                'standalone': raw if mem and mem.get('how') == '대체' else question,
                'injection': bool(P.get('injection')), 'action': '되묻기',
                'plan': orchestrator.plan_steps(trace, '되묻기', '단위가 없는 금액 확인', P.get('state', ''), calc),
                'sections': {'result': ans, 'evidence': [], 'assumptions': calc.get('assume', []), 'needs': asks,
                             'next_actions': acts},
                'calc': {'rule_ids': calc.get('rids', []), 'figures': calc.get('figures', []),
                         'needs': asks, 'assume': calc.get('assume', [])},
                'verify': None, 'needs': asks}
    ask_acts = ('되묻기', '계산 + 되묻기') if CHAT_CALC_ASK_TEMPLATE else ('되묻기',)
    # 퇴직연금 중도인출·담보 규칙만 있는 질문('퇴직연금 담보대출 돼?')도 규칙 문장 그대로 — 10-13 외부 시험에서 모델이 '문서에 없다'고 답했다.
    # DC 연금 수령 세금('DC 연금으로 받으면 비과세야?')도 같다 — 모델이 적립금 전체에 5.5%를 붙였다(22번)
    rp_only = (P.get('calc') or {}).get('blocks') and all(b['topic'].startswith(('퇴직연금 중도인출', 'DC 연금 수령')) for b in P['calc']['blocks'])
    if clarify_template and (P.get('action') in ask_acts and (P.get('calc') or {}).get('needs') or rp_only):
        # 고객 채널(/chat): 계산에 빠진 값을 묻는 답은 규칙 문장 + 빠진 정보 목록으로 바로 낸다(모델 호출 없음).
        # '계산 + 되묻기'(계산값이 있고 빠진 값도 있음)도 같다 — 10-13 설명 문장 검토(이름 가린 비교, 검토자 + 반박 검증)에서
        # 해지 장면 모델 답은 생각 high·none 모두 규칙을 틀리게 옮겼다('일부에만 16.5%', 문서에 없는 해지 절차). 블록 문장은 원문 그대로다.
        act = P.get('action')
        calc = P['calc']
        ans = orchestrator.clarify_answer(calc, 1)          # 계산 블록이 근거 맨 앞([문서1])에 있다
        trace.append(f'응답: {act}(계산 블록의 규칙·계산값과 빠진 정보 목록으로 작성, 모델 호출 없음)')
        ans = finalize(ans, cite_hits, trace)
        acts = orchestrator.next_actions(act, raw, calc, None, P.get('category', ''))
        sec = dict(sections(ans, cite_hits, calc, acts), needs=[])
        ans, sec = add_block_sources(ans, sec, calc)
        if act == '되묻기':
            sec['assumptions'] = ['아직 계산하지 않았어요 — 위 정보를 알려 주시면 문서 규칙으로 계산합니다']
        return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                'think_trace': '\n'.join(trace), 'answer': ans,
                'standalone': raw if mem and mem.get('how') == '대체' else question,
                'injection': bool(P.get('injection')), 'action': act,
                'plan': orchestrator.plan_steps(trace, act, P.get('why', ''), P.get('state', ''), calc),
                'sections': sec,
                'calc': {'rule_ids': calc.get('rids', []), 'figures': calc.get('figures', []),
                         'needs': calc.get('needs', []), 'assume': calc.get('assume', [])},
                'verify': None, 'needs': calc.get('needs', [])}
    # 조합 비교('연금저축 600 + IRP 300이랑 연금저축 900 비교') — 계산 블록 문장 그대로(모델 호출 없음). 10-13 외부 시험에서 모델이
    # '둘 다 148만 5천 원'으로 잘못 옮겼고(연금저축 900은 99만 원), 숫자 대조는 값이 블록에 있어 통과시켰다
    pc = P.get('calc') or {}
    combo_only = (pc.get('blocks') and all(b['topic'].startswith('세액공제(조합') for b in pc['blocks'] if b.get('numbers'))
                  and any(b.get('numbers') for b in pc['blocks']) and not pc.get('needs'))
    if clarify_template and combo_only and P.get('action') == '계산 → 설명' and \
            re.search(r'비교|차이|어느\s*(?:쪽|게|것)|뭐가\s*(?:더|나)|어떤\s*게\s*(?:더|나)', raw.split('기준으로 질문:', 1)[-1]):
        calc = pc
        ans = orchestrator.clarify_answer(calc, 1)
        trace.append('응답: 조합 비교(계산 블록의 조합별 공제액 그대로, 모델 호출 없음)')
        ans = finalize(ans, cite_hits, trace)
        acts = orchestrator.next_actions('계산 → 설명', raw, calc, None, P.get('category', ''))
        sec = sections(ans, cite_hits, calc, acts)
        ans, sec = add_block_sources(ans, sec, calc)
        return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                'think_trace': '\n'.join(trace), 'answer': ans,
                'standalone': raw if mem and mem.get('how') == '대체' else question,
                'injection': bool(P.get('injection')), 'action': '계산 → 설명',
                'plan': orchestrator.plan_steps(trace, '계산 → 설명', P.get('why', ''), P.get('state', ''), calc),
                'sections': sec,
                'calc': {'rule_ids': calc.get('rids', []), 'figures': calc.get('figures', []),
                         'needs': [], 'assume': calc.get('assume', [])},
                'verify': None, 'needs': []}
    # 나눠 받기(질문한 방식) — 계산 블록 문장 그대로(시연 2차 장면 3턴). AGENT_CHAT_SPLIT_TEMPLATE=0 이면 예전처럼 모델이 설명
    if clarify_template and CHAT_SPLIT_TEMPLATE and pc.get('blocks') and not pc.get('needs') and \
            any('질문한 방식' in b['text'] for b in pc['blocks'] if b.get('numbers')):
        calc = pc
        ans = orchestrator.split_answer(calc, 1)
        trace.append('응답: 나눠 받기 비교(계산 블록의 질문한 방식 합계·비교 그대로, 모델 호출 없음)')
        ans = finalize(ans, cite_hits, trace)
        acts = orchestrator.next_actions('계산 → 설명', raw, calc, None, P.get('category', ''))
        sec = sections(ans, cite_hits, calc, acts)
        ans, sec = add_block_sources(ans, sec, calc)
        return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                'think_trace': '\n'.join(trace), 'answer': ans,
                'standalone': raw if mem and mem.get('how') == '대체' else question,
                'injection': bool(P.get('injection')), 'action': '계산 → 설명',
                'plan': orchestrator.plan_steps(trace, '계산 → 설명', P.get('why', ''), P.get('state', ''), calc),
                'sections': sec,
                'calc': {'rule_ids': calc.get('rids', []), 'figures': calc.get('figures', []),
                         'needs': [], 'assume': calc.get('assume', [])},
                'verify': None, 'needs': []}
    # 상품을 골라 달라는 말 — 막연한 말('추천할만한 연금', '뭘 넣어야')도(10-13 외부 시험). 상품 이름이 정해졌으면 아래 상품표·검색 길로
    if clarify_template and not P.get('calc') and orchestrator.is_product_ask(raw) and \
            not any(len(m['names']) == 1 for m in _product_names(question)):
        ans = orchestrator.product_clarify_answer()
        trace.append('응답: 상품 추천 되묻기(정해진 문장, 모델 호출 없음) — 상품 지목 대신 적합성 진단으로 연결')
        acts = orchestrator.next_actions('되묻기', raw, None, None, P.get('category', ''))
        return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                'think_trace': '\n'.join(trace), 'answer': ans,
                'standalone': raw if mem and mem.get('how') == '대체' else question,
                'injection': bool(P.get('injection')), 'action': '되묻기',
                'plan': orchestrator.plan_steps(trace, '되묻기', P.get('why', ''), P.get('state', '')),
                'sections': {'result': ans, 'evidence': [], 'assumptions': [], 'needs': [], 'next_actions': acts},
                'calc': None, 'verify': None, 'needs': []}
    pcalc = P.get('calc') or {}
    no_numbers = not any(b.get('numbers') for b in pcalc.get('blocks', []))
    if clarify_template and P.get('action') != '상담 연결' and no_numbers:
        # 줄여 부른 상품 이름이 받은 문서의 여러 상품에 걸린다 — "어느 상품인가요?"로 되묻는다(10-13 외부 시험, 모델 호출 없음).
        # 한 상품으로 정해지면 검색(retrieve)이 그 상품 조각을 맨 앞에 두고 아래 생성 경로로 간다
        amb = [m for m in _product_names(question) if len(m['names']) >= 2]
        if amb:
            ans = product_names.ask_answer(amb)
            trace.append('응답: 상품 이름 되묻기(여러 상품에 걸리는 이름, 모델 호출 없음) — '
                         + ' / '.join(f"{m['said']}: {', '.join(m['names'])}" for m in amb))
            acts = product_names.ask_actions(raw if any(m['said'] in raw for m in amb) else question, amb)
            acts.append({'label': '상담 연결', 'kind': 'handoff', 'payload': ''})
            return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                    'think_trace': '\n'.join(trace), 'answer': ans,
                    'standalone': raw if mem and mem.get('how') == '대체' else question,
                    'injection': bool(P.get('injection')), 'action': '되묻기',
                    'plan': orchestrator.plan_steps(trace, '되묻기', '상품 이름이 여러 상품에 걸림', P.get('state', '')),
                    'sections': {'result': ans, 'evidence': [], 'assumptions': [], 'needs': [], 'next_actions': acts},
                    'calc': None, 'verify': None, 'needs': []}
        # 상품표(doc37 연금 인출 가이드)에 있는 상품만 물었다 — 표의 행을 그대로 옮긴 비교표로 답한다(모델 호출 없음).
        # 10-13 외부 시험: 모델이 표를 옮기며 위험등급을 이웃 행 값으로 바꿔 쓰고 '안정성 중시 → ○○'처럼 골라 줬다
        uniq = [m for m in _product_names(question) if len(m['names']) == 1]
        try:
            trows = product_names.table_rows_in(question)
        except Exception:
            trows = []
        tfiles = {r['file'] for r in trows}
        k = next((i for i, (c, _) in enumerate(cite_hits or [], 1) if c['file'] in tfiles), None)
        tab = product_names.table_answer([], k, rows=trows) if trows and k and not orchestrator.is_projection_ask(raw) else None
        if tab:
            trace.append('응답: 상품표 값 그대로(받은 문서의 표 행을 옮김, 모델 호출 없음) — ' + ', '.join(r['name'] for r in trows))
            ans = finalize(tab, cite_hits, trace)
            acts = [{'label': '투자성향 진단 받기', 'kind': 'handoff', 'payload': '적합성 진단'},
                    {'label': '상담 연결', 'kind': 'handoff', 'payload': ''}]
            sec = sections(ans, cite_hits, None, acts)
            sec['assumptions'] = list(sec.get('assumptions') or []) + product_names.note(uniq)
            return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                    'think_trace': '\n'.join(trace), 'answer': ans,
                    'standalone': raw if mem and mem.get('how') == '대체' else question,
                    'injection': bool(P.get('injection')), 'action': '검색 → 설명',
                    'plan': orchestrator.plan_steps(trace, '검색 → 설명', '상품표 값 그대로', P.get('state', '')),
                    'sections': sec, 'calc': None, 'verify': None, 'needs': []}
        # 금액을 계산 조건으로 읽지 못한 짧은 세금 질문 — 모델이 숫자를 채우지 않게 먼저 되묻는다(10-14 외부 검토 P1:
        # '월 50,000원씩 연금저축에 넣으면'을 못 읽어 모델이 99만 원(맞는 값 9만 9천 원)으로 계산). 파서를 보강해도 처음 보는
        # 표현은 남는다 — '숫자는 규칙이'를 지키는 마지막 그물
        unread = orchestrator.unread_amounts(question, pcalc) or (orchestrator.unread_amounts(raw, pcalc) if raw != question else [])
        if unread and not orchestrator.is_projection_ask(raw):
            ans = orchestrator.unread_amount_answer(unread, raw)
            trace.append('응답: 금액을 계산 조건으로 읽지 못함 → 숫자를 짐작하지 않고 되묻기(모델 호출 없음) — '
                         + ', '.join(f'{v}({r})' for v, r in unread))
            acts = [{'label': 'DIY로 직접 넣어 보기', 'kind': 'diy', 'payload': ''},
                    {'label': '상담 연결', 'kind': 'handoff', 'payload': ''}]
            return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                    'think_trace': '\n'.join(trace), 'answer': ans,
                    'standalone': raw if mem and mem.get('how') == '대체' else question,
                    'injection': bool(P.get('injection')), 'action': '되묻기',
                    'plan': orchestrator.plan_steps(trace, '되묻기', '금액을 계산 조건으로 읽지 못함', P.get('state', '')),
                    'sections': {'result': ans, 'evidence': [], 'needs': [], 'next_actions': acts,
                                 'assumptions': ['숫자를 짐작해 계산하지 않았어요 — 조건을 알려 주시면 문서 규칙으로 계산합니다']},
                    'calc': None, 'verify': None, 'needs': []}
        # 앞으로 받을 연금액('65세에 얼마 받아요?', '평균으로 가정해서 계산해줘') — 수익률을 지어내지 않는다(10-13 외부 시험)
        if orchestrator.is_projection_ask(raw) or orchestrator.is_projection_ask(question):
            ans = orchestrator.projection_answer()
            trace.append('응답: 예상 수령액 질문 — 미래 수익률 규칙이 문서에 없어 금액을 만들지 않고 계산 가능한 질문·확인 경로로 안내(모델 호출 없음)')
            acts = orchestrator.projection_actions(question)
            return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                    'think_trace': '\n'.join(trace), 'answer': ans,
                    'standalone': raw if mem and mem.get('how') == '대체' else question,
                    'injection': bool(P.get('injection')), 'action': '규칙 → 설명',
                    'plan': orchestrator.plan_steps(trace, '규칙 → 설명', '예상 수령액은 문서 규칙 밖 — 계산 가능한 질문으로 안내', P.get('state', '')),
                    'sections': {'result': ans, 'evidence': [], 'needs': [], 'next_actions': acts,
                                 'assumptions': ['미래 수익률·납입액을 가정한 수령액은 계산하지 않았어요(받은 문서에 기준 없음)']},
                    'calc': None, 'verify': None, 'needs': []}
    if P.get('action') == '상담 연결':
        # 고객이 사람 상담을 원한다 — 생성 없이 상담 창구(번호·운영시간)를 안내한다. 이관 연동은 없어 '넘겨 드린다'고 하지 않는다
        prof = (mem or {}).get('profile') or P.get('state') or ''
        ans = orchestrator.handoff_reply(prof, ui=clarify_template)
        trace.append('응답: 상담 연결 안내(생성 없음)')
        return {'question_id': question_id, 'question': raw, 'retrieved_context': ctx,
                'think_trace': '\n'.join(trace), 'answer': ans,
                'standalone': raw if mem and mem.get('how') == '대체' else question,
                'injection': bool(P.get('injection')), 'action': '상담 연결',
                'plan': orchestrator.plan_steps(trace, '상담 연결', P.get('why', ''), P.get('state', '')),
                'sections': {'result': ans, 'evidence': [], 'assumptions': [], 'needs': [],
                             'next_actions': [{'label': '상담 연결 요청됨', 'kind': 'handoff', 'payload': ''}]},
                'calc': None, 'verify': None}

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
    # 계산 질문은 숫자를 규칙이 낸다 — 초안 여러 개를 맞춰 볼 이유가 적어 초안 1개(생각 high)로 바로 답한다.
    # 100문항 실험(g1)에서 초안 1개 high 는 지금 방식과 같은 수준(−0.7pp ±5.0)에 토큰 1/3, 체감 −40%였다.
    if CALC_FAST and samples is None and P.get('action') == '계산 → 설명' and DEFAULT_SAMPLES > 1:
        samples = 1
        draft_effort = draft_effort or 'high'
        maxtok = max(maxtok, 4096)
        trace.append('라우터: 숫자는 규칙 계산이 냈으므로 초안 1개로 설명만 생성')
    # calc_effort: 숫자를 규칙 계산이 낸 답(계산 → 설명·계산 + 되묻기)의 생각 강도. 시연 채널(/chat)이 정한다.
    # 10-13 실측(시연 장면 4개): 시간은 출력 토큰(생각 포함)에 비례해 초당 약 60토큰 — 생각을 끄면 34.8→10.1초,
    # 24.2→7.0초, 29.5→14.7초, 36.7→11.7초. 비우면 예전 그대로(대회 규격 /answer 는 넘기지 않는다).
    if calc_effort and P.get('action') in ('계산 → 설명', '계산 + 되묻기') and \
            any(b.get('numbers') for b in (P.get('calc') or {}).get('blocks', [])):
        draft_effort = calc_effort
        trace.append(f'라우터: 숫자는 규칙 계산이 냈으므로 설명만 생성(생각 {calc_effort})')
    elif calc_effort and P.get('cards') and CHAT_CARD_EFFORT:
        # 핵심 사실 카드가 근거 맨 앞에 있다 — 사실은 카드가 주므로 생각을 줄여 빨리 답한다(시연 채널)
        draft_effort = CHAT_CARD_EFFORT
        trace.append(f'라우터: 핵심 사실 카드로 설명 생성(생각 {CHAT_CARD_EFFORT})')
    elif clarify_template and LOW_GUARD and draft_effort in ('low', 'none') and P.get('injection'):
        draft_effort = 'high'
        trace.append('라우터: 지시문 의심 질문이라 생각 high 로 답함')
    if _progress_on():
        # 여기까지가 준비(가드·검색·규칙 계산·다음 행동) — 생성 전에 단계와 규칙 계산값을 먼저 알린다
        n_now = int(samples if samples is not None else DEFAULT_SAMPLES)
        eff = (draft_effort or DRAFT_EFFORT) if thinking else '없음'
        c = P.get('calc')
        _progress(f'AI가 설명을 쓰는 중({clova.CHAT_MODEL} · 생각 {eff}' + (f' · 초안 {n_now}개' if n_now > 1 else '') + ')',
                  steps=orchestrator.plan_steps(trace, P.get('action', ''), P.get('why', ''), P.get('state', ''), c),
                  calc={'rule_ids': c.get('rids', []), 'figures': c.get('figures', []),
                        'needs': c.get('needs', []), 'assume': c.get('assume', [])} if c else {})

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
            if len(sanitize_answer(ans or '').strip()) < 50:
                # 생각 high 가 출력 상한(4096)을 생각에 다 쓰면 본문이 비어 나온다(A1 재측정 D-07 1회).
                # 초안이 하나뿐이라 대신할 초안이 없으므로 생각을 낮춰 한 번 더 받는다.
                t1 = time.time()
                ans, th2 = generate(msgs, maxtok=4096, effort='low', thinking=thinking)
                reasoning = th2 or reasoning
                trace.append(f'답이 비어 생각을 낮춰 다시 생성 ({time.time() - t1:.1f}s)')
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
            if a1_on and len(drafts) > 1:
                # 분할 과세 오독 초안 — 맞는 초안이 2개 이상 남으면 빼고, 아니면 고쳐서 병합에 넣는다(병합이 사라지지 않게)
                drafts, note = screen_drafts(drafts, P.get('calc'), _cite_for_tax(cite_hits))
                if note:
                    trace.append(note)
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
                _progress(f'초안 {len(drafts)}개 중 다른 초안과 가장 많이 겹치는 초안을 기준으로 합치는 중(초안 합의 병합)')
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
    if card_fallback and len((ans or '').strip()) < 50:
        # 카드 모드에서 생성이 실패했다 — 원문으로 쓴 정해진 문장으로 대신한다
        trace.append('생성 실패 → 핵심 사실 카드의 정해진 문장으로 대신')
        ans = card_fallback['answer']

    # 남은 분할 과세 오독은 문서 규칙대로 고친 문장으로 바꾼다(초안이 하나뿐이거나 모두 같은 오독일 때).
    # 각주 렌더링 전에 해야 [문서N] 이 실제 문서로 풀린다.
    fixed, nfix = taxcalc.repair(ans, P.get('calc'), _cite_for_tax(cite_hits)) if a1_on else (ans, 0)
    if nfix:
        ans = fixed
        trace.append(f'세금 계산 검증: 분할 과세 서술 {nfix}곳을 문서 규칙대로 고침')
    # 문서 근거 원칙 — 문서에 없다고 밝힌 답은 '답할 수 없음'으로, 일부만 있으면 일반론 문장을 뺀다(계산 블록이 없을 때만)
    calc = P.get('calc')
    if not calc:
        ans, how_g = enforce_grounding(ans)
        if how_g:
            trace.append(f'문서 근거: {how_g}' + (' — 문서에 없는 질문이라 상담 연결로 안내' if how_g == '답할 수 없음' else ''))
    # 답 속 숫자 대조 — 질문·앞 대화·규칙 계산·근거 문서에 있는 값(또는 그 곱·합)인지 하나씩 확인한다
    if not (ans or '').strip() and calc and any(b.get('numbers') for b in calc['blocks']):
        # 생성이 끝내 비었다 — 일반 안내문 대신 규칙 계산값과 상담 연결을 내보낸다
        ans = verify.handoff_answer(calc, _cite_for_tax(cite_hits) or 1)
        trace.append('답이 비어 규칙 계산값과 상담 연결 안내로 대체')
    vr = None
    _progress('답 속 숫자를 규칙 계산·근거와 대조하는 중')
    if verify.MODE != 'off':
        extra = [mem['history'], mem.get('profile') or ''] if mem else []
        # 시연 채널(/chat)은 결론 자리에 규칙 계산과 다른 세금 금액이 있으면 덧붙임 대신 상담 연결로 넘긴다(10-12 시연 점검:
        # 3턴 결론에 '한꺼번에 받으면 132만 6천 원'을 쓰고 바른 값 57만 6천 원은 맨 끝 '계산 확인'에만 있었다).
        # 대회 규격(/answer)은 측정 전이라 그대로 둔다.
        vr = verify.check(ans, question + '\n' + raw, calc if a1_on else None, ctx, extra, lead_strict=clarify_template)
        trace.append(verify.summary_line(vr))
        if vr['contradiction'] and verify.MODE == 'handoff':
            ans = verify.handoff_answer(calc, _cite_for_tax(cite_hits) or 1)
            trace.append('라우터: 다음 행동 = 상담 연결 (답 속 금액이 규칙 계산과 어긋나 계산값만 내보냄)')
        elif vr['contradiction'] and verify.MODE == 'note' or vr.get('conflict'):
            ans = verify.note_answer(ans, calc, 1)
            trace.append('숫자 대조: 답 끝에 규칙 계산값을 덧붙임(계산에 없는 세금 금액이 있어서)')
    ans, nold = verify.fix_outdated(ans)
    if nold:
        trace.append(f'숫자 대조: 옛 제도 값 {nold}곳을 현행 값으로 고침(연금저축 세액공제 한도 400만 → 600만 등)')
    ans, nlim = verify.fix_limit_terms(ans)
    if nlim:
        trace.append(f"숫자 대조: '납입한도 900만 원' {nlim}곳을 '세액공제 한도'로 고침(입금은 합산 1,800만 원까지, 공제는 900만 원까지)")
    ans, nbasis = verify.fix_income_basis(ans)
    if nbasis:
        trace.append(f'숫자 대조: 공제율 기준을 섞어 쓴 {nbasis}곳을 고침(총급여는 5,500만 원, 종합소득금액은 4,500만 원 기준)')
    ans, n1500 = verify.fix_over1500(ans, calc)      # 규칙 계산의 연금수령한도로 한도 규칙 초과분을 가른다(3차 검토 231~239)
    if n1500:
        trace.append("숫자 대조: '1,500만 원 초과분만 따로 과세'로 읽히는 답에 바로잡는 줄을 붙임(넘으면 전액이 대상, doc38)")
    if clarify_template and calc:
        ans, nask = ensure_asked(ans, calc)
        if nask:
            trace.append('숫자 대조: 질문이 물은 금액(늘어나는 공제액)이 답에 없어 첫 줄에 넣음')
    ans, nband = verify.fix_rate_bands(ans)
    if nband:
        trace.append(f'숫자 대조: 나이 구간별 연금소득세율 나열 {nband}곳이 세율표(R-07)와 달라 표대로 고침')
    ans, nage = verify.fix_age_rate(ans)
    if nage:
        trace.append(f'숫자 대조: 나이와 맞지 않는 연금소득세율 {nage}곳을 나이별 세율표(R-07)대로 고침(예: 65세 4.4% → 5.5%)')
    if clarify_template and not calc:
        ans, npair = drop_unpaired_product_figures(ans, cite_hits)
        if npair:
            trace.append(f'숫자 대조: 상품 이름과 수치가 같은 근거에 없는 줄 {npair}곳을 뺌(상품·수치 짝 지어내기 방지)')
        ans, npick = drop_product_picks(ans)
        if npick:
            trace.append(f'상품 권유: 특정 상품을 고르는 줄 {npick}곳을 뺌(상품 지목은 투자성향 진단·상담으로)')
    ans, nyr = verify.strip_unsupported_years(ans, '\n'.join([question, raw, ctx] + ([mem['history']] if mem else [])))
    if nyr:
        trace.append(f'숫자 대조: 근거에 없는 연도 기준 {nyr}곳을 뺌(예: "2023년 기준")')
    # 시연 채널(/chat)은 모든 답에서 — 10-13 외부 시험에서 상품 비교·초보 질문 답에 '추천 전략'·'추천 포인트' 소제목이 나왔다
    if (calc and calc.get('compare')) or clarify_template:
        ans, nrec = neutral_headings(ans)
        if nrec:
            trace.append(f'비교 질문: 소제목의 추천·권장 표현 {nrec}곳을 "고르는 기준"으로 바꿈')
    ans = finalize(ans, cite_hits, trace)
    reasoning_line(trace, reasoning, P.get('injection'))
    action = '상담 연결' if vr and vr['contradiction'] and verify.MODE == 'handoff' else P.get('action', '')
    acts = orchestrator.next_actions(action, raw, calc, vr, P.get('category', ''))
    sec = sections(ans, cite_hits, calc, acts)
    if clarify_template:
        # 줄여 부른 상품 이름을 어느 상품으로 봤는지 '가정' 칸에 밝힌다('그 상품 말씀이시죠?')
        pn = product_names.note(_product_names(question))
        if pn:
            sec['assumptions'] = list(sec.get('assumptions') or []) + pn
    return {'question_id': question_id, 'question': raw,
            'retrieved_context': ctx, 'think_trace': '\n'.join(trace),
            'answer': ans,
            # 재작성이 대체(앞 질문 덧붙이기)로 끝났으면 기억에는 원문을 남긴다 — 덧붙인 글이 다음 턴에 겹겹이 쌓이지 않게.
            # 단 정보 보충(되묻기에 답한 턴)은 '앞 질문 + 알려 준 값'을 남긴다 — 원문만 남기면 인출액이 빠진다(시연 3턴째)
            'standalone': (mem.get('calc_q') or raw) if mem and mem.get('how') == '대체' else question,
            'injection': bool(P.get('injection')),
            # 아래는 시연 화면(/chat)용 — 서버의 /answer 는 규격 5필드만 내보낸다
            'action': action,
            'plan': orchestrator.plan_steps(trace, action, P.get('why', ''), P.get('state', ''), calc, vr),
            'sections': sec,
            'calc': None if not calc else {'rule_ids': calc.get('rids', []), 'figures': calc.get('figures', []),
                                           'needs': calc.get('needs', []), 'assume': calc.get('assume', [])},
            'verify': vr, 'needs': (calc or {}).get('needs', [])}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    q = sys.argv[1] if len(sys.argv) > 1 else '연금저축이랑 IRP에 넣으면 세액공제 얼마까지 되나요? 다 합쳐서요.'
    r = answer(q, 'Q-TEST')
    print('=== think_trace ===\n' + r['think_trace'][:600])
    print('\n=== answer ===\n' + r['answer'])
    print(f"\n(근거 {len(r['retrieved_context']):,}자)")
