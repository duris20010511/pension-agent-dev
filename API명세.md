# 평가용 API End-point 명세 (주최측 공지)

## 제출
- 네이버클라우드 또는 개인 서버에 Agent를 API 형태로 구성. **Public 망 접근 가능 필수**
- End-point 주소는 **README.md에 명시(필수)**
- 평가 담당자가 GET 요청으로 비공개 평가질의 전송 → 응답 수집·평가

## 평가 호출 방식
1. 평가 질의는 팀당 **순차 1건씩** (동시 요청 없음)
2. 문항당 타임아웃 **300초**
3. 타임아웃/5xx 시 **최대 2회 재시도**

## 요청 규격 (주최측 → 참가팀)
```
GET {team-end-point}/answer?question_id={질의 ID}&question={평가 질의}
```
- 경로 `/answer` 고정
- 요청에 별도 헤더 없음 (인증 헤더 포함)
- 접근 제어 필요 시 주최측 발신 IP 허용 방식 (IP 대역 추후 공지)

## 응답 규격 (참가팀 → 주최측, application/json)
```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 문서",
  "think_trace": "사고, 추론, 도구 사용 과정",
  "answer": "최종 생성 답변"
}
```
- 모든 필드 **문자열(string)** 타입
- retrieved_context에 여러 문서 → 문자열 안에서 구분 태그 자유 (태그 자체는 평가 대상 아님)
- 필드 길이 제한 없음. 단 극단적으로 길면 초과분은 평가에 미반영될 수 있음

## 서버 구성
1. HTTP 기본, HTTPS도 무방 (자체 서명 인증서 OK)
2. 포트: HTTP 80 / HTTPS 443 (표준 포트)
3. 도메인 불필요 — 공인 IP 제출로 충분
4. 주최측 발신 IP 대역 추후 공지 (방화벽 허용 구성 가능)
5. **서버 운영 기간: 09.07 ~ 09.20 중 별도 공지 기간** (주제별 최대 1주)
6. 마감 후 '결과물 변경'이 아닌 서버 장애 재기동은 실격 아님
7. 개인 노트북 구성 시 정적 도메인 URL 제출 (ngrok 등 터널링 자유)

## 요청 예시
```python
import requests
URL = "http://123.45.67.89/answer"  # 공인 IP 직접 접근 가능, 표준 포트는 표기 생략
params = {"question_id": "Q-001", "question": "평가 질의"}
resp = requests.get(URL, params=params, timeout=300)
result = resp.json()
```
```bash
curl -G "http://123.45.67.89/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=평가 질의"
```
