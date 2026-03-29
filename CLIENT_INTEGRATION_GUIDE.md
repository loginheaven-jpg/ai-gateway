# AI Gateway — 서비스 연동 가이드

## 개요

AI Gateway는 AI 채팅과 음성인식(STT)을 **단일 API**로 제공합니다.
각 서비스는 API Key 없이, Gateway URL만으로 모든 AI 기능을 호출할 수 있습니다.

```
[교적부/기도의집/기타 서비스]  →  [AI Gateway]  →  [Claude/GPT/Gemini/Whisper/CLOVA]
        별칭만 전달                 API Key 관리        실제 AI 호출
```

---

## 환경변수 (클라이언트 서비스)

```env
AI_GATEWAY_URL=https://ai-gateway20251125.up.railway.app
```

**이것만 있으면 됩니다.** AI API Key는 불필요합니다.

---

## 1. Chat API — AI 채팅

### 엔드포인트

```
POST {AI_GATEWAY_URL}/api/ai/chat
Content-Type: application/json
```

### 사용 가능한 provider 별칭

| 별칭 | 엔진 | 특징 |
|------|------|------|
| `claude-sonnet` | Claude Sonnet 4.5 | 고성능 분석/작성 **(기본값)** |
| `claude-haiku` | Claude Haiku 4.5 | 빠른 응답, 경량 작업 |
| `chatgpt` | GPT 5.1 | 범용 AI |
| `gemini-pro` | Gemini 3 Pro | 고성능 분석 |
| `gemini-flash` | Gemini 2.5 Flash | 빠른 응답 |
| `moonshot` | Kimi K2 | 중국어 특화 |
| `perplexity` | Sonar Pro | 웹 검색 + AI 답변 |

### 기본 호출 (provider 미지정 → 기본값 자동)

```typescript
const res = await fetch(`${AI_GATEWAY_URL}/api/ai/chat`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    messages: [{ role: 'user', content: '안녕하세요' }]
  })
});
const data = await res.json();
console.log(data.content);  // AI 응답 텍스트
```

### 특정 provider 지정

```typescript
const res = await fetch(`${AI_GATEWAY_URL}/api/ai/chat`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    provider: 'chatgpt',    // ← 별칭 지정
    messages: [{ role: 'user', content: '안녕하세요' }]
  })
});
```

### 전체 파라미터

```json
{
  "provider": "claude-sonnet",       // (선택) 미지정 시 기본값
  "messages": [                       // (필수) 대화 배열
    { "role": "user", "content": "질문" }
  ],
  "system_prompt": "당신은 목사입니다.", // (선택) 시스템 프롬프트
  "max_tokens": 4096,                 // (선택) 최대 토큰
  "temperature": 0.7,                 // (선택) 창의성 (0~1)
  "use_fallback": true,               // (선택) 실패 시 자동 대체
  "use_cache": true,                   // (선택) 동일 요청 캐시
  "caller": "saint-record:memo"        // (선택) 호출자 식별 (사용량 추적)
}
```

### 응답 형식

```json
{
  "content": "안녕하세요! 무엇을 도와드릴까요?",
  "model": "claude-sonnet-4-5",
  "provider": "claude-sonnet",
  "usage": {
    "input_tokens": 15,
    "output_tokens": 25
  }
}
```

### Fallback (자동 대체)

1차 프로바이더가 실패하면 자동으로 다른 엔진을 시도합니다.

| 1차 | → 2차 → 3차 |
|-----|-------------|
| claude-sonnet | claude-haiku → chatgpt → gemini-pro |
| chatgpt | claude-sonnet → gemini-pro |
| gemini-pro | gemini-flash → claude-sonnet → chatgpt |

응답의 `provider` 필드로 실제 사용된 엔진을 확인할 수 있습니다.

---

## 2. Streaming API — 실시간 스트리밍

### 엔드포인트

```
POST {AI_GATEWAY_URL}/api/ai/chat/stream
Content-Type: application/json
```

Request Body는 Chat API와 동일합니다.

### 응답 (SSE)

```
data: {"text": "안녕"}
data: {"text": "하세요"}
data: {"text": "!"}
data: {"done": true}
```

### JavaScript 연동

```typescript
const res = await fetch(`${AI_GATEWAY_URL}/api/ai/chat/stream`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    messages: [{ role: 'user', content: '이야기 해줘' }]
  })
});

const reader = res.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const lines = decoder.decode(value).split('\n');
  for (const line of lines) {
    if (!line.startsWith('data: ')) continue;
    const data = JSON.parse(line.slice(6));
    if (data.text) process.stdout.write(data.text);
    if (data.done) console.log('\n[완료]');
  }
}
```

---

## 3. STT API — 음성 → 텍스트

### 엔드포인트

```
POST {AI_GATEWAY_URL}/api/ai/stt
Content-Type: multipart/form-data
```

### 사용 가능한 provider 별칭

| 별칭 | 엔진 | 특징 |
|------|------|------|
| `whisper` | OpenAI Whisper | 다국어, 최대 25MB **(기본값)** |
| `clova-csr` | Naver CLOVA CSR | 한국어 특화, 최대 60초, 빠름 |
| `clova-speech` | Naver CLOVA Speech Long | 최대 80분, 화자분리 |

### 기본 호출 (provider 미지정 → 기본값 자동)

```typescript
const formData = new FormData();
formData.append('file', audioBlob, 'audio.webm');

const res = await fetch(`${AI_GATEWAY_URL}/api/ai/stt`, {
  method: 'POST',
  body: formData,
});
const data = await res.json();
console.log(data.text);  // "인식된 텍스트"
```

### 특정 provider 지정

```typescript
const formData = new FormData();
formData.append('file', audioBlob, 'audio.webm');
formData.append('provider', 'clova-csr');  // ← 별칭 지정

const res = await fetch(`${AI_GATEWAY_URL}/api/ai/stt`, {
  method: 'POST',
  body: formData,
});
```

### 전체 파라미터

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `file` | File | **O** | - | 오디오 파일 |
| `language` | string | X | `ko` | 언어 (`ko`, `en`, `ja`, `zh`) |
| `provider` | string | X | 기본값 | STT 엔진 별칭 |
| `caller` | string | X | - | 호출자 식별 |

### 응답 형식

```json
{
  "text": "기도해 주셔서 감사합니다.",
  "language": "ko",
  "duration_sec": 12.5,
  "provider": "whisper",
  "model": "whisper-1",
  "elapsed_ms": 3200
}
```

### 지원 오디오 포맷

| 포맷 | Whisper | CLOVA CSR | CLOVA Speech |
|------|---------|-----------|--------------|
| webm | O | X | X |
| mp4 | O | O | O |
| mp3 | O | O | O |
| wav | O | O | O |
| ogg | O | O | O |

> 브라우저 녹음(webm)은 **Whisper만** 지원합니다.

---

## 4. 재사용 유틸 함수 (TypeScript)

서비스 코드에 복사하여 사용하세요.

```typescript
const AI_GATEWAY_URL = process.env.AI_GATEWAY_URL
  || 'https://ai-gateway20251125.up.railway.app';

// ─── Chat ─────────────────────────────────────────────────

interface ChatResult {
  content: string;
  model: string;
  provider: string;
  usage: { input_tokens: number; output_tokens: number };
}

export async function callAI(
  messages: { role: string; content: string }[],
  options?: {
    provider?: string;
    system_prompt?: string;
    max_tokens?: number;
    temperature?: number;
    caller?: string;
  }
): Promise<ChatResult> {
  const res = await fetch(`${AI_GATEWAY_URL}/api/ai/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      messages,
      ...options,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown' }));
    throw new Error(`AI_ERROR: ${res.status} ${err.detail}`);
  }

  return res.json();
}

// ─── STT ──────────────────────────────────────────────────

interface STTResult {
  text: string;
  language: string;
  duration_sec: number;
  provider: string;
  model: string;
  elapsed_ms: number;
}

export async function callSTT(
  file: File | Blob,
  options?: {
    language?: string;
    provider?: string;
    caller?: string;
  }
): Promise<STTResult> {
  const formData = new FormData();
  const ext = (file as File).name?.split('.').pop() || 'webm';
  formData.append('file', file, `audio.${ext}`);

  if (options?.language) formData.append('language', options.language);
  if (options?.provider) formData.append('provider', options.provider);
  if (options?.caller) formData.append('caller', options.caller);

  const res = await fetch(`${AI_GATEWAY_URL}/api/ai/stt`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Unknown' }));
    throw new Error(`STT_ERROR: ${res.status} ${err.error}`);
  }

  return res.json();
}
```

### 호출 예시

```typescript
// ─── Chat ─────────────────────────────────────────────

// 기본 (provider 미지정)
const { content } = await callAI([
  { role: 'user', content: '성경 구절 추천해줘' }
]);

// Claude Sonnet + 시스템 프롬프트
const { content } = await callAI(
  [{ role: 'user', content: '오늘의 묵상' }],
  {
    provider: 'claude-sonnet',
    system_prompt: '당신은 목사입니다.',
    caller: 'saint-record:devotion',
  }
);

// ChatGPT 지정
const { content } = await callAI(
  [{ role: 'user', content: '요약해줘' }],
  { provider: 'chatgpt' }
);

// Gemini Flash (빠른 응답)
const { content } = await callAI(
  [{ role: 'user', content: '간단히 답해줘' }],
  { provider: 'gemini-flash' }
);

// ─── STT ──────────────────────────────────────────────

// 기본 (provider 미지정)
const { text } = await callSTT(audioBlob);

// CLOVA CSR 지정
const { text } = await callSTT(audioBlob, {
  provider: 'clova-csr',
  caller: 'prayer-house:encouragement',
});
```

---

## 5. Python 연동

```python
import requests

AI_GATEWAY_URL = "https://ai-gateway20251125.up.railway.app"

# ─── Chat ────────────────────────────────────────
def call_ai(messages, provider=None, system_prompt=None, caller=None):
    body = {"messages": messages}
    if provider:
        body["provider"] = provider
    if system_prompt:
        body["system_prompt"] = system_prompt
    if caller:
        body["caller"] = caller

    res = requests.post(f"{AI_GATEWAY_URL}/api/ai/chat", json=body)
    res.raise_for_status()
    return res.json()

# 기본 호출
result = call_ai([{"role": "user", "content": "안녕하세요"}])
print(result["content"])

# ChatGPT 지정
result = call_ai(
    [{"role": "user", "content": "요약해줘"}],
    provider="chatgpt"
)

# ─── STT ─────────────────────────────────────────
def call_stt(file_path, provider=None, language="ko", caller=None):
    data = {}
    if provider:
        data["provider"] = provider
    if language:
        data["language"] = language
    if caller:
        data["caller"] = caller

    with open(file_path, "rb") as f:
        res = requests.post(
            f"{AI_GATEWAY_URL}/api/ai/stt",
            files={"file": f},
            data=data,
        )
    res.raise_for_status()
    return res.json()

# 기본 호출
result = call_stt("recording.webm")
print(result["text"])
```

---

## 6. cURL 예시

### Chat

```bash
# 기본 (provider 미지정)
curl -X POST https://ai-gateway20251125.up.railway.app/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "안녕"}]}'

# ChatGPT 지정
curl -X POST https://ai-gateway20251125.up.railway.app/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{"provider": "chatgpt", "messages": [{"role": "user", "content": "안녕"}]}'
```

### STT

```bash
# 기본 (provider 미지정)
curl -X POST https://ai-gateway20251125.up.railway.app/api/ai/stt \
  -F "file=@recording.webm"

# CLOVA CSR 지정
curl -X POST https://ai-gateway20251125.up.railway.app/api/ai/stt \
  -F "file=@recording.webm" \
  -F "provider=clova-csr" \
  -F "language=ko"
```

---

## 7. 에러 처리

### Chat 에러

```json
{ "detail": "Provider not found: unknown" }
```

### STT 에러

```json
{ "error": "File too large: 15000000 bytes (max 10MB)", "code": "FILE_TOO_LARGE" }
```

| STT 에러 코드 | 상황 |
|------------|------|
| `UNSUPPORTED_FORMAT` | 지원하지 않는 오디오 포맷 |
| `FILE_TOO_LARGE` | 파일 크기 초과 (10MB) |
| `INVALID_FILE` | 빈 파일 |
| `PROVIDER_ERROR` | 모든 STT 엔진 실패 |

---

## 8. 요약

| 기능 | 엔드포인트 | provider 미지정 시 |
|------|-----------|-------------------|
| AI 채팅 | `POST /api/ai/chat` | 기본 Chat 엔진 (claude-sonnet) |
| 스트리밍 | `POST /api/ai/chat/stream` | 기본 Chat 엔진 |
| 음성인식 | `POST /api/ai/stt` | 기본 STT 엔진 (whisper) |

**원칙: provider를 지정하지 않으면 Gateway 기본값이 적용됩니다.**
기본값은 Admin 대시보드에서 언제든 변경할 수 있으며, 클라이언트 코드 수정은 불필요합니다.
