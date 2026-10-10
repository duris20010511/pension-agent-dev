# -*- coding: utf-8 -*-
"""
HyperCLOVA X 클라이언트 (CLOVA Studio).

과제 규정상 LLM 은 HyperCLOVA X 만 쓸 수 있다. 임베딩도 같은 계열로 붙여야 안전하다.

키는 코드에 넣지 말고 프로젝트 루트의 .env 에 둔다 (.gitignore 에 포함시킬 것).
  CLOVA_API_KEY=nv-...
  CLOVA_CHAT_MODEL=HCX-005
  CLOVA_EMBED_MODEL=v2   # 임베딩 v2 = bge-m3. 경로 세그먼트는 'v2' 다

엔드포인트 (2026-08 기준 공식 문서):
  chat  : https://clovastudio.stream.ntruss.com/v3/chat-completions/{model}
  embed : https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2
          (모델명 bge-m3 를 넣으면 40080 Model not found. 'v2' 가 맞다 — 실측 확인)
  헤더  : Authorization: Bearer {key}, X-NCP-CLOVASTUDIO-REQUEST-ID, Content-Type: application/json

콘솔에서 발급한 앱 종류에 따라 경로에 /testapp 이 붙는 경우가 있다.
연결이 404 면 CLOVA_BASE 를 .env 에서 바꿔 끼우면 된다.
"""
import os
import re
import json
import time
import uuid
import threading
import contextvars
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    """.env 를 읽어 환경변수로 올린다. 이미 설정된 값은 덮어쓰지 않는다."""
    p = os.path.join(ROOT, '.env')
    if not os.path.exists(p):
        return
    for line in open(p, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

BASE = os.environ.get('CLOVA_BASE', 'https://clovastudio.stream.ntruss.com')
API_KEY = os.environ.get('CLOVA_API_KEY', '')
CHAT_MODEL = os.environ.get('CLOVA_CHAT_MODEL', 'HCX-005')
EMBED_MODEL = os.environ.get('CLOVA_EMBED_MODEL', 'v2')


class ClovaError(RuntimeError):
    pass


# 한도는 429 응답 헤더에 그대로 적혀 온다 (실측 2026-08-21):
#   x-ratelimit-limit-requests: 60      분당 60회
#   x-ratelimit-remaining-requests: 0
#   x-ratelimit-reset-requests: 25s     복구까지
#   (채팅은 x-ratelimit-limit-tokens: 60000 도 함께)
# 채팅·임베딩 각각 분당 60회 = 초당 1회. 병렬화로 못 넘는다.
#
# 짧은 더미로 벤치마크하면 17건/초가 나오는데 그건 quota 가 차 있을 때뿐이다.
# 그걸 믿고 6,803개를 돌렸다가 33분을 재시도로만 태운 적이 있다.
# → 처음부터 429 헤더를 읽었어야 했다.
RATE_PER_MIN = float(os.environ.get('CLOVA_RATE_PER_MIN', '60'))
SAFETY = float(os.environ.get('CLOVA_RATE_SAFETY', '1.08'))   # 8% 여유
MIN_INTERVAL = 60.0 / RATE_PER_MIN * SAFETY
EMBED_MIN_INTERVAL = MIN_INTERVAL

# 한도는 **모델마다 따로** 센다(실측 2026-10-08 — HCX-007 로 8천 토큰을 쓴 직후에도
# HCX-005 의 남은 토큰은 그대로였다). 예전엔 채팅을 한 통으로 묶어서 HCX-007 의 429 가
# HCX-005(채점·분류) 호출까지 세웠다. 그래서 모델 이름별로 따로 관리한다.
#
# 토큰 한도는 **입력 + 설정한 최대 출력**으로 차감된다(실측: 실제 출력 18토큰인데
# maxCompletionTokens=2000 이면 2,007 차감). 그래서 보내기 전에 차감될 양을 추정하고,
# 응답 헤더(x-ratelimit-remaining-tokens / reset-tokens)로 남은 양을 따라간다.
# 남은 양이 모자라면 429 를 맞고 1분을 통째로 쉬는 대신 필요한 만큼만 미리 기다린다.
# 테스트 앱 키 한도(분당): HCX-007·HCX-005 60회·6만, HCX-DASH-002 90회·8만, 임베딩 60회·4만(입력만).
TOKENS_PER_CHAR = float(os.environ.get('CLOVA_TOKENS_PER_CHAR', '0.6'))   # 한국어 실측 0.54, 넉넉히

_rate = {}
_rate_lock = threading.Lock()


def _state(key):
    with _rate_lock:
        if key not in _rate:
            _rate[key] = {'lock': threading.Lock(), 'last': 0.0, 'until': 0.0,
                          'tokens': None, 'limit': None, 'reset_s': 60.0, 'seen': 0.0}
        return _rate[key]


def estimate_charge(payload):
    """이 요청이 토큰 한도에서 차감될 양의 추정치."""
    if 'messages' in payload:
        chars = sum(len(m.get('content') or '') for m in payload['messages'])
    else:
        chars = len(payload.get('text') or '')
    out = payload.get('maxCompletionTokens') or payload.get('maxTokens') or 0
    return int(chars * TOKENS_PER_CHAR) + 32 + int(out)


def _available(b, now):
    """지금 쓸 수 있는 토큰 추정. 헤더의 reset 은 '다 찰 때까지 남은 시간'이라 선형으로 찬다고 본다."""
    if b['tokens'] is None or not b['limit']:
        return None
    rate = (b['limit'] - b['tokens']) / max(b['reset_s'], 1.0)
    return min(b['limit'], b['tokens'] + rate * max(0.0, now - b['seen']))


def budget(model=None):
    """(지금 쓸 수 있는 토큰 추정, 분당 한도). 아직 헤더를 못 봤으면 (None, None)."""
    b = _state(model or CHAT_MODEL)
    with b['lock']:
        return _available(b, time.time()), b['limit']


def _throttle(key='chat', charge=0):
    b = _state(key)
    with b['lock']:
        now = time.time()
        # 429 로 알아낸 복구 시각이 있으면 그때까지 기다린다
        if b['until'] > now:
            time.sleep(b['until'] - now)
            now = time.time()
        # 토큰이 모자라면 찰 때까지만 기다린다
        avail = _available(b, now)
        if avail is not None and charge and avail < charge:
            rate = b['limit'] / max(b['reset_s'], 1.0)
            time.sleep(min(70.0, (charge - avail) / max(rate, 1.0) + 0.3))
            now = time.time()
            avail = _available(b, now)
        if avail is not None and charge:
            # 동시에 나가는 요청끼리 같은 잔량을 두 번 쓰지 않게 미리 떼어 둔다
            b['tokens'], b['seen'] = max(0.0, avail - charge), now
        wait = MIN_INTERVAL - (now - b['last'])
        if wait > 0:
            time.sleep(wait)
        b['last'] = time.time()


def _seconds(raw):
    m = re.match(r'([\d.]+)\s*(ms|s|m)?', str(raw or '').strip())
    if not m:
        return 0.0
    v, unit = float(m.group(1)), m.group(2) or 's'
    return v / 1000 if unit == 'ms' else (v * 60 if unit == 'm' else v)


def _note_headers(key, headers):
    """성공 응답 헤더로 남은 토큰을 맞춘다.

    잠금을 기다리지 않는다. _throttle 은 잠금을 쥔 채로 한도 대기(최대 70초)를 하므로, 여기서
    잠금을 기다리면 **이미 끝난 응답**이 남의 한도 대기가 끝날 때까지 호출자에게 돌아가지 못한다.
    실제로 초안 3개 설정의 응답 시간이 이 때문에 부풀어 측정됐다(score_gen 검토, 2026-10-08).
    헤더 반영은 추정치를 다듬는 것일 뿐이라 한 번 건너뛰어도 된다."""
    try:
        h = {k.lower(): v for k, v in dict(headers).items()}
        if 'x-ratelimit-remaining-tokens' not in h:
            return
        b = _state(key)
        if not b['lock'].acquire(blocking=False):
            return
        try:
            b['tokens'] = float(h['x-ratelimit-remaining-tokens'])
            b['limit'] = float(h.get('x-ratelimit-limit-tokens') or b['limit'] or 0) or None
            b['reset_s'] = _seconds(h.get('x-ratelimit-reset-tokens')) or b['reset_s']
            b['seen'] = time.time()
        finally:
            b['lock'].release()
    except Exception:
        pass


def _note_429(key, headers):
    """429 헤더의 reset 값(요청·토큰 중 긴 쪽)을 읽어 그 시각까지 전면 대기시킨다."""
    reset = 0.0
    try:
        h = {k.lower(): v for k, v in dict(headers).items()}
        reset = max(_seconds(h.get('x-ratelimit-reset-requests')),
                    _seconds(h.get('x-ratelimit-reset-tokens')))
    except Exception:
        pass
    reset = min(max(reset, 1.0), 90.0)
    b = _state(key)
    with b['lock']:
        b['until'] = max(b['until'], time.time() + reset)
        if b['limit']:
            b['tokens'], b['seen'] = 0.0, time.time()
    return reset


# 호출별 사용량(최근 1,000건). 출력 상한(maxCompletionTokens)을 실제 필요량에 맞추는 근거다.
USAGE = []
_usage_lock = threading.Lock()

# 측정 스크립트가 호출을 문항·설정별로 가르기 위한 꼬리표. 운영에서는 비어 있다.
# 스레드 풀로 넘어가는 호출은 contextvars.copy_context().run 으로 감싸야 따라간다(answer.py 초안).
TAG = contextvars.ContextVar('clova_tag', default=None)


def _note_usage(model, payload, out, sec, wait=0.0, t_send=None):
    try:
        u = (out.get('result') or {}).get('usage') or {}
        rec = {'model': model, 'prompt': u.get('promptTokens'), 'completion': u.get('completionTokens'),
               'thinking': (u.get('completionTokensDetails') or {}).get('thinkingTokens', 0),
               'max': payload.get('maxCompletionTokens') or payload.get('maxTokens'),
               'effort': (payload.get('thinking') or {}).get('effort'), 'sec': round(sec, 2),
               # 한도 대기(_throttle·429)는 sec 와 따로 센다 — 응답 시간을 잴 때 빼야 한다
               'wait': round(wait, 2), 'tag': TAG.get(), 'thread': threading.get_ident(),
               't_send': round(t_send, 3) if t_send else None,
               'stop': ((out.get('result') or {}).get('stopReason')
                        or (out.get('result') or {}).get('finishReason'))}
        with _usage_lock:
            USAGE.append(rec)
            if len(USAGE) > 1000:
                del USAGE[:-1000]
    except Exception:
        pass


# Claude 클라우드 세션에서는 키를 '네트워크 시크릿'으로 두면 프록시가 VM 밖에서 Authorization 헤더를
# 붙인다 — 키가 세션 안에 들어오지 않는다. 그때는 CLOVA_KEY_VIA_PROXY=1 로 두고 헤더를 비운다.
KEY_VIA_PROXY = os.environ.get('CLOVA_KEY_VIA_PROXY') == '1'


def _post(url, payload, timeout=120, retries=6, kind='chat'):
    if not API_KEY and not KEY_VIA_PROXY:
        raise ClovaError('CLOVA_API_KEY 가 없습니다. .env 를 확인하세요.')
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    key = 'embed' if kind == 'embed' else url.rstrip('/').rsplit('/', 1)[-1]   # 모델 이름
    charge = estimate_charge(payload)
    last = None
    t_first = time.time()
    for attempt in range(retries):
        _throttle(key, charge)
        t0 = time.time()
        headers = {
            'X-NCP-CLOVASTUDIO-REQUEST-ID': uuid.uuid4().hex,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        if not KEY_VIA_PROXY:
            headers['Authorization'] = f'Bearer {API_KEY}'
        req = urllib.request.Request(url, data=body, method='POST', headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                t_end = time.time()          # 시계는 잠금·기록 전에 멈춘다
                _note_headers(key, r.headers)
                out = json.loads(raw.decode('utf-8'))
                if kind != 'embed':
                    # wait = 성공한 시도 전까지 쓴 시간(한도 대기 + 429·재시도)
                    _note_usage(key, payload, out, t_end - t0, t0 - t_first, t0)
                return out
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'ignore')[:400]
            last = ClovaError(f'HTTP {e.code} {url}\n{detail}')
            if e.code == 429:
                _note_429(key, dict(e.headers))      # 헤더의 reset 만큼 전면 대기
                continue
            if e.code in (408, 500, 502, 503, 504):     # 408: CLOVA 쪽 요청 시간 초과 — 다시 보내면 대개 된다
                time.sleep(2 ** attempt)
                continue
            raise last
        except Exception as e:                      # 타임아웃·네트워크
            last = ClovaError(f'{type(e).__name__}: {e}')
            time.sleep(2 ** attempt)
    raise last


def is_thinking_model(name=None):
    """추론 모델(HCX-007 계열)은 파라미터 이름이 다르다 — maxCompletionTokens."""
    return (name or CHAT_MODEL).upper().startswith('HCX-007')


def chat(messages, temperature=0.2, max_tokens=2048, top_p=0.8, seed=0, model=None, timeout=120, retries=6):
    """messages = [{'role':'system'|'user'|'assistant', 'content': '...'}]

    추론 모델이 설정돼 있으면 chat_thinking 으로 자동 전환한다.
    (HCX-007 에 maxTokens 를 보내면 400 이 난다 — 호출부마다 분기하면 놓치기 쉽다)

    model 을 주면 그 모델로 부른다. 전역 CHAT_MODEL 을 잠깐 바꿨다 되돌리는 방식은
    스레드가 여럿이면 깨진다 — 채점 스레드가 HCX-005 로 바꾼 사이에 생성 스레드가
    HCX-007 용 thinking 파라미터를 HCX-005 로 보내 400 이 났다(score_speed, 2026-10-07).
    되돌리는 순서가 엇갈리면 전역이 HCX-005 로 굳을 수도 있다.
    """
    m = model or CHAT_MODEL
    if is_thinking_model(m):
        return chat_thinking(messages, max_tokens=max(max_tokens, 1024),
                             temperature=temperature, top_p=top_p, seed=seed, model=m)['content']
    url = f'{BASE}/v3/chat-completions/{m}'
    out = _post(url, {
        'messages': messages,
        'temperature': temperature,
        'maxTokens': max_tokens,
        'topP': top_p,
        'seed': seed,
        'repeatPenalty': 1.1,
    }, timeout=timeout, retries=retries)
    try:
        return out['result']['message']['content']
    except (KeyError, TypeError):
        raise ClovaError(f'예상치 못한 응답 형태: {json.dumps(out, ensure_ascii=False)[:400]}')


def embed(text):
    """단일 텍스트 → 1024차원 벡터. 임베딩 v2(bge-m3), 최대 8,192 토큰."""
    url = f'{BASE}/v1/api-tools/embedding/{EMBED_MODEL}'
    out = _post(url, {'text': text}, kind='embed')
    for path in (('result', 'embedding'), ('result', 'embeddings'), ('embedding',)):
        cur = out
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                cur = None
                break
            cur = cur[k]
        if isinstance(cur, list):
            return cur
    raise ClovaError(f'임베딩 응답 파싱 실패: {json.dumps(out, ensure_ascii=False)[:400]}')


def chat_thinking(messages, max_tokens=4096, temperature=0.2, top_p=0.8, seed=0,
                  effort='low', model=None):
    """HCX-007 계열 추론 모델. maxTokens 가 아니라 maxCompletionTokens 를 쓴다.
    응답에 thinkingContent(추론 과정)가 따로 담겨 온다 — 과제의 think_trace 에 그대로 쓸 수 있다."""
    url = f'{BASE}/v3/chat-completions/{model or CHAT_MODEL}'
    payload = {
        'messages': messages,
        'maxCompletionTokens': max_tokens,
        'temperature': temperature,
        'topP': top_p,
        'seed': seed,
    }
    if effort:
        payload['thinking'] = {'effort': effort}
    out = _post(url, payload, timeout=240)
    try:
        msg = out['result']['message']
    except (KeyError, TypeError):
        raise ClovaError(f'예상치 못한 응답 형태: {json.dumps(out, ensure_ascii=False)[:400]}')
    res = out.get('result') or {}
    return {'content': msg.get('content', ''),
            'thinking': msg.get('thinkingContent', ''),
            # 'length' 면 출력 상한에 걸려 잘린 것이다
            'stop': res.get('stopReason') or res.get('finishReason') or ''}


def healthcheck():
    """키·엔드포인트가 살아있는지 최소 호출로 확인."""
    report = {}
    try:
        report['chat'] = chat([{'role': 'user', 'content': '한 단어로만 답하세요: 연금'}],
                              max_tokens=1024)[:60]
    except Exception as e:
        report['chat'] = f'FAIL — {e}'
    try:
        v = embed('퇴직연금 세액공제 한도')
        report['embed'] = f'OK — {len(v)}차원'
    except Exception as e:
        report['embed'] = f'FAIL — {e}'
    return report


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print(f'BASE       {BASE}')
    print(f'CHAT_MODEL {CHAT_MODEL}')
    print(f'EMBED_MODEL{EMBED_MODEL}')
    print(f'API_KEY    {"설정됨 (" + API_KEY[:6] + "…)" if API_KEY else "없음"}')
    if API_KEY:
        for k, v in healthcheck().items():
            print(f'  {k:6} {v}')
