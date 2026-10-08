# 클라우드 작업: 생성 방식 비교 실험 (g1)

이 저장소는 미래에셋 AI Festival 연금 Agent(팀 보송타로) 본선 작업본의 **스냅숏**이다(로컬 브랜치
finals-improve 1fa9af3 + 검색 캐시). 이 세션의 일은 아래 실험 하나를 끝까지 돌려 결과를 push 하는 것이다.

## 무엇을 비교하나

`src/score_gen.py` — 같은 문항에 네 생성 방식을 이어서 돌리고 채점한다(평가셋 100문항).

| 이름 | 방식 |
|---|---|
| q3low | 초안 3개(생각 low) + 병합(생각 끔) — 지금 기본 |
| q3none | 초안 3개(생각 끔) + 병합(생각 끔) |
| one_med | 초안 1개(생각 medium, 출력 상한 4096) |
| one_high | 초안 1개(생각 high, 출력 상한 4096) |

## 지켜야 할 것

- **키·환경변수 값을 출력하지 않는다.** `env`, `printenv`, `echo $CLOVA_*` 금지. 키는 네트워크 시크릿으로
  프록시가 붙인다(`CLOVA_KEY_VIA_PROXY=1`). 그 변수가 없고 `CLOVA_API_KEY` 가 있으면 그걸 쓴다.
- `src/answer.py`, `src/clova.py`, 평가셋은 **고치지 않는다.** 실험 도중 코드를 바꾸면 행끼리 비교가 깨진다.
  `score_gen.py` 가 죽는 버그만 고치고, 고쳤으면 무엇을 왜 고쳤는지 최종 보고에 적는다.
- CLOVA 테스트 키는 분당 6만 토큰(HCX-007)이다. 한도 대기는 정상이다. 동시 실행 수(`--workers 2`)를 올리지 않는다.
- 결과 해석에서 신뢰구간이 0을 걸치면 "차이가 있다"고 쓰지 않는다.

## 순서

0. 준비: `pip install numpy==2.2.1` (fastapi·uvicorn 은 실험에 필요 없다).
1. 연결 확인 (`src/` 에서):
   `python -X utf8 -c "import clova; print(clova.chat([{'role':'user','content':'한 단어로 인사해줘'}], max_tokens=20, model='HCX-005'))"`
   실패하면(HTTP 401·연결 오류) 더 하지 말고 오류 문구만 보고하고 끝낸다 — 환경 설정 문제다.
2. 캐시 확인: `python -X utf8 -c "import answer as A; H=A.get_hybrid(); print(H is not None, len(H.chunks))"`
   → `True 7199` 가 나와야 한다. 아니면 멈추고 보고한다(캐시를 새로 만들지 말 것 — 임베딩 수천 번 호출).
3. 본 실험: `src/` 에서 아래를 **앞에서(foreground) 반복** 실행한다. 한 번에 10분 남짓 돌고 스스로 멈추며,
   다시 실행하면 이어서 돈다. 출력 마지막 줄의 `남은 (문항, 설정)` 이 0이 될 때까지 반복한다.

   ```
   python -X utf8 score_gen.py --tag g1 --stop-after 10
   ```

   - 매 실행이 끝날 때마다 `eval/_gen_g1.json` 을 커밋하고 이 세션의 작업 브랜치로 push 한다(중간 결과 보존).
   - **턴을 끝내지 말고** 다 돌 때까지 계속 실행한다. 세션이 쉬면 VM 이 멈춘다.
   - 앞 50문항이 끝나면(약 1시간) `python -X utf8 score_gen.py --report g1 --first 50` 결과를 메시지로 한 번 남긴다.
   - 같은 행이 오류로 세 번 넘게 반복되면 그 오류를 기록하고 나머지를 계속한다(오류 행은 다음 실행에서 다시 시도된다).
4. 끝나면 `python -X utf8 score_gen.py --report g1` 와 `--first 50` 두 표를 최종 메시지에 그대로 붙이고,
   마지막으로 커밋·push 한다. 표 아래에 다음을 짧게 적는다:
   - 지금 방식(q3low) 대비 각 방식의 체크리스트 차이와 95% CI, 함정·할루시네이션
   - 한도 대기를 뺀 응답 시간(API 중앙·p90)과 질문당 토큰 차감
   - one_med·one_high 의 '잘림'(출력 상한 4096 에 걸린 호출) 수
   - 유형별로 눈에 띄는 차이(문항 수가 적으면 그렇다고)
