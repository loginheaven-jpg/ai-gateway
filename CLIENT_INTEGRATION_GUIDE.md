# AI Gateway — 서비스 연동 가이드

## 개요

AI Gateway는 AI 채팅, Vision(이미지 분석), 음성인식(STT)을 **단일 API**로 제공합니다.
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

## 3. Vision API — 이미지 분석

기존 Chat API(`/api/ai/chat`)에 이미지를 포함하여 호출합니다. **별도 엔드포인트 없이** content 배열로 전달합니다.

### Vision 지원 프로바이더

| 별칭 | Vision 지원 |
|------|------------|
| `claude-sonnet` | O |
| `claude-haiku` | O (권장 — 빠르고 저렴) |
| `chatgpt` | O |
| `gemini-pro` | O |
| `gemini-flash` | O |
| `moonshot` | X (자동 fallback) |
| `perplexity` | X (자동 fallback) |

### 호출 예시 (영수증 분석)

```typescript
const imageBase64 = await fileToBase64(receiptFile);  // 300KB 이하 권장

const res = await fetch(`${AI_GATEWAY_URL}/api/ai/chat`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    provider: 'claude-haiku',
    messages: [{
      role: 'user',
      content: [
        {
          type: 'image',
          source: {
            type: 'base64',
            media_type: 'image/jpeg',
            data: imageBase64
          }
        },
        {
          type: 'text',
          text: '이 영수증의 금액, 가맹점명, 날짜를 JSON으로 추출해줘'
        }
      ]
    }],
    max_tokens: 500,
    caller: 'church-finance:receipt-verify'
  })
});

const data = await res.json();
console.log(data.content);
// {"amount": 25000, "store": "이마트", "date": "2026-03-28"}
```

### cURL

```bash
# IMAGE_B64 변수에 base64 인코딩된 이미지 데이터
curl -X POST https://ai-gateway20251125.up.railway.app/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "claude-haiku",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "'$IMAGE_B64'"}},
        {"type": "text", "text": "이 이미지를 설명해줘"}
      ]
    }],
    "max_tokens": 500
  }'
```

### 주의사항
- 이미지는 **base64**로 인코딩하여 전달 (URL 방식 미지원)
- 이미지 크기: **300KB 이하** 권장 (압축 후)
- Vision 요청은 **캐시되지 않음** (자동 스킵)
- Moonshot/Perplexity로 보내면 자동으로 Claude/ChatGPT로 fallback

---

## 4. Image Generation API — 이미지 생성

### 엔드포인트

```
POST {AI_GATEWAY_URL}/api/ai/image
Content-Type: application/json
```

### 사용 가능한 provider 별칭

| 별칭 | 엔진 | 특징 |
|------|------|------|
| `dall-e` | DALL-E 3 (OpenAI) | 범용, 프롬프트 자동 개선 **(기본값)** |
| `imagen` | Imagen 3 (Google) | 풍경/자연 고품질 |

### 호출 예시

```typescript
const res = await fetch(`${AI_GATEWAY_URL}/api/ai/image`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    prompt: 'A serene mountain landscape at golden dawn with soft clouds',
    size: '1080x1350',
    style: 'natural',
    caller: 'yebom-card:background'
  })
});

const data = await res.json();
// data.data = base64 이미지 데이터
// data.media_type = "image/png"

const imgSrc = `data:${data.media_type};base64,${data.data}`;
```

### 전체 파라미터

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `prompt` | string | **O** | - | 영문 이미지 생성 프롬프트 |
| `size` | string | X | `1024x1024` | 이미지 크기 |
| `style` | string | X | `natural` | `natural` / `vivid` / `artistic` |
| `provider` | string | X | 기본값 | 이미지 엔진 별칭 |
| `caller` | string | X | - | 호출자 식별 |

### 지원 사이즈

| 사이즈 | DALL-E 3 | Imagen 3 |
|--------|----------|----------|
| `1024x1024` | O (1:1) | O (1:1) |
| `1080x1350` | O → 1024x1792 | O (3:4) |
| `1792x1024` | O (16:9) | O (16:9) |
| `1024x1792` | O (9:16) | O (9:16) |

### 응답 형식

```json
{
  "data": "iVBORw0KGgoAAAA...",
  "media_type": "image/png",
  "provider": "dall-e",
  "model": "dall-e-3",
  "size": "1024x1024",
  "revised_prompt": "A breathtaking serene mountain...",
  "elapsed_ms": 8500
}
```

> `revised_prompt`는 DALL-E 3이 자동 개선한 프롬프트입니다 (Imagen은 null).

---

## 5. STT API — 음성 → 텍스트

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

## 6. 재사용 유틸 함수 (TypeScript)

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

## 7. Python 연동

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

## 8. cURL 예시

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

## 9. 에러 처리

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

## 10. 요약

| 기능 | 엔드포인트 | provider 미지정 시 |
|------|-----------|-------------------|
| AI 채팅 | `POST /api/ai/chat` | 기본 Chat 엔진 (claude-sonnet) |
| 스트리밍 | `POST /api/ai/chat/stream` | 기본 Chat 엔진 |
| 음성인식 | `POST /api/ai/stt` | 기본 STT 엔진 (whisper) |
| 이미지 생성 | `POST /api/ai/image` | 기본 Image 엔진 (dall-e) |

**원칙: provider를 지정하지 않으면 Gateway 기본값이 적용됩니다.**
기본값은 Admin 대시보드에서 언제든 변경할 수 있으며, 클라이언트 코드 수정은 불필요합니다.
