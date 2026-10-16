# -*- coding: utf-8 -*-
"""
평가용 API 서버.

주최측 규격 (API명세.md):
  GET /answer?question_id={id}&question={질의}
  → JSON {question_id, question, retrieved_context, think_trace, answer}  전부 문자열

대화 기억(본선, O5): POST /session 으로 세션 ID 를 받아 &session_id={ID} 를 붙이면 같은 세션의 앞 질문·답변과
고객이 말한 조건을 기억해 이어지는 질문("그럼 70살이면요?")을 풀어 답한다. 응답 형식은 같다. session_id 가 없거나
형식이 맞지 않으면 예전과 똑같이 질문 하나만 보고 답한다(대회 평가 경로는 그대로).
  POST   /session                서버가 만든 추측 불가능한 세션 ID 발급(클라이언트가 고른 ID 로 남의 대화를 읽지 못하게)
  GET    /session/{session_id}   기억 내용 보기 — AGENT_SESSION_DEBUG=1 일 때만(시연 점검용)
시연 화면(본선): GET /demo, POST /chat(응답 4칸·오케스트레이터 단계·숫자 대조), /alerts, /diy, /rules, /ontology — 파일 끝 참고
  DELETE /session/{session_id}   기억 지우기 — 같은 조건
막힌 질문(가드 차단)은 기억에 넣지 않는다. 지시문이 의심되는 질문은 내용 없이 자리만 남긴다.
  타임아웃 300초 · 팀당 순차 1건씩 · 5xx/타임아웃 시 최대 2회 재시도

설계상 지킨 것
  - 기동 시 색인·임베딩을 미리 올린다 (첫 질의가 느려지면 타임아웃 위험)
  - 어떤 예외가 나도 200 + 스키마를 지킨 JSON 을 돌려준다.
    5xx 를 내면 주최측이 재시도하는데, 재시도가 또 실패하면 그 문항은 0점이다.
    빈 답이라도 형식을 지켜 돌려주는 편이 낫다.
  - 질의당 호출은 임베딩 1 + 초안 최대 3 + 병합 0~1 이다. 부하가 높으면 초안 수를 줄인다
    (_samples_for). CLOVA 한도(테스트 앱 키: HCX-007 분당 60회·6만 토큰)를 넘지 않는 게 관건이다.
  - 동시 요청은 받아 두고 초안 수로 조절한다(_CONC). 세마포어로 1건씩 받던 때는
    배치 대기 때문에 오히려 시간 초과가 났다.
"""
import os
import re
import sys
import json
import time
import asyncio
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

import clova
import hashlib
import secrets
from memory import STORE

MAX_Q = int(os.environ.get("AGENT_MAX_QUESTION", "2000"))   # 질문 길이 상한(긴 입력으로 서버를 붙잡는 일 방지)
SESSION_DEBUG = os.environ.get("AGENT_SESSION_DEBUG") == "1"
_SID = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
from answer import (answer, observed_call_seconds, get_index, get_hybrid,
                    DRAFT_EFFORT, MERGE_EFFORT, SKIP_MERGE_AT, AGENT_MODE, DEFAULT_SAMPLES)

APP_START = time.time()
# 부하 조절.
#
# 실측 — 페이로드 크기에 따라 API 거동이 갈린다.
#   짧은 호출(400토큰) : 동시 1→8 에서 8.6 → 44.3회/분 (거의 선형, 병렬화됨)
#   실제 호출(3만 자 + 추론 4096토큰) : 동시 1·3·6·12 에서 1.4/1.6/1.5/1.4회/분
#                                      속도이득 1.0배 — **직렬화된다**
#
# 즉 동시성을 올려도 총 처리량이 늘지 않는다. 12건을 한꺼번에 받으면 전부
# 513초에 끝나 **모두 실패**하고, 3건씩 받으면 앞의 6건은 300초 안에 산다.
# 백엔드가 직렬화할 때는 입장을 제한하는 편이 낫다.
#
# 그래서 **진입 시점의 처리 중 건수**를 보고 초안 수를 즉시 정한다.
# 큐에서 기다린 뒤 줄이면 이미 쓴 시간을 못 돌려받는다 — 들어올 때 정해야 한다.
# 한산하면 초안 3개(품질), 붐비면 1개(속도). 느린 좋은 답보다 제때 온 보통 답이 낫다.
_CONC = int(os.environ.get("AGENT_CONCURRENCY", "16"))
_sem = asyncio.Semaphore(_CONC)
_inflight = 0
_inflight_lock = asyncio.Lock()


# 단독 실측 13.6초. 다만 여러 요청이 겹치면 직렬화로 체감이 커진다
# (동시 12건 시험에서 4호출짜리 요청이 129초 = 호출당 32초로 관측).
# 예산은 보수적으로 잡아야 초과가 안 난다.
PER_CALL_S = float(os.environ.get("AGENT_PER_CALL_S", "20"))
DEADLINE_S = float(os.environ.get("AGENT_DEADLINE_S", "300"))
MARGIN_S = float(os.environ.get("AGENT_MARGIN_S", "60"))


def _samples_for(load, elapsed=0.0):
    """초안 수를 남은 시간 예산으로 정한다.

    실측 — 이 크기의 호출은 API 가 직렬 처리한다(동시 1·3·6·12 에서 처리량
    1.4~1.6회/분으로 평평, 속도이득 1.0배). 그래서 어떤 요청이 자기 호출 k 개를
    끝내는 데 드는 벽시계 시간은  k x PER_CALL x (동시에 도는 요청 수) 다.

    남은 예산 안에 들려면
        k x PER_CALL x min(load, _CONC)  <=  DEADLINE - elapsed - MARGIN
    이고, 초안 수는 k - 1 이다(마지막 1회가 병합).

    두 신호를 다 봐야 한다는 걸 부하 시험에서 배웠다.
      · 부하만 보면 : 큐에서 200초 기다린 요청이 '지금 한산하다'며 초안 3개를
                     쓰고 315초에 끝났다 (3건 실패)
      · 경과만 보면 : 동시에 도착한 12건이 서로를 못 보고 다 3개를 쓴다 (4건 실패)
    """
    # 대기 전부를 센다. min(load, _CONC) 을 쓰다가 부하 시험에서 7건을 잃었다 —
    # 세마포어로 배치를 나누면 2배치는 1배치가 **완전히 끝나야** 시작해서
    # 12건이 190초 + 120초 = 310초가 됐다. 호출이 43초일 땐 입장 제한이 이득이었지만
    # 15초로 싸진 지금은 배치 지연이 더 크다. 전부 받고 초안 수로 조절한다.
    busy = max(1, int(load))
    left = DEADLINE_S - elapsed - MARGIN_S
    # 호출당 시간은 서버가 실제로 재둔 값을 쓴다. 상수로 박으면 환경마다 틀린다 —
    # 로컬 13~16초로 보고 20 을 잡았는데 배포 서버(2vCPU)에서는 약 37초였다.
    per = observed_call_seconds(PER_CALL_S)
    k = int(left / (per * busy)) if left > 0 else 1
    n = max(1, min(3, k - 1))
    return None if n >= 3 else n

app = FastAPI(title="연금 Agent", version="1.0")


@app.on_event("startup")
def warmup():
    """색인·임베딩을 미리 올린다. 첫 요청에서 40MB 로딩이 걸리면 위험하다."""
    t = time.time()
    idx, chunks = get_index()
    H = get_hybrid()
    print(f"[startup] 조각 {len(chunks):,} · 임베딩 "
          f"{'있음 ' + str(H.emb.shape) if H is not None else '없음(BM25 단독)'} "
          f"· {time.time()-t:.1f}s", flush=True)
    print(f"[startup] 모델 {clova.CHAT_MODEL} · 키 {'설정됨' if clova.API_KEY else '없음'}",
          flush=True)


@app.get("/health")
def health():
    H = get_hybrid()
    _, chunks = get_index()
    # 배포 후 설정이 의도대로 실렸는지 눈으로 확인할 수 있게 노출한다
    return {
        "concurrency": _CONC,
        "mode": AGENT_MODE,
        "samples": DEFAULT_SAMPLES,
        "status": "ok",
        "uptime_sec": round(time.time() - APP_START, 1),
        "chunks": len(chunks),
        "embeddings": None if H is None else list(H.emb.shape),
        "observed_call_sec": round(observed_call_seconds(PER_CALL_S), 1),
        "chat_model": clova.CHAT_MODEL,
        "api_key": bool(clova.API_KEY),
        "draft_effort": DRAFT_EFFORT,
        "merge_effort": MERGE_EFFORT,
        "skip_merge_at": SKIP_MERGE_AT,
        "policy_excluded_chunks": sum(1 for c in chunks if c.get("policy") == "exclude"),
        # 배포 판(deploy/deploy.sh 가 커밋·시각으로 붙임)과 시연 채널 설정 — 어떤 코드가 올라갔는지 밖에서 확인
        "version": os.environ.get("AGENT_VERSION", ""),
        "chat_deadline": CHAT_DEADLINE,
        "chat_calc_effort": CHAT_CALC_EFFORT,
        "chat_fast_effort": CHAT_FAST_EFFORT,
        "chat_precise_effort": CHAT_PRECISE_EFFORT,
        "chat_samples": CHAT_SAMPLES,
    }


# ---------------------------------------------------------------- 운영 로그
# 요청마다 한 줄씩 JSONL 로 남긴다. 실패 질의를 모아 규칙·문서를 고치는 재료다
# (운영 사이클: 로그 → 실패 유형 분석 → 규칙·테스트 보강 → 재측정).
# 예선 서버는 print 만 해서 어떤 질문에서 무엇이 틀렸는지 남은 게 없었다.
# 개인정보로 보이는 숫자(주민번호·전화번호·계좌번호 꼴)는 가린다.
# 로그 쓰기가 실패해도 응답은 나가야 하므로 예외는 삼킨다.
LOG_DIR = os.environ.get("AGENT_LOG_DIR",
                         os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"))
_log_lock = threading.Lock()
_MASKS = [
    (re.compile(r"\d{6}\s*-\s*[1-4]\d{6}"), "[주민번호]"),
    (re.compile(r"01[016-9]\s*-?\s*\d{3,4}\s*-?\s*\d{4}"), "[전화번호]"),
    (re.compile(r"\d{2,6}(?:-\d{2,8}){2,}"), "[계좌번호]"),
    (re.compile(r"\d{10,}"), "[긴 숫자]"),
]


_PII_OUT = re.compile(r"\d{6}\s*-\s*[1-4]\d{6}|01[016-9]\s*-\s*\d{3,4}\s*-\s*\d{4}")


_DATE = re.compile(r"(?:19|20)\d{2}-\d{1,2}-\d{1,2}")


def mask(t):
    # 계좌번호 꼴(숫자-숫자-숫자)에 날짜('2025-09-01', 근거의 기준일)가 걸려 '[계좌번호]'로 가려졌다(10-13 질문 기록) — 날짜는 그대로
    for pat, rep_ in _MASKS:
        t = pat.sub(lambda m, r=rep_: m.group(0) if r == "[계좌번호]" and _DATE.fullmatch(m.group(0)) else r, t or "")
    return t


def _trace_field(trace, head):
    for line in (trace or "").split("\n"):
        if line.startswith(head):
            return line[len(head):].strip()
    return ""


def log_query(question_id, question, out, elapsed, load, samples, error=None, session_id="", standalone=""):
    try:
        trace = out.get("think_trace", "")
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "question_id": question_id,
            # 세션 아이디는 그대로 남기지 않는다(해시 앞 8자리만 — 같은 대화끼리 묶어 보기용)
            "session": hashlib.sha256(session_id.encode()).hexdigest()[:8] if session_id else "",
            "question": mask(question),
            "standalone": mask(standalone) if standalone and standalone != question else "",
            "guard": _trace_field(trace, "가드:"),
            "type": _trace_field(trace, "유형:"),
            "top": next((l.split("상위:", 1)[1].strip() for l in trace.split("\n") if "상위:" in l), ""),
            "policy_filter": _trace_field(trace, "대상 필터:"),
            "drafts": samples or DEFAULT_SAMPLES,
            "load": load,
            "elapsed_s": round(elapsed, 1),
            "answer_chars": len(out.get("answer", "")),
            "answer": mask(out.get("answer", "")),
            "error": error,
        }
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f"queries-{time.strftime('%Y%m%d')}.jsonl")
        with _log_lock, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:                       # 로그 때문에 응답이 실패하면 안 된다
        print(f"[log] 기록 실패: {e}", flush=True)


SCHEMA_FIELDS = ("question_id", "question", "retrieved_context", "think_trace", "answer")


def _to_schema(r, question_id, question):
    """규격 5필드만 골라 문자열로 내보낸다.

    answer() 가 진단용 키를 달고 오더라도 제출 응답에는 섞이면 안 된다.
    전에는 answer() 반환값을 그대로 JSONResponse 에 넘겨서, 내부 키를 하나만 추가해도
    규격을 벗어날 수 있는 구조였다. 여기서 한 번 걸러 그 가능성을 없앤다.
    모든 값은 문자열이어야 하므로 형변환도 여기서 끝낸다.
    """
    r = r or {}
    out = {k: r.get(k, "") for k in SCHEMA_FIELDS}
    for k in ("retrieved_context", "answer"):
        # 근거 원문(doc9 화면 캡처)에 휴대폰번호 꼴 번호가 있다 — 내보낼 때 가린다. 날짜(2026-10-08)는 건드리지 않게
        # 주민번호·휴대폰번호 꼴만 가린다(로그용 _MASKS 의 '계좌번호' 규칙은 날짜도 먹는다)
        out[k] = _PII_OUT.sub("[가림]", str(out[k] or ""))
    out["question_id"] = str(out["question_id"] or question_id or "")
    out["question"] = str(out["question"] or question or "")
    for k in ("retrieved_context", "think_trace", "answer"):
        out[k] = "" if out[k] is None else str(out[k])
    if not out["answer"].strip():
        # 빈 답변은 200 을 받고 0점이 확정되며 원인도 안 남는다.
        # answer() 안에도 같은 보루가 있지만, 나가는 문턱에서 한 번 더 막는다.
        out["answer"] = ("죄송합니다. 지금은 이 질문에 대한 답변을 준비하지 못했습니다. "
                         "연금 상담은 미래에셋증권 영업점 또는 고객센터로 문의해 주시기 바랍니다.")
        out["think_trace"] = (out["think_trace"] + chr(10) + "경고: 빈 답변이 감지되어 안내문으로 대체").strip()
    return out


@app.post("/session")
def session_new():
    return {"session_id": secrets.token_urlsafe(24)}


@app.get("/session/{session_id}")
def session_view(session_id: str):
    if not SESSION_DEBUG:
        return JSONResponse({"error": "disabled"}, status_code=404)
    return STORE.view(session_id)


@app.delete("/session/{session_id}")
def session_reset(session_id: str):
    if not SESSION_DEBUG:
        return JSONResponse({"error": "disabled"}, status_code=404)
    return {"reset": STORE.reset(session_id)}


@app.get("/answer")
async def answer_endpoint(
    question_id: str = Query("", description="질의 ID"),
    question: str = Query("", description="평가 질의"),
    session_id: str = Query("", description="대화 기억용 세션 ID(선택). 없으면 질문 하나만 보고 답한다"),
):
    t0 = time.time()
    if len(question) > MAX_Q:
        return JSONResponse({
            "question_id": question_id, "question": question[:MAX_Q],
            "retrieved_context": "", "think_trace": f"질문이 너무 깁니다({len(question)}자)",
            "answer": f"질문이 너무 깁니다. {MAX_Q}자 이내로 나눠서 질문해 주시기 바랍니다.",
        })
    if not question.strip():
        return JSONResponse({
            "question_id": question_id, "question": question,
            "retrieved_context": "", "think_trace": "빈 질의",
            "answer": "질문을 입력해 주십시오.",
        })

    global _inflight
    async with _inflight_lock:
        _inflight += 1
        load = _inflight
    # 몰려 들어온 요청이 서로를 볼 수 있게 아주 잠깐 기다린다.
    #
    # 세마포어를 풀었더니 12건이 같은 순간에 들어와, 먼저 도착한 4건이
    # 카운터가 오르기 전에 '한산하다'고 읽고 초안 3개를 썼다. 그 16호출이
    # 예산을 먹어 뒤의 3건이 300초를 넘겼다.
    # 0.4초는 전체 예산(300초)에서 무시할 수 있고, 동시 도착을 다 세게 해준다.
    await asyncio.sleep(0.4)
    try:
      async with _sem:
        # 초안 수는 **일을 시작하는 순간** 정한다.
        #
        # 도착 시점에 정했다가 부하 시험에서 4건을 잃었다 — 12건이 거의 동시에
        # 들어오면 먼저 도착한 몇 건은 카운터가 아직 낮아 '한산하다'고 보고
        # 초안 3개를 쓴다. 그들이 예산을 다 쓰고 뒤가 굶는다.
        # 세마포어를 잡은 뒤에는 나머지가 모두 도착해 있으므로 판단이 정확하다.
        load = _inflight
        n = _samples_for(load, time.time() - t0)
        if n is not None and n >= DEFAULT_SAMPLES:
            n = None              # 프리셋보다 늘리지는 않는다(fast 는 초안 1개)
        try:
            # answer() 는 동기 함수 — 이벤트 루프를 막지 않도록 스레드로 넘긴다
            sid = session_id.strip() if _SID.match(session_id.strip() or "-") else ""
            history = STORE.history(sid) if sid else None
            r = await asyncio.to_thread(answer, question, question_id, samples=n, history=history)
            note = f" (동시 {load}건, 초안 {n}개로 축소)" if n else f" (동시 {load}건)"
            r["think_trace"] = f"{r['think_trace']}\n소요 {time.time()-t0:.1f}s{note}"
            out = _to_schema(r, question_id, question)
            if sid and not r.get("blocked"):
                # 기억 저장은 정규식 파싱이 있어 이벤트 루프 밖에서 한다
                await asyncio.to_thread(STORE.add, sid, question, r.get("standalone") or question, out["answer"],
                                        bool(r.get("injection")), r.get("needs"))
            log_query(question_id, question, out, time.time() - t0, load, n,
                      session_id=sid, standalone=r.get("standalone", ""))
            return JSONResponse(out)
        except Exception as e:
            # 5xx 를 내면 주최측이 재시도한다. 재시도도 실패하면 0점이므로
            # 형식을 지킨 200 을 돌려주는 편이 낫다.
            tb = traceback.format_exc(limit=3)
            print(f"[error] {question_id}: {e}\n{tb}", flush=True)
            log_query(question_id, question, {}, time.time() - t0, load, n,
                      error=f"{type(e).__name__}: {e}")
            return JSONResponse({
                "question_id": question_id,
                "question": question,
                "retrieved_context": "",
                "think_trace": f"처리 중 오류: {type(e).__name__}: {e}",
                "answer": "죄송합니다. 일시적인 오류로 답변을 생성하지 못했습니다. "
                          "다시 질문해 주시면 도와드리겠습니다.",
            })
    finally:
        async with _inflight_lock:
            _inflight -= 1


# ---------------------------------------------------------------- 시연 화면(본선)
# 대회 규격(/answer 5필드)은 그대로 두고, 시연 화면은 아래 경로를 쓴다.
#   GET  /demo                  시연 화면(src/static/demo.html)
#   POST /chat                  상담 — 답 + 응답 4칸(결과·근거·가정·다음 행동) + 오케스트레이터 단계 + 숫자 대조
#   GET  /alerts/profiles       가상 고객 목록        POST /alerts   알림 점검 + 리포트(push 함에 쌓음)
#   GET  /alerts/outbox         push 함에서 꺼내기    POST /diy      DIY 연금 설계 계산
#   GET  /rules                 규칙표 R-01~R-15      GET  /ontology 온톨로지 그래프
# 새 모듈(alerts·diy·ontology·doc_meta)은 경로 안에서 불러온다 — 하나가 없어도 서버와 /answer 는 뜬다.
from fastapi import Body
from fastapi.responses import FileResponse, HTMLResponse

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/demo")
def demo_page():
    path = os.path.join(STATIC, "demo.html")
    if not os.path.exists(path):
        return HTMLResponse("<p>demo.html 이 없습니다</p>", status_code=404)
    return FileResponse(path, media_type="text/html; charset=utf-8")


# 시연 채널 마감 시간 — API 가 멈추면 한 호출에 최대 240초씩, 6번까지 다시 시도한다(10-10 실측: 첫 질문 397.8초).
# 90초: 10-12 실측에서 가장 긴 정상 답이 65.6초(주 장면, 알림 문구)였다 — 70초면 정상 답까지 끊길 수 있다.
# 라이브 시연에서 몇 분씩 멈추지 않게, 이 시간이 지나면 모델 없이 만들 수 있는 답(규칙 계산값·되묻기)을 먼저 내보낸다.
# 대회 규격(/answer)에는 걸지 않는다.
CHAT_DEADLINE = float(os.environ.get("AGENT_CHAT_DEADLINE", "90"))
# 빠른 모드에서 숫자를 규칙 계산이 낸 답(계산 → 설명·계산 + 되묻기)의 생각 강도. 빈 값이면 다른 답과 같이 high.
# 10-13 실측: 시간은 출력 토큰(생각 포함)에 비례한다 — 시연 장면 4개 합계 high 125초 → low 84초 → none 44초.
# 계산 9문항 × 3회(eval/_gen_effc_r1~3): 체크리스트 high 87.4% · low 89.9% · none 87.4%(생각을 끈 7문항만 −2.4pp,
# 95% −10.3~+4.9), 계산 답 생성 평균 25.1 → 16.2 → 7.9초, 할루시네이션 0. none 답은 짧고 곁 설명을 덜 쓴다.
CHAT_CALC_EFFORT = os.environ.get("AGENT_CHAT_CALC_EFFORT", "none").strip()
# 시연 채널 모드별 생각 강도(10-15 사용자 결정: 빠른 모드 low · 정밀 모드 high, 둘 다 초안 1개).
# 같은 123문항 짝비교(답 가림·문서 대조·반박 검증 4회): low 는 API 중앙 20.8 → 12.8초, 틀린 문장은 같거나 적고
# 빠진 핵심이 문항당 +0.15~0.30 — 꼼꼼함이 필요하면 정밀 모드. 정밀 모드는 예전 '초안 3개(low) + 병합'(체감 약 50초) 대신 high 1개.
# 10-16: 시연도 초안 3개 + 병합이 되며 빠른 모드 초안 low → none(사용자 결정). 넓은 질문('연금 질문이 뭐가 있죠')이
# 초안 3개 low 17.1초 + 병합 11.0초 = 28.2초였다. 100문항: 초안3(none)+병합 80.2% vs 초안3(low)+병합 83.0%(−2.8pp ±5.4, 0 포함).
# 지시문 의심 질문은 그대로 high(answer.LOW_GUARD).
CHAT_FAST_EFFORT = os.environ.get("AGENT_CHAT_FAST_EFFORT", "none").strip() or "high"
CHAT_PRECISE_EFFORT = os.environ.get("AGENT_CHAT_PRECISE_EFFORT", "high").strip() or "high"
# 시연 채널 초안 수(10-16 사용자 결정: 시연에서도 초안 합의 병합 — 빠른·정밀 모드 모두, 계산 답까지 초안 3개 + 병합).
# 대회 규격(/answer)은 계산 답을 초안 1개로 줄이지만(answer.CALC_FAST) 시연은 생성하는 답 모두 병합한다. 1 이면 예전(초안 1개).
# 출력 상한: 빠른 모드는 초안이 여럿이면 대회 규격과 같은 DRAFT_MAXTOK(1,600). 토큰 차감 = 입력 + 상한이라 4096 × 3 이면
# 이어지는 장면이 분당 6만 토큰 한도 대기(clova._throttle, 최대 70초)에 걸리기 쉽다. 정밀 모드(생각 high)는 생각이 길어 4096 그대로.
CHAT_SAMPLES = max(1, int(os.environ.get("AGENT_CHAT_SAMPLES", "3") or 3))

# 시연 화면 실시간 단계(10-15) — 답을 기다리는 동안 화면이 0.5초마다 GET /chat/progress 로 '지금 하는 일'과 지금까지의 단계·규칙
# 계산값을 묻는다. 생성이 시간의 98%라(속도 세션 실측) 규칙이 이미 낸 숫자를 먼저 보여 준다. 답 글자는 담지 않는다 — 답은 숫자 대조를
# 거친 /chat 응답에만 있다. 기록은 메모리에만 두고 /chat 이 끝나면 지운다. request_id 가 없으면(대회 규격·예전 화면) 예전과 같다.
_PROGRESS = {}
_RID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
PROGRESS_MAX = 64


def _progress_sink(rid, t0):
    """request_id 의 진행 기록을 만들고, answer() 가 부를 콜백을 돌려준다(콜백은 답 스레드에서 불린다)."""
    while len(_PROGRESS) >= PROGRESS_MAX:            # 끝나지 않은 기록이 쌓이면 오래된 것부터 버린다
        _PROGRESS.pop(min(_PROGRESS, key=lambda k: _PROGRESS[k]["t0"]), None)
    st = {"steps": [], "now": "질문을 확인하는 중", "calc": None, "t0": t0}
    _PROGRESS[rid] = st

    def cb(ev):
        for k in ("steps", "now", "calc"):
            if k in ev:
                st[k] = ev[k]
    return cb


_RULE_NOTE = {
    "late": "답을 만드는 데 시간이 오래 걸려, 문서 규칙으로 계산한 결과를 먼저 보여 드려요.",
    "net": "지금 인터넷 연결이 불안정해, AI 설명 없이 문서 규칙으로 계산한 결과를 먼저 보여 드려요.",
    "error": "답을 만드는 중 문제가 생겨, 문서 규칙으로 계산한 결과를 먼저 보여 드려요.",
}
_RULE_EMPTY = {
    "late": "답을 만드는 데 시간이 오래 걸리고 있어요. 잠시 뒤 다시 물어봐 주시거나, 아래 상담 연결로 이어 가 주세요.",
    "net": "지금 인터넷 연결이 불안정해 문서를 찾아 설명할 수 없어요. 연결이 돌아오면 다시 물어봐 주시거나, 아래 상담 연결로 이어 가 주세요.",
    "error": "죄송합니다. 일시적인 오류로 답변을 생성하지 못했습니다. 잠시 뒤 다시 물어봐 주시거나, 아래 상담 연결로 이어 가 주세요.",
}
_RULE_WHY = {"late": f"응답 마감({CHAT_DEADLINE:.0f}초) 초과", "net": "인터넷 연결 끊김", "error": "답 생성 오류"}


_AMOUNT_ASK = re.compile(r"'(?P<raw>[^']+)'은\s*(?P<a>[^,]+?)인가요,\s*(?P<b>[^?]+?)인가요")
_YES = re.compile(r"^\s*(?:네|예|응|넵|맞아|맞습니다|맞아요|그렇)")


def _offline_calc_q(question, history):
    """재작성 모델 없이 앞 질문과 이어 붙일지 정한다 → (계산할 질문, 이어 붙인 까닭 또는 '').
    answer.py 의 '대체' 경로(재작성 실패)와 같은 조건만 쓴다(10-13 검토):
      - 지금 질문만으로 계산이 되면 새 질문이다 — 장면 1 → 장면 2 버튼을 이어 눌러도 섞지 않는다
      - 정정·새 금액·새 나이가 있으면 앞 금액과 섞이므로 잇지 않는다
      - 앞 답이 금액 단위를 물었으면 고객 답으로 그 표기만 바꿔 앞 질문을 다시 계산한다(이어 붙이면 같은 되묻기가 되풀이된다)"""
    import taxcalc
    import memory as mem_mod
    real = [t for t in (history or []) if not t.get("flagged")]
    if not real:
        return question, ""
    prev = real[-1]
    prev_q = prev.get("standalone") or prev["q"]
    for ask in prev.get("needs") or []:
        m = _AMOUNT_ASK.search(ask)
        if not m or m.group("raw") not in prev_q:
            continue
        say = re.sub(r"\s+", "", question)
        opt_a, opt_b = m.group("a").strip(), m.group("b").strip()
        key = lambda o: re.sub(r"원$", "", o.replace(" ", ""))
        pick = opt_b if key(opt_b) in say else opt_a if (key(opt_a) in say or _YES.search(question)) else None
        if pick:
            return prev_q.replace(m.group("raw"), pick, 1), "금액 확인"
        return question, ""
    try:
        if taxcalc.compute(question):
            return question, ""
    except Exception:
        pass
    fu, why = mem_mod.is_followup(question, history)
    if not fu or why == "정정":
        return question, ""
    if why == "정보 보충":
        return prev_q + " " + question, why
    # 그 밖에는 '나머지·달라지면·한도까지만'처럼 앞 계산의 조건을 잇는 말이고, 새 금액·나이가 없을 때만
    if mem_mod._REF.search(question) and not taxcalc.money_spans(question) and taxcalc.customer_age(question) is None:
        return prev_q + " " + question, why
    return question, ""


def _rule_reply(question, history=None, reason="late"):
    """모델 없이 만드는 답 — 마감 초과·연결 끊김·생성 오류 때. 네트워크를 쓰지 않는다(안전 관문·세금 계산·정해진 문장).
    상담 요청이면 상담 연결 안내, 규칙 계산값이 있으면 그 값과 상담 연결, 빠진 값이 있으면 되묻기, 상품 추천 질문이면
    정해진 되묻기. 이어지는 질문은 _offline_calc_q 가 정한 경우에만 앞 질문과 이어 계산한다."""
    import taxcalc
    import verify
    import orchestrator
    import memory as mem_mod
    from answer import REFUSE, _calc_chunk, finalize, sections as make_sections
    from understand_guard import guard, detect_injection
    why = _RULE_WHY[reason]
    if guard(question)[0]:
        return {"answer": REFUSE, "think_trace": f"{why} — 안전 관문에서 막힘", "action": "거절", "blocked": True,
                "late": True, "reason": reason, "standalone": question, "needs": [],
                "sections": {"result": REFUSE, "evidence": [], "assumptions": [], "needs": [], "next_actions": []},
                "plan": [{"step": "안전 관문", "detail": "차단"}, {"step": "다음 행동", "detail": "거절"}],
                "calc": None, "verify": None, "retrieved_context": "", "injection": False}
    acts, needs, kind, calc, calc_q, how, nums = [], [], "plain", None, question, "", False
    if orchestrator._HANDOFF.search(question):
        prof = mem_mod.profile_text(mem_mod.merge_profile(history)) if history else ""
        ans = orchestrator.handoff_reply(prof)          # answer.py '상담 연결' 안내와 같은 문장
        action = "상담 연결"
    else:
        calc_q, how = _offline_calc_q(question, history)
        try:
            calc = taxcalc.compute(calc_q)
        except Exception:
            calc = None
        nums = bool(calc) and any(bl.get("numbers") for bl in calc["blocks"])
        needs = (calc or {}).get("needs", [])
        if calc and calc.get("ambiguous"):
            ans, action = orchestrator.amount_clarify_answer(calc), "되묻기"
            needs = [x["ask"] for x in calc["ambiguous"]]
        elif calc and needs:
            # 계산값이 있으면 '계산 + 되묻기'(장면 1: 330만 원 상한 + 공제 안 받은 납입금 되묻기)
            ans, kind = orchestrator.clarify_answer(calc, 1), "clarify"
            action = "계산 + 되묻기" if nums else "되묻기"
        elif nums:
            ans, action, kind = (_RULE_NOTE[reason] + "\n\n" + verify.handoff_answer(calc, 1, why="")), "상담 연결", "calc"
        elif orchestrator.is_product_ask(question):
            ans, action = orchestrator.product_clarify_answer(), "되묻기"
        else:
            ans, action = _RULE_EMPTY[reason], "상담 연결"
    try:
        acts = orchestrator.next_actions(action, calc_q, calc, None, "")
    except Exception:
        acts = []
    # 근거 — 계산 블록의 출처 문서(검색 없이 색인에서 찾는다). '[문서1]'을 실제 문서명 각주로 바꾼다
    hits = []
    if kind != "plain":
        try:
            hits = [(_calc_chunk(bl), 1.0) for bl in calc["blocks"]]
        except Exception:
            hits = []
    ans = finalize(ans, hits, [])
    sec = {"result": ans, "evidence": [], "assumptions": (calc or {}).get("assume", []), "needs": needs,
           "next_actions": acts}
    if hits:
        sec = make_sections(ans, hits, calc, acts)
        if kind == "clarify":
            sec = dict(sec, needs=[])          # 빠진 정보는 답 본문 목록에 있다(answer.py 되묻기와 같게)
            if not nums:
                sec["assumptions"] = ["아직 계산하지 않았어요 — 위 정보를 알려 주시면 문서 규칙으로 계산합니다"]
    trace = f"{why} — 모델 답 대신 {action} 답을 냄(규칙 계산·정해진 문장)" + (f" · 앞 질문과 이어 계산({how})" if how else "")
    return {"answer": ans, "think_trace": trace,
            "action": action, "late": True, "reason": reason, "standalone": calc_q, "needs": needs,
            "sections": sec,
            "plan": [{"step": _RULE_WHY[reason], "detail": f"모델 답 대신 {action} 답을 먼저 냄"}],
            "calc": None if not calc else {"rule_ids": calc.get("rids", []), "figures": calc.get("figures", []),
                                           "needs": needs, "assume": calc.get("assume", [])},
            "verify": None, "retrieved_context": "", "injection": bool(detect_injection(question))}


def _late_reply(question):
    """(호환) 마감 초과 답."""
    return _rule_reply(question, None, "late")


async def _reachable():
    """연결 확인(clova.probe) — DNS 조회는 urllib 시간 제한 밖이라 벽시계로 묶는다(10-13 검토). 넘기면 닿지 않는 것으로 본다.
    이 확인은 실제 호출이 네트워크 오류로 실패한 뒤에만 부른다 — 정상일 때는 부르지 않는다."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(clova.probe, 3.0), 6.0)
    except asyncio.TimeoutError:
        return False


def _drop_result(fut):
    """기다리지 않기로 한 답 스레드 — 끝나면 결과·예외를 거두기만 한다('Task exception was never retrieved' 방지)."""
    if not fut.cancelled():
        fut.exception()


@app.post("/chat")
async def chat(body: dict = Body(...)):
    t0 = time.time()
    question = str(body.get("question") or "").strip()[:MAX_Q]
    sid = str(body.get("session_id") or "").strip()
    # 시연 화면은 세션 없이 첫 질문을 보낼 수 있다 — 서버가 만들어 돌려준다(없으면 대화 기억이 안 쌓인다)
    sid = sid if _SID.match(sid or "-") else secrets.token_urlsafe(24)
    if not question:
        return {"answer": "질문을 입력해 주십시오.", "elapsed": 0, "session_id": sid}
    rid = str(body.get("request_id") or "").strip()
    prog = _progress_sink(rid, t0) if _RID.match(rid) else None
    global _inflight
    async with _inflight_lock:
        _inflight += 1
    try:
        history = STORE.history(sid) if sid else None
        try:
            # 빠른 모드: 초안 CHAT_SAMPLES개(생각 low, 카드 답 low, 계산 답 none) + 병합 / 정밀 모드: 초안 CHAT_SAMPLES개(생각 high) + 병합
            if str(body.get("mode") or "") == "fast":
                kw = {"samples": CHAT_SAMPLES, "draft_effort": CHAT_FAST_EFFORT}
                if CHAT_SAMPLES <= 1:
                    kw["draft_maxtok"] = 4096
                if CHAT_CALC_EFFORT:
                    kw["calc_effort"] = CHAT_CALC_EFFORT
            else:
                kw = {"samples": CHAT_SAMPLES, "draft_effort": CHAT_PRECISE_EFFORT, "draft_maxtok": 4096}
            if prog:
                kw["progress"] = prog
            r = None
            # 바로 앞 호출에서 연결이 끊겼으면 먼저 짧게 확인한다 — 아직 끊겨 있으면 기다리지 않고 규칙 답(라이브 시연 안전장치)
            if clova.net_down() and not await _reachable():
                print("[net] /chat 인터넷 연결 끊김 — 규칙 답으로 대신함", flush=True)
                r = await asyncio.to_thread(_rule_reply, question, history, "net")
            else:
                fut = asyncio.ensure_future(
                    asyncio.to_thread(answer, question, "", history=history, clarify_template=True, **kw))
                while r is None:
                    done, _ = await asyncio.wait({fut}, timeout=1.0)
                    if done:
                        r = fut.result()
                        break
                    # 답을 만드는 중 네트워크 실패가 생겼고 확인해도 닿지 않으면 재시도 대기(최대 63초)를 기다리지 않는다
                    if clova.net_failed_since(t0) and not await _reachable():
                        if fut.done():
                            continue                # 확인하는 사이 모델 답이 끝났다 — 그 답을 쓴다
                        fut.add_done_callback(_drop_result)
                        print("[net] /chat 답 생성 중 인터넷 연결 끊김 — 규칙 답으로 대신함", flush=True)
                        r = await asyncio.to_thread(_rule_reply, question, history, "net")
                        break
                    if time.time() - t0 >= CHAT_DEADLINE:
                        # 스레드는 뒤에서 마저 돌지만 화면은 기다리지 않는다(라이브 시연 안전장치)
                        fut.add_done_callback(_drop_result)
                        print(f"[late] /chat {CHAT_DEADLINE:.0f}초 초과 — 규칙 답으로 대신함", flush=True)
                        r = await asyncio.to_thread(_rule_reply, question, history, "late")
                        break
        except Exception as e:
            print(f"[error] /chat: {e}\n{traceback.format_exc(limit=3)}", flush=True)
            # 사과문만 내지 않는다 — 규칙 계산·정해진 되묻기는 모델 없이 만들 수 있다
            try:
                r = await asyncio.to_thread(_rule_reply, question, history,
                                            "net" if clova.net_failed_since(t0) else "error")
            except Exception as e2:
                print(f"[error] /chat 규칙 답: {e2}", flush=True)
                return {"answer": "죄송합니다. 일시적인 오류로 답변을 생성하지 못했습니다.", "elapsed": round(time.time() - t0, 1),
                        "session_id": sid, "error": type(e).__name__}
        out = _to_schema(r, "", question)
        if sid and not r.get("blocked"):
            await asyncio.to_thread(STORE.add, sid, question, r.get("standalone") or question, out["answer"],
                                    bool(r.get("injection")), r.get("needs"))
        el = time.time() - t0
        log_query("chat", question, out, el, _inflight, None, session_id=sid, standalone=r.get("standalone", ""))
        sec = r.get("sections") or {"result": out["answer"], "evidence": [], "assumptions": [], "needs": [],
                                    "next_actions": []}
        return {"answer": out["answer"], "elapsed": round(el, 1), "session_id": sid, "question": question,
                "sections": sec, "plan": r.get("plan") or [], "calc": r.get("calc"),
                "verify": r.get("verify"), "action": r.get("action", ""), "trace": out["think_trace"],
                "standalone": r.get("standalone", ""), "late": bool(r.get("late")), "reason": r.get("reason", "")}
    finally:
        if prog:
            _PROGRESS.pop(rid, None)
        async with _inflight_lock:
            _inflight -= 1


@app.get("/chat/progress")
async def chat_progress(id: str = Query("")):
    """답을 기다리는 동안의 단계 — 지금 하는 일, 지금까지의 단계, 규칙 계산값(답 글자 없음)."""
    st = _PROGRESS.get(id) if _RID.match(id or "") else None
    if not st:
        return {"found": False}
    return {"found": True, "now": st["now"], "steps": list(st["steps"] or []), "calc": st["calc"],
            "elapsed": round(time.time() - st["t0"], 1)}


def _module(name):
    try:
        return __import__(name)
    except Exception as e:
        print(f"[demo] {name} 모듈을 불러오지 못함: {e}", flush=True)
        return None


@app.get("/alerts/profiles")
def alert_profiles():
    m = _module("alerts")
    if not m:
        return JSONResponse({"error": "alerts 모듈 없음"}, status_code=503)
    out = []
    for p in m.load_profiles():
        out.append({"id": p.get("id"), "name": p.get("name"), "summary": p.get("summary", "")})
    return out


@app.post("/alerts")
def alerts_scan(body: dict = Body(...)):
    m = _module("alerts")
    if not m:
        return JSONResponse({"error": "alerts 모듈 없음"}, status_code=503)
    pid = body.get("profile_id")
    prof = body.get("profile") or next((p for p in m.load_profiles() if p.get("id") == pid), None)
    if not isinstance(prof, dict):
        return JSONResponse({"error": "고객을 찾을 수 없음"}, status_code=404)
    today = str(body.get("today") or time.strftime("%Y-%m-%d"))
    try:
        al = m.scan(prof, today)
    except ValueError as e:                      # 기준일 형식 등 입력 오류
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        m.push(prof.get("id"), al)
    except Exception:
        pass
    return {"alerts": al, "report_md": m.report(prof, today, alerts=al), "today": today}


@app.get("/alerts/outbox")
def alerts_outbox(profile_id: str = Query("")):
    m = _module("alerts")
    if not m:
        return JSONResponse({"error": "alerts 모듈 없음"}, status_code=503)
    return {"alerts": m.pull(profile_id)}


@app.post("/diy")
def diy_simulate(body: dict = Body(...)):
    m = _module("diy")
    if not m:
        return JSONResponse({"error": "diy 모듈 없음"}, status_code=503)
    try:
        return m.simulate(body)
    except ValueError as e:                      # 입력 오류만 400 — 코드 오류는 500 으로 드러나게
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        print(f"[error] /diy: {e}\n{traceback.format_exc(limit=3)}", flush=True)
        return JSONResponse({"error": f"{type(e).__name__}"}, status_code=500)


@app.get("/rules")
def rules_table():
    import taxcalc
    dm = _module("doc_meta")
    out = []
    for rid, name, keys, summary, how in taxcalc.RULEBOOK:
        quotes = []
        for k in keys:
            f, text = taxcalc.RULES[k]
            asof = ""
            if dm:
                try:
                    asof = dm.as_of(f) or ""
                except Exception:
                    asof = ""
            title, aud = "", ""
            if dm:
                try:
                    title, aud = (dm.meta(f) or {}).get("title", ""), dm.audience(f) or ""
                except Exception:
                    title, aud = "", ""
            quotes.append({"file": f, "text": text, "as_of": asof, "title": title, "audience": aud})
        out.append({"rid": rid, "name": name, "summary": summary, "how": how, "quotes": quotes})
    return out


@app.get("/ontology")
def ontology_graph():
    m = _module("ontology")
    if not m:
        return JSONResponse({"error": "ontology 모듈 없음"}, status_code=503)
    return m.graph()


@app.get("/ontology/overview")
def ontology_overview():
    """시연 화면 '세금 지도' — 재원 × 인출 방식 9칸, 단계별 규칙, 문서 제목(문서 번호 대신)."""
    m = _module("ontology")
    if not m:
        return JSONResponse({"error": "ontology 모듈 없음"}, status_code=503)
    return m.overview()


@app.post("/ontology/path")
def ontology_path(body: dict = Body(...)):
    """질문이 세금 지도에서 지나는 칸(모델 호출 없음). 화면이 마지막 상담 질문(재작성된 질문)을 보낸다."""
    m = _module("ontology")
    if not m:
        return JSONResponse({"error": "ontology 모듈 없음"}, status_code=503)
    q = str(body.get("question") or "").strip()[:MAX_Q]
    if not q:
        return {"cells": [], "stage": "", "rule_ids": [], "intent": "", "steps": []}
    try:
        return m.question_cells(q)
    except Exception as e:                       # 경로를 못 그려도 화면은 그대로
        print(f"[error] /ontology/path: {e}", flush=True)
        return {"cells": [], "stage": "", "rule_ids": [], "intent": "", "steps": []}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "80"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
