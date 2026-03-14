# AI Gateway - Architecture & API Specification

## Overview

AI Gateway는 여러 AI 프로바이더(Claude, OpenAI, Gemini, Moonshot, Perplexity)를 통합 관리하는 중앙 집중형 게이트웨이 서비스입니다. 다른 서비스에서 단일 API로 다양한 AI 모델을 호출할 수 있습니다.

## Tech Stack

| 항목 | 기술 |
|------|------|
| Framework | FastAPI (Python) |
| Database | PostgreSQL (운영) / SQLite (로컬) |
| HTTP Client | httpx, openai SDK, google-genai SDK |
| Deployment | Heroku (Procfile) |
| Port | 9000 (로컬) / $PORT (운영) |

## Project Structure

```
ai-gateway/
├── app/
│   ├── main.py              # FastAPI 앱 초기화, CORS, 라우터 등록
│   ├── config.py             # 프로바이더 설정 관리 (DB/JSON/ENV fallback)
│   ├── routers/
│   │   ├── ai.py             # /api/ai/* - 채팅 API 엔드포인트
│   │   └── settings.py       # /api/settings/* - 설정 관리 엔드포인트
│   └── services/
│       ├── base.py           # AIService 추상 클래스
│       ├── claude.py         # Anthropic Claude (Sonnet/Haiku)
│       ├── chatgpt.py        # OpenAI GPT
│       ├── gemini.py         # Google Gemini (Pro/Flash)
│       ├── moonshot.py       # Moonshot Kimi
│       └── perplexity.py     # Perplexity (검색 특화)
├── frontend/
│   └── admin.html            # 관리자 대시보드
├── Procfile                  # Heroku 배포 설정
└── requirements.txt          # Python 의존성
```

## Providers

| Provider ID | 이름 | 기본 모델 | 용도 |
|-------------|------|-----------|------|
| `claude-sonnet` | Claude Sonnet (Anthropic) | claude-sonnet-4-5 | 고성능 분석/작성 |
| `claude-haiku` | Claude Haiku (Anthropic) | claude-haiku-4-5-20251001 | 빠른 응답/경량 작업 |
| `openai` | GPT (OpenAI) | gpt-5.1 | 범용 AI |
| `gemini-pro` | Gemini Pro (Google) | gemini-3-pro-preview | 고성능 분석 |
| `gemini-flash` | Gemini Flash (Google) | gemini-2.5-flash | 빠른 응답 |
| `moonshot` | Moonshot (Kimi) | kimi-k2-0905-preview | 중국어 특화 |
| `perplexity` | Perplexity | sonar-pro | 웹 검색 + AI 답변 |

---

# API Specification

## Base URL

```
운영: https://<your-domain>
로컬: http://localhost:9000
```

## 공통 사항

- Content-Type: `application/json`
- 인증: 현재 없음 (서비스 간 내부 통신 전제)
- CORS: 모든 origin 허용

---

## 1. Chat API

### `POST /api/ai/chat`

AI 프로바이더에 채팅 요청을 보냅니다.

**Request Body:**

```json
{
  "provider": "claude-sonnet",       // (선택) 미지정 시 기본 프로바이더 사용
  "messages": [                       // (필수) 대화 메시지 배열
    { "role": "user", "content": "안녕하세요" },
    { "role": "assistant", "content": "안녕하세요! 무엇을 도와드릴까요?" },
    { "role": "user", "content": "날씨 알려줘" }
  ],
  "system_prompt": "당신은 친절한 비서입니다.",  // (선택) 시스템 프롬프트
  "max_tokens": 4096,                 // (선택, 기본: 4096)
  "temperature": 0.7                  // (선택, 기본: 0.7)
}
```

**Response (200):**

```json
{
  "content": "오늘 서울 날씨는...",
  "model": "claude-sonnet-4-5",
  "provider": "claude-sonnet",
  "usage": {
    "input_tokens": 150,
    "output_tokens": 300
  },
  "citations": null
}
```

> `citations`는 Perplexity 프로바이더 사용 시에만 URL 배열로 반환됩니다.

**Error Response (4xx/5xx):**

```json
{
  "detail": "Provider not found: unknown-provider"
}
```

**사용 가능한 provider 값:**
`claude-sonnet`, `claude-haiku`, `openai`, `gemini-pro`, `gemini-flash`, `moonshot`, `perplexity`

---

### `POST /api/ai/batch-chat`

여러 프로바이더에 동시에 테스트 요청을 보냅니다.

**Request Body:**

```json
{
  "providers": ["claude-sonnet", "claude-haiku", "openai"],
  "test_message": "Hello, please respond briefly.",  // (선택)
  "max_tokens": 100,          // (선택, 기본: 100)
  "temperature": 0.7          // (선택, 기본: 0.7)
}
```

**Response (200):**

```json
{
  "results": [
    {
      "provider": "claude-sonnet",
      "success": true,
      "response": "안녕하세요!",
      "model": "claude-sonnet-4-5",
      "elapsed_ms": 1200
    },
    {
      "provider": "openai",
      "success": false,
      "error": "API key not configured for: openai",
      "elapsed_ms": 5
    }
  ]
}
```

---

### `GET /api/ai/providers`

활성화된 프로바이더 목록을 조회합니다.

**Response (200):**

```json
{
  "providers": [
    {
      "id": "claude-sonnet",
      "name": "Claude Sonnet (Anthropic)",
      "model": "claude-sonnet-4-5",
      "enabled": true,
      "has_api_key": true,
      "is_default": true
    }
  ],
  "default": "claude-sonnet"
}
```

---

## 2. Settings API

### `GET /api/settings/providers`

전체 프로바이더 설정 조회 (API Key 마스킹됨)

### `GET /api/settings/provider/{provider_id}`

특정 프로바이더 설정 조회

### `PUT /api/settings/provider/{provider_id}`

프로바이더 설정 수정

```json
{
  "api_key": "sk-...",     // (선택)
  "model": "claude-sonnet-4-5",   // (선택)
  "enabled": true          // (선택)
}
```

### `PUT /api/settings/default-provider`

기본 프로바이더 변경

```json
{
  "provider": "claude-haiku"
}
```

### `POST /api/settings/reset`

모든 프로바이더를 기본값으로 초기화

### `GET /api/settings/export`

전체 설정 JSON 내보내기

### `POST /api/settings/import`

JSON 설정 가져오기

---

## 3. System Endpoints

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/` | 관리자 대시보드 (HTML) |
| `GET` | `/health` | 헬스체크 (`{"status": "healthy"}`) |
| `GET` | `/docs` | FastAPI Swagger UI |

---

## 연동 예시

### Python (requests)

```python
import requests

response = requests.post("https://<gateway-url>/api/ai/chat", json={
    "provider": "claude-sonnet",
    "messages": [{"role": "user", "content": "안녕하세요"}],
    "system_prompt": "친절하게 답변해주세요.",
    "max_tokens": 2000
})

data = response.json()
print(data["content"])
```

### JavaScript (fetch)

```javascript
const response = await fetch('https://<gateway-url>/api/ai/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    provider: 'claude-haiku',
    messages: [{ role: 'user', content: '요약해줘' }],
    max_tokens: 1000
  })
});

const data = await response.json();
console.log(data.content);
```

### cURL

```bash
curl -X POST https://<gateway-url>/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "claude-sonnet",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 500
  }'
```

---

## Environment Variables

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `ANTHROPIC_API_KEY` | Anthropic API 키 | - |
| `CLAUDE_SONNET_MODEL` | Claude Sonnet 모델 | claude-sonnet-4-5 |
| `CLAUDE_HAIKU_MODEL` | Claude Haiku 모델 | claude-haiku-4-5-20251001 |
| `OPENAI_API_KEY` | OpenAI API 키 | - |
| `OPENAI_MODEL` | OpenAI 모델 | gpt-5.1 |
| `GOOGLE_API_KEY` | Google AI API 키 | - |
| `GEMINI_PRO_MODEL` | Gemini Pro 모델 | gemini-3-pro-preview |
| `GEMINI_FLASH_MODEL` | Gemini Flash 모델 | gemini-2.5-flash |
| `MOONSHOT_API_KEY` | Moonshot API 키 | - |
| `MOONSHOT_MODEL` | Moonshot 모델 | kimi-k2-0905-preview |
| `PERPLEXITY_API_KEY` | Perplexity API 키 | - |
| `PERPLEXITY_MODEL` | Perplexity 모델 | sonar-pro |
| `DEFAULT_AI_PROVIDER` | 기본 프로바이더 | claude-sonnet |
| `DATABASE_URL` | PostgreSQL 연결 URL | (SQLite fallback) |
