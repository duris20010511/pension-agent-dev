FROM python:3.11-slim

WORKDIR /app

# 의존성 먼저 (레이어 캐시)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 + 추출본 + 캐시
#   .cache 에 색인(40MB)과 임베딩(27MB)이 들어간다.
#   컨테이너 안에서 다시 만들면 임베딩에만 2시간이 걸리고 API 한도를 태운다.
#   반드시 이미지에 포함하거나 볼륨으로 마운트할 것.
COPY src/ ./src/
COPY extracted/ ./extracted/
COPY .cache/ ./.cache/
# 펀드 요약 카드(색인 v3). 없으면 색인 지문이 달라져 캐시를 못 찾는다.
COPY data/ ./data/

ENV PYTHONUNBUFFERED=1
# 임베딩 캐시를 못 찾으면 기동을 실패시킨다. 조용히 BM25 단독으로 돌면 검색 R@10 이 87.5 → 75.0 이다.
ENV AGENT_REQUIRE_EMB=1
ENV PORT=80
EXPOSE 80

# .env 는 이미지에 넣지 않는다. 실행 시 -e 또는 --env-file 로 주입.
CMD ["python", "src/server.py"]
