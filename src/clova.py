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

_buckets = {
    'chat': {'lock': threading.Lock(), 'last': 0.0, 'until': 0.0},
    'embed': {'lock': threading.Lock(), 'last': 0.0, 'until': 0.0},
}


def _throttle(kind='chat'):
    b = _buckets.get(kind) or _buckets['chat']
    with b['lock']:
        now = time.time()
        # 429 로 알아낸 복구 시각이 있으면 그때까지 기다린다
        if b['until'] > now:
            time.sleep(b['until'] - now)
            now = time.time()
        wait = MIN_INTERVAL - (now - b['last'])
        if wait > 0:
            time.sleep(wait)
        b['last'] = time.time()


def _note_429(kind, headers):
    """429 헤더의 reset 값을 읽어 그 시각까지 전면 대기시킨다."""
    reset = 0.0
    try:
        raw = (headers.get('x-ratelimit-reset-requests')
               or headers.get('X-RateLimit-Reset-Requests') or '')
        m = re.match(r'([\d.]+)\s*(ms|s|m)?', str(raw).strip())
        if m:
            v = float(m.group(1))
            unit = m.group(2) or 's'
            reset = v / 1000 if unit == 'ms' else (v * 60 if unit == 'm' else v)
    except Exception:
        pass
    reset = min(max(reset, 1.0), 90.0)
    b = _buckets.get(kind) or _buckets['chat']
    with b['lock']:
        b['until'] = max(b['until'], time.time() + reset)
    return reset


def _post(url, payload, timeout=120, retries=6, kind='chat'):
    if not API_KEY:
        raise ClovaError('CLOVA_API_KEY 가 없습니다. .env 를 확인하세요.')
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    last = None
    for attempt in range(retries):
        _throttle(kind)
        req = urllib.request.Request(url, data=body, method='POST', headers={
            'Authorization': f'Bearer {API_KEY}',
            'X-NCP-CLOVASTUDIO-REQUEST-ID': uuid.uuid4().hex,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'ignore')[:400]
            last = ClovaError(f'HTTP {e.code} {url}\n{detail}')
            if e.code == 429:
                _note_429(kind, dict(e.headers))     # 헤더의 reset 만큼 전면 대기
                continue
            if e.code in (500, 502, 503, 504):
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


def chat(messages, temperature=0.2, max_tokens=2048, top_p=0.8, seed=0):
    """messages = [{'role':'system'|'user'|'assistant', 'content': '...'}]

    추론 모델이 설정돼 있으면 chat_thinking 으로 자동 전환한다.
    (HCX-007 에 maxTokens 를 보내면 400 이 난다 — 호출부마다 분기하면 놓치기 쉽다)
    """
    if is_thinking_model():
        return chat_thinking(messages, max_tokens=max(max_tokens, 1024),
                             temperature=temperature, top_p=top_p, seed=seed)['content']
    url = f'{BASE}/v3/chat-completions/{CHAT_MODEL}'
    out = _post(url, {
        'messages': messages,
        'temperature': temperature,
        'maxTokens': max_tokens,
        'topP': top_p,
        'seed': seed,
        'repeatPenalty': 1.1,
    })
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
                  effort='low'):
    """HCX-007 계열 추론 모델. maxTokens 가 아니라 maxCompletionTokens 를 쓴다.
    응답에 thinkingContent(추론 과정)가 따로 담겨 온다 — 과제의 think_trace 에 그대로 쓸 수 있다."""
    url = f'{BASE}/v3/chat-completions/{CHAT_MODEL}'
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
    return {'content': msg.get('content', ''),
            'thinking': msg.get('thinkingContent', '')}


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
