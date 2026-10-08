# -*- coding: utf-8 -*-
"""
평가용 API 서버.

주최측 규격 (API명세.md):
  GET /answer?question_id={id}&question={질의}
  → JSON {question_id, question, retrieved_context, think_trace, answer}  전부 문자열
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


def mask(t):
    for pat, rep_ in _MASKS:
        t = pat.sub(rep_, t or "")
    return t


def _trace_field(trace, head):
    for line in (trace or "").split("\n"):
        if line.startswith(head):
            return line[len(head):].strip()
    return ""


def log_query(question_id, question, out, elapsed, load, samples, error=None):
    try:
        trace = out.get("think_trace", "")
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "question_id": question_id,
            "question": mask(question),
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


@app.get("/answer")
async def answer_endpoint(
    question_id: str = Query("", description="질의 ID"),
    question: str = Query("", description="평가 질의"),
):
    t0 = time.time()
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
            r = await asyncio.to_thread(answer, question, question_id, samples=n)
            note = f" (동시 {load}건, 초안 {n}개로 축소)" if n else f" (동시 {load}건)"
            r["think_trace"] = f"{r['think_trace']}\n소요 {time.time()-t0:.1f}s{note}"
            out = _to_schema(r, question_id, question)
            log_query(question_id, question, out, time.time() - t0, load, n)
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "80"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
