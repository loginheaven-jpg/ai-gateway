# AI Gateway - Architecture & API Specification

## Overview

AI Gateway는 여러 AI 프로바이더(Claude, ChatGPT, Gemini, Moonshot, Perplexity)와 STT 프로바이더(Whisper, CLOVA Speech)를 통합 관리하는 중앙 집중형 게이트웨이 서비스입니다. 다른 서비스에서 단일 API로 다양한 AI 모델(텍스트 + Vision 이미지 분석)과 음성인식(STT)을 호출할 수 있습니다.

## Tech Stack

| 항목 | 기술 |
|------|------|
| Framework | FastAPI (Python) |
| Database | PostgreSQL (운영) / SQLite (로컬) |
| HTTP Client | httpx, openai SDK, google-genai SDK |
| Deployment | Railway (GitHub 자동 배포) |
| 운영 URL | https://ai-gateway20251125.up.railway.app |
| Port | 9000 (로컬) / $PORT (운영) |

## Project Structure

```
ai-gateway/
├── app/
│   ├── main.py              # FastAPI 앱 초기화, CORS, 라우터 등록
│   ├── config.py             # 프로바이더 설정 관리 (DB/JSON/ENV fallback)
│   ├── usage.py              # 사용량 로깅 모듈 (DB 기반)
│   ├── cache.py              # 인메모리 응답 캐시 (TTL 기반)
│   ├── routers/
│   │   ├── ai.py             # /api/ai/* - 채팅/스트리밍 API (fallback 지원)
│   │   ├── stt.py            # /api/ai/stt - STT API (Whisper/CLOVA, fallback)
│   │   └── settings.py       # /api/settings/* - 설정/통계/캐시 관리
│   └── services/
│       ├── base.py           # AIService 추상 클래스 (chat + stream)
│       ├── stt_base.py       # STTService 추상 클래스 (recognize)
│       ├── claude.py         # Anthropic Claude (Sonnet/Haiku, 네이티브 스트리밍)
│       ├── chatgpt.py        # OpenAI GPT (네이티브 스트리밍)
│       ├── gemini.py         # Google Gemini (Pro/Flash)
│       ├── moonshot.py       # Moonshot Kimi
│       ├── perplexity.py     # Perplexity (검색 특화)
│       ├── whisper.py        # OpenAI Whisper (STT)
│       ├── clova_csr.py      # Naver CLOVA CSR (STT, 단시간)
│       └── clova_stt.py      # Naver CLOVA Speech Long (STT, 장시간)
├── frontend/
│   └── admin.html            # 관리자 대시보드 (통계/캐시/스트리밍 UI 포함)
├── Procfile                  # Heroku 배포 설정
└── requirements.txt          # Python 의존성
```

## Providers

| Provider ID | 이름 | 기본 모델 | 용도 |
|-------------|------|-----------|------|
| `claude-sonnet` | Claude Sonnet (Anthropic) | claude-sonnet-4-5 | 고성능 분석/작성 |
| `claude-haiku` | Claude Haiku (Anthropic) | claude-haiku-4-5-20251001 | 빠른 응답/경량 작업 |
| `chatgpt` | ChatGPT (OpenAI) | gpt-5.1 | 범용 AI |
| `gemini-pro` | Gemini Pro (Google) | gemini-3-pro-preview | 고성능 분석 |
| `gemini-flash` | Gemini Flash (Google) | gemini-2.5-flash | 빠른 응답 |
| `moonshot` | Moonshot (Kimi) | kimi-k2-0905-preview | 중국어 특화 |
| `perplexity` | Perplexity | sonar-pro | 웹 검색 + AI 답변 |

## STT Providers (Speech-to-Text)

| Provider ID | 이름 | 기본 모델 | 최대 길이 | 용도 |
|-------------|------|-----------|-----------|------|
| `whisper` | Whisper (OpenAI) | whisper-1 | 25MB | 범용 음성인식 (다국어) |
| `clova-csr` | CLOVA CSR (Naver, Short) | clova-csr | 60초 | 한국어 특화, 빠른 인식 |
| `clova-speech` | CLOVA Speech (Naver, Long) | clova-speech-long | 80분 | 장시간 오디오, 화자분리 |

---

# API Specification

## Base URL

```
운영: https://ai-gateway20251125.up.railway.app
로컬: http://localhost:9000
```

## 공통 사항

- Content-Type: `application/json`
- 인증: 현재 없음 (서비스 간 내부 통신 전제)
- CORS: 모든 origin 허용

---

## 1. Chat API

### `POST /api/ai/chat`

AI 프로바이더에 채팅 요청을 보냅니다. **Fallback 체인**, **응답 캐싱**, **사용량 로깅**이 자동 적용됩니다.

**Request Body:**

```json
{
  "provider": "claude-sonnet",       // (선택) 미지정 시 기본 프로바이더 사용
  "messages": [                       // (필수) 대화 메시지 배열
    { "role": "user", "content": "안녕하세요" },           // content: string (텍스트)
    { "role": "user", "content": [                          // content: array (Vision)
      { "type": "image", "source": { "type": "base64", "media_type": "image/jpeg", "data": "..." } },
      { "type": "text", "text": "이 이미지를 분석해줘" }
    ]}
  ],
  "system_prompt": "당신은 친절한 비서입니다.",  // (선택) 시스템 프롬프트
  "max_tokens": 4096,                 // (선택, 기본: 4096)
  "temperature": 0.7,                 // (선택, 기본: 0.7)
  "use_fallback": true,               // (선택, 기본: true) 실패 시 자동 대체
  "use_cache": true,                   // (선택, 기본: true) 동일 요청 캐시
  "caller": "saint-record"             // (선택) 호출자 식별 (사용량 추적용)
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
`claude-sonnet`, `claude-haiku`, `chatgpt`, `gemini-pro`, `gemini-flash`, `moonshot`, `perplexity`

**Fallback 체인 (기본):**

| Primary | Fallback 순서 |
|---------|---------------|
| claude-sonnet | claude-haiku → chatgpt → gemini-pro |
| claude-haiku | claude-sonnet → chatgpt → gemini-flash |
| chatgpt | claude-sonnet → gemini-pro |
| gemini-pro | gemini-flash → claude-sonnet → chatgpt |
| gemini-flash | gemini-pro → claude-haiku → chatgpt |
| moonshot | claude-sonnet → chatgpt |
| perplexity | chatgpt → claude-sonnet |

---

### `POST /api/ai/chat/stream`

SSE(Server-Sent Events)로 응답을 스트리밍합니다. Request Body는 `/api/ai/chat`과 동일합니다.

**Response (SSE stream):**

```
data: {"text": "안녕"}
data: {"text": "하세요"}
data: {"text": "!"}
data: {"done": true}
```

> 에러 발생 시: `data: {"error": "메시지"}`
> Claude와 OpenAI는 네이티브 스트리밍, 나머지는 전체 응답 후 단일 청크로 전송됩니다.

**JavaScript 연동 예시:**

```javascript
const response = await fetch('/api/ai/chat/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    provider: 'claude-sonnet',
    messages: [{ role: 'user', content: '이야기 해줘' }]
  })
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const lines = decoder.decode(value).split('\n');
  for (const line of lines) {
    if (!line.startsWith('data: ')) continue;
    const data = JSON.parse(line.slice(6));
    if (data.text) process.stdout.write(data.text);
  }
}
```

---

### `POST /api/ai/batch-chat`

여러 프로바이더에 동시에 테스트 요청을 보냅니다.

**Request Body:**

```json
{
  "providers": ["claude-sonnet", "claude-haiku", "chatgpt"],
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
      "provider": "chatgpt",
      "success": false,
      "error": "API key not configured for: chatgpt",
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

### `GET /api/settings/usage/stats?days=7&provider=claude-sonnet`

사용량 통계 조회. `days`(기간), `provider`(필터) 파라미터 지원.

```json
{
  "period_days": 7,
  "total": { "total_requests": 150, "total_input_tokens": 50000, "total_output_tokens": 80000, "avg_elapsed_ms": 2500 },
  "by_provider": [
    { "provider": "claude-sonnet", "request_count": 80, "total_input_tokens": 30000, "total_output_tokens": 50000, "avg_elapsed_ms": 3000, "success_count": 78, "error_count": 2 }
  ],
  "daily": [
    { "date": "2026-03-14", "request_count": 25, "total_input_tokens": 8000, "total_output_tokens": 12000 }
  ]
}
```

### `GET /api/settings/usage/logs?limit=50`

최근 사용 로그 조회.

### `GET /api/settings/cache/stats`

캐시 통계 (hit/miss, hit rate, 엔트리 수 등).

### `POST /api/settings/cache/clear`

캐시 전체 삭제.

---

## 3. STT API (Speech-to-Text)

### `POST /api/ai/stt`

오디오 파일을 텍스트로 변환합니다. Whisper ↔ CLOVA 간 **Fallback**이 자동 적용됩니다.

**Request (multipart/form-data):**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `file` | File | O | 오디오 파일 (webm, mp4, mp3, wav, ogg, m4a, flac) |
| `language` | string | X | 언어 코드 (기본: `ko`). ISO 639-1 |
| `provider` | string | X | STT 프로바이더 (기본: `DEFAULT_STT_PROVIDER` 또는 `whisper`) |
| `caller` | string | X | 호출 서비스 식별자 (예: `prayer-house:encouragement`) |

**Response (200):**

```json
{
  "text": "인식된 텍스트",
  "language": "ko",
  "duration_sec": 12.5,
  "provider": "whisper",
  "model": "whisper-1",
  "elapsed_ms": 3200
}
```

**Error Response (4xx/5xx):**

```json
{
  "error": "File too large: 15000000 bytes (max 10MB)",
  "code": "FILE_TOO_LARGE"
}
```

**에러 코드:** `UNSUPPORTED_FORMAT`, `FILE_TOO_LARGE`, `INVALID_FILE`, `PROVIDER_ERROR`

**Fallback 체인:**

| Primary | Fallback 순서 |
|---------|---------------|
| whisper | clova-csr → clova-speech |
| clova-csr | whisper → clova-speech |
| clova-speech | whisper → clova-csr |

**클라이언트 호출 예시 (JavaScript):**

```javascript
const formData = new FormData();
formData.append('file', audioBlob, 'audio.webm');
formData.append('language', 'ko');
formData.append('caller', 'my-service');

const res = await fetch('https://<gateway-url>/api/ai/stt', {
  method: 'POST',
  body: formData
});
const data = await res.json();
console.log(data.text);
```

**cURL:**

```bash
curl -X POST https://<gateway-url>/api/ai/stt \
  -F "file=@recording.webm" \
  -F "language=ko" \
  -F "caller=test"
```

---

### `GET /api/ai/stt/providers`

STT 프로바이더 목록을 조회합니다.

```json
{
  "providers": [
    { "id": "whisper", "name": "Whisper (OpenAI)", "model": "whisper-1", "enabled": true, "has_api_key": true, "is_default": true }
  ],
  "default": "whisper"
}
```

---

## 4. System Endpoints

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
| `DEFAULT_AI_PROVIDER` | 기본 Chat 프로바이더 | claude-sonnet |
| `DATABASE_URL` | PostgreSQL 연결 URL | (SQLite fallback) |
| `CLOVA_CSR_CLIENT_ID` | CLOVA CSR Client ID | - |
| `CLOVA_CSR_CLIENT_SECRET` | CLOVA CSR Client Secret | - |
| `CLOVA_SPEECH_SECRET_KEY` | CLOVA Speech Long Secret Key | - |
| `CLOVA_SPEECH_INVOKE_URL` | CLOVA Speech Long Invoke URL | - |
| `DEFAULT_STT_PROVIDER` | 기본 STT 프로바이더 | whisper |
| `STT_MAX_FILE_SIZE_MB` | STT 최대 파일 크기 | 10 |
