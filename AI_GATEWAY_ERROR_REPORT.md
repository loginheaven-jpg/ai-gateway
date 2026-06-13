# AI Gateway 장애 리포트 · 추정 원인 · 보완 권고

> 작성일: 2026-06-13 · 작성: 예봄성경(yebomcard) 클라이언트 측 · 수신: AI Gateway 개발팀
> 대상 서비스: `https://ai-gateway20251125.up.railway.app` (`POST /api/ai/chat`)
> 심각도: **Critical** — 장애 구간 동안 게이트웨이 사실상 전면 불가 → 의존하는 앱 AI 기능(주제추천 등) 동반 정지

---

## 0. TL;DR (요약)

장애 구간(2026-06-13 약 13:22~13:35 UTC 관측)에 **provider 폴백 체인 3단이 동시에 모두 실패**해 게이트웨이가 응답을 못 하거나(클라이언트 30s 타임아웃) 매우 느린(17~22s) 응답만 반환했습니다. 원인은 단일이 아니라 **3중 복합**입니다.

| # | provider | 증상 | 추정 원인 | 상태 |
|---|---|---|---|---|
| 1 | **gemini-flash** (1순위) | 매 요청 실패 → 폴백 | 게이트웨이의 gemini 연동 설정(모델명/키) 문제로 추정 *(아래 3-1)* | **미해결** |
| 2 | **OpenAI gpt-5.1** (2순위 폴백) | `429 insufficient_quota` | **OpenAI 계정 크레딧/결제 소진** (rate limit 아님) | **미해결** |
| 3 | **Claude haiku** (3순위 폴백) | `404 not_found: model: claude-haiku-4-6` | **존재하지 않는 모델 ID** | ✅ 4.5로 수정됨(보고 시점) |

→ 1·2가 실패한 상태에서 마지막 보루인 3까지 오타로 죽어 있어, **단일 결제 누락(OpenAI)이 게이트웨이 전체 다운으로 증폭**되었습니다.

---

## 1. 영향 (클라이언트 관측)

`provider: "gemini-flash"` 로 `/api/ai/chat` 호출 시, 장애 구간 동안 다음이 혼재:

- **30초+ 무응답(타임아웃)** — 세 provider를 순차로 모두 시도하다 누적 지연으로 클라이언트 타임아웃. 클라이언트는 "느림"과 "다운"을 구분할 수 없었음(에러 바디 없이 행).
- **17~22초 초저속 응답** — gpt-5.1 폴백으로 넘어간 경우(quota 완전 소진 전).
- **회복↔악화 반복(flaky)** — 약 1시간에 걸쳐 수 분 단위로 정상/비정상이 교차.
- claude-haiku 모델 ID를 4.5로 고친 뒤에는 **~1~2.5초 정상 응답**으로 회복 — 단 이는 **Claude가 대신 서빙**한 것이며 gemini-flash 자체는 계속 실패 중.

영향 받은 클라이언트 기능: 앱 실시간 AI 기능(주제추천 등) + 배치 작업(성경 본문 띄어쓰기 교정).

---

## 2. 증거 (게이트웨이 로그 · 클라이언트 probe)

### 2-1. OpenAI: `429 insufficient_quota` (rate limit 아님 — 결제 소진)
게이트웨이 로그(2026-06-13T13:25:33 UTC):
```
ERROR:app.services.chatgpt:[OPENAI ERROR] RateLimitError: Error code: 429 -
{'error': {'message': 'You exceeded your current quota, please check your plan and billing details. ...',
           'type': 'insufficient_quota', 'param': None, 'code': 'insufficient_quota'}}
```
- `code: insufficient_quota` 는 **분당 요청 초과(rate limit)가 아니라 계정 잔액/쿼터 소진**입니다.
- openai SDK가 **429를 2~3회 재시도(backoff ~0.4~0.9s)** 했으나, `insufficient_quota` 는 영구 오류라 재시도가 전부 무의미 → 요청당 1~2초를 낭비하고 폴백으로 넘어감.

### 2-2. Claude: `404 not_found — model: claude-haiku-4-6` (없는 모델 ID)
```
ERROR:app.services.claude:[CLAUDE ERROR] Status 404:
{"type":"error","error":{"type":"not_found_error","message":"model: claude-haiku-4-6"}}
WARNING:app.routers.ai:[FALLBACK] claude-haiku failed (... model: claude-haiku-4-6 ...), trying next...
```
- `claude-haiku-4-6` 은 **존재하지 않는 모델 ID**입니다. (Sonnet 4.6과 혼동 추정)
- 올바른 Haiku ID: **`claude-haiku-4-5-20251001`** (별칭 `claude-haiku-4-5`).
- 보고 시점에 4.5로 수정 → 정상 동작 확인(`model=claude-haiku-4-5-20251001`, ~1~2.5s).

### 2-3. gemini-flash (1순위): 매 요청 실패 → 폴백 발동
- 위 로그 구간은 이미 OpenAI·Anthropic로 **폴백한 상태**입니다 = gemini-flash가 이미 실패했다는 의미.
- 클라이언트 probe로 본 실제 서빙 모델(게이트웨이 응답 `model` 필드):
  - claude 수정 **전**: `model=gpt-5.1-2025-11-13` (gemini 실패 → openai)
  - claude 수정 **후**: `model=claude-haiku-4-5-20251001` (gemini 실패 → openai 실패 → claude)
  - **두 경우 모두 gemini-flash가 서빙한 적이 없음** → gemini-flash는 장애 구간 내내 실패.
- **중요 진단 단서**: 동일 작업을 **Google Gemini API에 직접 호출**(별도 키, `generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent`, `thinkingConfig.thinkingBudget=0`)하면 **200·~1s·정상 출력**입니다. 12 동시호출도 200×12(429 0).
  → **Google의 Gemini 서비스 자체는 정상**. 따라서 게이트웨이의 **gemini 연동 설정**(모델명/API 키/요청 형식) 문제일 가능성이 높습니다. *(게이트웨이 로그 상단의 gemini 호출 에러 본문 확인 필요 — 본 제보엔 미포함)*

---

## 3. 추정 원인

### 3-1. gemini-flash (1순위) — 게이트웨이 측 연동 문제 (최우선 조사)
Google Gemini 직접 호출은 정상이므로 Google 장애가 아님. 후보:
- **모델명 문제**: 게이트웨이가 preview/구 모델(예: `gemini-3-flash-preview` 류)을 호출 → preview 모델은 수시로 retire되어 404. 안정 별칭(`gemini-flash-latest`) 또는 현행 GA 모델명 사용 권장.
- **API 키/프로젝트**: 키 만료·무효·프로젝트 결제 미설정·리전 문제.
- **요청 형식**: thinking 모델로 라우팅되며 `maxOutputTokens` 가 추론에 소진돼 빈 응답(직접 호출에서 thinking ON·작은 토큰일 때 `finishReason=MAX_TOKENS`로 빈 답 재현됨 → 게이트웨이도 동일 가능).

### 3-2. OpenAI (2순위) — 결제/쿼터 소진
`insufficient_quota`. 계정 크레딧 소진 또는 결제수단 문제. 충전/오토리차지 전까지 영구 429.

### 3-3. Claude (3순위) — 모델 ID 오타 (해결됨)
`claude-haiku-4-6`(없음) → `claude-haiku-4-5-20251001`. 마지막 폴백이 오타로 죽어 전체 가용성 붕괴의 결정타.

### 3-4. 오케스트레이션 — 장애 증폭 구조
- **영구 오류에도 재시도**: `insufficient_quota`·`404`는 재시도해도 결과 동일인데 SDK 기본 재시도가 작동 → 요청당 지연 누적.
- **총 시도 시간 상한 없음**: gemini 실패 → openai(재시도 포함) → claude(재시도 포함)를 직렬로 모두 시도하다 **30s 클라이언트 타임아웃 초과** → 행처럼 보임.
- **구성 검증 부재**: 잘못된 모델 ID가 부팅·런타임에 걸러지지 않고 장애 시점에야 404로 드러남.

---

## 4. 보완 권고 (우선순위)

### A. 즉시 (서비스 복구)
1. **Claude 폴백 모델 ID 수정** — `claude-haiku-4-6` → `claude-haiku-4-5-20251001` *(완료됨, 확인 요망)*.
2. **OpenAI 결제 충전** + `insufficient_quota` 알림 설정.
3. **gemini-flash 연동 점검** — 호출 모델명을 안정 별칭(`gemini-flash-latest`)으로, 키/프로젝트/결제 확인. (직접 호출 정상이므로 설정 문제일 확률 높음)

### B. 단기 (장애 증폭 차단)
4. **에러 분류 + 영구 오류 즉시 폴오버**:
   - 영구(401/403/404/`insufficient_quota`) → **재시도 금지**, 즉시 다음 provider.
   - 일시(429 rate-limit·5xx·timeout) → 제한적 재시도(backoff).
5. **시간 예산(deadline) 도입**: provider별 상한(예 8~10s) + 요청 전체 상한(예 25s, 클라이언트 타임아웃 미만). 초과 시 **빠르게 503 + 구조화된 에러 바디** 반환(행 금지).
6. **구조화된 에러 응답**: 전 provider 실패 시 200/무응답 대신 `502/503` + `{ "error": "all_providers_failed", "tried": ["gemini-flash","openai","claude"], "last_reason": "..." }`. → 클라이언트가 "느림 vs 다운"을 구분·대응 가능.

### C. 견고화 (재발 방지)
7. **부팅 시 구성 검증**: 설정된 모든 모델 ID를 각 provider API로 핑(또는 허용목록 대조). 404 모델이 있으면 부팅 실패/강한 경고.
8. **헬스 엔드포인트**: `GET /health/providers` — provider별 마지막 성공/실패·모델 유효성(주기적 self-test). 클라이언트·모니터링이 사전 감지 가능.
9. **알림**: `insufficient_quota`, 반복 `model not_found(404)`, `all_providers_failed` 발생 시 즉시 알림.
10. **폴백 순서 재검토**: 비용·안정성 기준. 비싼 gpt-5.1이 1순위 폴백이라 quota에 가장 먼저 부딪힘 → `gemini-flash → claude-haiku → gpt` 처럼 저렴·안정 모델을 앞 폴백으로.
11. **응답 `model` 필드 유지**: 실제 서빙 모델을 응답에 노출하는 현 동작은 매우 유용 — 유지 권장(클라가 폴백 여부를 인지).

---

## 5. 참고 — 클라이언트(yebomcard) 측 임시 조치

게이트웨이 복구와 별개로 클라이언트는 다음으로 자체 완화함(게이트웨이 팀 참고용):
- **배치(띄어쓰기 교정)**: 게이트웨이 우회 → **Google Gemini API 직접 호출**로 전환(`gemini-flash-latest`, thinking off). 실패 절은 "처리완료" 표시하지 않고 **재시도 가능**하게 + 회복 시 **수렴 루프**로 자동 마무리.
- **앱 실시간 추천**: 잘림/간헐 실패 대비 라우트 재시도 보유. 단 **앱 실시간 AI 기능은 여전히 게이트웨이에 의존** → 게이트웨이 안정성이 곧 사용자 경험.

---

## 6. 부록 — 원문 로그 핵심 라인

```
2026-06-13T13:25:33Z ERROR app.services.chatgpt [OPENAI ERROR] RateLimitError 429
  code='insufficient_quota' message='You exceeded your current quota, please check your plan and billing details.'
2026-06-13T13:25:33Z ERROR app.services.claude  [CLAUDE ERROR] Status 404 {"type":"not_found_error","message":"model: claude-haiku-4-6"}
2026-06-13T13:25:33Z WARNING app.routers.ai     [FALLBACK] claude-haiku failed (... model: claude-haiku-4-6 ...), trying next...
2026-06-13T13:25:35Z ERROR app.routers.ai       [CHAT ERROR] RateLimitError 429 insufficient_quota
```
클라이언트 probe(요약): `provider=gemini-flash` 요청 → 응답 `model` 이 `gpt-5.1-2025-11-13`(claude 수정 전) / `claude-haiku-4-5-20251001`(수정 후). 전 구간 gemini-flash 서빙 0건. 직접 Gemini(`gemini-flash-latest`) 호출은 200·~1s 정상.
