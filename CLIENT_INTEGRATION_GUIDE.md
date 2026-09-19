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
| `claude-sonnet` | Claude Sonnet 5 | 고성능 분석/작성 **(기본값)** |
| `claude-haiku` | Claude Haiku 4.5 | 빠른 응답, 경량 작업 |
| `chatgpt` (`openai`는 같은 뜻의 옛 이름) | GPT-5.6 terra | 범용 AI |
| `gemini-pro` | Gemini Pro (latest) | 고성능 분석, 느림 |
| `gemini-flash` | Gemini 3.8 Flash | 빠른 응답 |
| `gemini-lite` | Gemini 3.5 Flash-Lite | 분류·짧은 답(예: 질문 판별). 가장 빠르고 저렴 |
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
  "temperature": 0.7,                 // (선택) 창의성 (0~1). null이면 보내지 않음
  "use_fallback": true,               // (선택) 실패 시 자동 대체
  "use_cache": true,                   // (선택) 동일 요청 캐시
  "caller": "saint-record:memo",       // (선택) 호출자 식별 (사용량 추적)
  "model": "claude-sonnet-5",          // (선택) 아래 '모델·옵션 지정' 참고
  "options": { "reasoning": "off", "timeout_s": 60 }  // (선택) 아래 '모델·옵션 지정' 참고
}
```

### 모델·옵션 지정 (선택, 2026-09 추가)

필드를 보내지 않으면 별칭의 **권장 기본값**이 쓰입니다(아래 표). 기능마다 다르게 쓰고 싶을 때만 지정하세요.

```json
{
  "provider": "gemini-flash",
  "model": "gemini-3.5-flash-lite",     // (선택) 같은 회사 모델만. 다른 회사 모델이면 400
  "options": {
    "reasoning": "off",                 // (선택) off | low | medium | high
    "timeout_s": 60                     // (선택) 이 요청의 공급사 호출 1회 상한(초)
  },
  "temperature": 0,                     // (선택) null 이면 보내지 않음
  "messages": [{ "role": "user", "content": "..." }]
}
```

- `reasoning`은 공급사마다 이름이 다른 추론/thinking 설정을 하나로 묶은 값입니다. 게이트웨이가 모델에 맞게 바꿔 보냅니다.
  모델이 받지 못하는 값은 가능한 가장 가까운 값으로 바뀌거나 빠집니다(예: Gemini Pro는 추론을 끌 수 없어 `low`, Claude 5·GPT 추론 모드는 temperature를 받지 않음).
- 퇴역한 모델 이름을 보내면 후속 모델로 자동으로 바뀝니다.
- `model`은 1차 provider에만 적용됩니다. 다른 공급사로 폴백하면 그 별칭의 기본값을 쓰고, `options.reasoning`은 이어 받습니다.
- 모델 이름을 코드에 박으면 모델이 퇴역할 때 앱을 고쳐야 합니다. **기본은 별칭만 쓰고, 꼭 필요할 때만 `model`을 지정**하세요.
  빠른 분류용 모델이 필요하면 `model` 대신 `provider: "gemini-lite"`를 쓰세요.
- `model`은 게이트웨이의 **허용 목록**에 있는 모델만 받습니다. 목록에 없으면 공급사를 부르지 않고 400
  (`... is not in the gateway's allowed list ...`)을 돌려줍니다. 각 별칭에 설정된 모델과 그 같은 계열 대체 모델은 늘 허용됩니다.
  기본 목록: claude-sonnet-5, claude-haiku-4-5, gpt-5.6-terra / luna / sol, gemini-3.8-flash, gemini-3.5-flash-lite, gemini-pro-latest.
  목록을 늘리려면 게이트웨이 관리자에게 요청하세요(관리 화면 '요청 모델 허용 목록').
- 요청에서 고른 `model`이 실패해도 그 별칭의 차단기(연속 실패 시 잠시 건너뛰기)에는 세지 않습니다.
  한 앱의 모델 선택이 같은 별칭을 쓰는 다른 앱을 막지 않게 하려는 것입니다.

**`options.timeout_s`** (초, 0보다 크고 300 이하)

- 공급사 호출 1회의 상한을 이 요청에 한해 바꿉니다. 지정하지 않으면 서버 기본값(운영 30초)을 씁니다.
  폴백을 포함한 요청 전체 상한은 `timeout_s`의 2배(서버 기본값보다 작아지지 않음, 최대 600초)입니다.
- 긴 글 생성처럼 오래 걸리는 것이 정상인 기능에만 쓰세요. 짧게 잡으면 느린 공급사를 일찍 포기하고 폴백합니다.
- 지정하면 `gemini-flash`의 '6초 뒤 lite로 대체'는 하지 않고, 실패했을 때만 대체합니다(긴 답을 기다린다는 뜻이므로).
- 호출하는 쪽의 HTTP 타임아웃은 `timeout_s`보다 넉넉하게 잡으세요.

**권장 기본값** (요청에 없을 때. 관리 화면에서 별칭별로 바꿀 수 있고, 카드에 표시됩니다)

| 별칭 | 모델 | 추론 | 같은 계열 대체 |
|------|------|------|------|
| `claude-sonnet` | claude-sonnet-5 | 끔 | – |
| `claude-haiku` | claude-haiku-4-5 | 끔 | – |
| `chatgpt`, `openai` | gpt-5.6-terra | 끔 | – |
| `gemini-flash` | gemini-3.8-flash | low | 6초 안에 답이 없으면 gemini-3.5-flash-lite |
| `gemini-lite` | gemini-3.5-flash-lite | 끔 | – |
| `gemini-pro` | gemini-pro-latest | 모델 기본값(끌 수 없음) | – |

같은 계열 대체는 같은 회사 모델로만 바꾸므로 `use_fallback: false`여도 적용됩니다(칸 이름이 그대로 맞음).


### 응답 형식

```json
{
  "content": "안녕하세요! 무엇을 도와드릴까요?",
  "model": "claude-sonnet-5",
  "provider": "claude-sonnet",
  "usage": {
    "input_tokens": 15,
    "output_tokens": 25
  },
  "finish_reason": "stop",
  "applied": {
    "provider": "claude-sonnet",
    "model": "claude-sonnet-5",
    "reasoning": "off",
    "temperature": null,
    "fallback_from": null
  }
}
```

(`options.timeout_s`를 보냈으면 `applied.timeout_s`도 들어갑니다.)

- `finish_reason`: `stop`(정상 끝) · `length`(max_tokens에 걸려 잘림) · 그 밖의 공급사 값. **잘림은 이 값으로 판정하세요.**
- `applied`: 실제로 쓴 별칭·모델·추론·temperature. 같은 계열 대체가 일어나면 `fallback_from`에 원래 모델이 적힙니다.
- ChatGPT가 빈 답을 내면 실패로 처리해 폴백합니다(`use_fallback: false`면 500).

### Fallback (자동 대체)

1차 프로바이더가 실패하면 자동으로 다른 엔진을 시도합니다.

| Primary | Fallback 순서 |
|---------|---------------|
| claude-sonnet | claude-haiku → gemini-pro → chatgpt |
| claude-haiku | claude-sonnet → gemini-flash → chatgpt |
| chatgpt / openai | claude-haiku → claude-sonnet → gemini-pro |
| gemini-pro | claude-haiku → gemini-flash → claude-sonnet → chatgpt |
| gemini-flash | claude-haiku → gemini-pro → chatgpt |
| gemini-lite | gemini-flash → claude-haiku → chatgpt |
| moonshot | claude-haiku → claude-sonnet → chatgpt |
| perplexity | claude-haiku → claude-sonnet → chatgpt |

순서의 기준은 코드(`app/routers/ai.py`의 `FALLBACK_CHAINS`)입니다. `use_fallback: false`면 1차 provider만 시도합니다.

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
| `dall-e` | GPT Image (OpenAI, `gpt-image-2.5-flare`) | 범용 **(기본값)** |
| `imagen` | Gemini Image (Google, `gemini-3.1-flash-image`) | 풍경/자연 고품질 |

> 별칭은 그대로입니다. DALL-E 3 / Imagen 3 퇴역에 따라 엔진만 바뀌었습니다(2026-09).

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

| 사이즈 | GPT Image (`dall-e`) | Gemini Image (`imagen`) |
|--------|----------|----------|
| `1024x1024` | 1024x1024 | 1:1 |
| `1080x1350` | → 1024x1536 | 3:4 |
| `1792x1024` | → 1536x1024 | 16:9 |
| `1024x1792` | → 1024x1536 | 9:16 |

응답의 `size`는 실제로 생성한 크기(GPT Image) 또는 비율(Gemini Image)입니다. 두 엔진 모두 `media_type`은 `image/png`입니다.

### 응답 형식

```json
{
  "data": "iVBORw0KGgoAAAA...",
  "media_type": "image/png",
  "provider": "dall-e",
  "model": "gpt-image-2.5-flare",
  "size": "1024x1024",
  "revised_prompt": null,
  "elapsed_ms": 8500
}
```

> `revised_prompt`는 호환을 위해 남겨 둔 필드로, 현재 엔진에서는 `null`입니다.

---

## 5. Image Edit API — 이미지 텍스트 제거

이미지에서 텍스트/워터마크/간판 등을 자동 탐지하여 제거하고 주변 배경으로 채웁니다.

### 엔드포인트

```
POST {AI_GATEWAY_URL}/api/ai/image/edit
Content-Type: application/json
```

### 사용 가능한 provider 별칭

| 별칭 | 엔진 | 특징 |
|------|------|------|
| `imagen` | Imagen 3 (Vertex AI) | 고품질, 원본 크기 유지 **(기본값)** |
| `dall-e` | GPT Image (OpenAI, `gpt-image-2`) | 1024x1024 정사각형 출력 |

### 기본 호출 (provider 미지정 → 기본값 imagen)

```typescript
const imageBase64 = await fileToBase64(photoFile);

const res = await fetch(`${AI_GATEWAY_URL}/api/ai/image/edit`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    image: imageBase64,
    media_type: 'image/jpeg',
    edit_type: 'remove_text',
    caller: 'yebom-card:upload'
  })
});

const data = await res.json();
// data.data = 편집된 이미지 (base64)
// data.regions_found = 탐지된 텍스트 영역 수 (0이면 글자 없음 → 원본 반환)
// data.provider = 실제 사용된 provider ("imagen" 또는 "dall-e")
```

### 특정 provider 지정

```typescript
// DALL-E 사용
body: JSON.stringify({
  image: imageBase64,
  media_type: 'image/jpeg',
  edit_type: 'remove_text',
  provider: 'dall-e'    // ← provider 지정
})

// Imagen 사용 (기본값이므로 생략 가능)
body: JSON.stringify({
  image: imageBase64,
  media_type: 'image/jpeg',
  edit_type: 'remove_text',
  provider: 'imagen'
})
```

### 전체 파라미터

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `image` | string | **O** | - | 원본 이미지 (base64) |
| `media_type` | string | **O** | - | `image/jpeg` 또는 `image/png` |
| `edit_type` | string | X | `remove_text` | 편집 유형 (현재 `remove_text`만) |
| `provider` | string | X | 기본값 | `imagen` 또는 `dall-e` |
| `mask` | string | X | - | 수동 마스크 (base64 PNG). 제공 시 자동 탐지 건너뜀 |
| `caller` | string | X | - | 호출자 식별 |

### 응답

```json
{
  "data": "iVBORw0KGgoAAAA...",
  "media_type": "image/png",
  "edit_type": "remove_text",
  "regions_found": 3,
  "provider": "imagen",
  "model": "imagen-3.0-capability-001",
  "elapsed_ms": 8500
}
```

> `regions_found: 0`이면 텍스트가 없어 원본이 그대로 반환됩니다.

### 내부 파이프라인
1. **Gemini Vision** → 텍스트 영역 bounding box 좌표 탐지
2. **PIL** → 마스크 이미지 생성
3. **Imagen 3 (Vertex AI) 또는 GPT Image** → 마스크 기반 inpainting (텍스트 제거)

### provider 비교

| | Imagen 3 | GPT Image |
|--|----------|----------|
| 품질 | 높음 | 보통~높음 (마스크 밖은 그대로 둠) |
| 출력 크기 | 원본 유지 | 1024x1024 고정 |
| 비용 | ~$0.04/장 | ~$0.04/장 |
| 인프라 | Vertex AI (서비스 계정) | API Key |

---

## 6. STT API — 음성 → 텍스트

### 엔드포인트

```
POST {AI_GATEWAY_URL}/api/ai/stt
Content-Type: multipart/form-data
```

### 사용 가능한 provider 별칭

| 별칭 | 엔진 | 특징 |
|------|------|------|
| `whisper` | OpenAI gpt-transcribe (별칭 이름은 그대로) | 다국어, 최대 25MB **(기본값)** |
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
  "model": "gpt-transcribe",
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

> 브라우저 녹음(webm)은 **whisper 별칭만** 지원합니다.
>
> 2026-09-19부터 `whisper` 별칭의 모델은 whisper-1 대신 gpt-transcribe입니다(whisper-1은 2027-02-26 퇴역).
> 한국어 시험에서 더 정확하고 빨랐습니다. 요청·응답 형식은 같고, 응답 `model` 값만 바뀝니다.

---

## 7. 재사용 유틸 함수 (TypeScript)

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

## 8. Python 연동

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

## 9. cURL 예시

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

## 10. 에러 처리

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

## 11. 요약

| 기능 | 엔드포인트 | provider 미지정 시 |
|------|-----------|-------------------|
| AI 채팅 | `POST /api/ai/chat` | 기본 Chat 엔진 (claude-sonnet) |
| 스트리밍 | `POST /api/ai/chat/stream` | 기본 Chat 엔진 |
| 음성인식 | `POST /api/ai/stt` | 기본 STT 엔진 (whisper) |
| 이미지 생성 | `POST /api/ai/image` | 기본 Image 엔진 (dall-e) |
| 이미지 편집 | `POST /api/ai/image/edit` | Gemini 탐지 + DALL-E inpainting |

**원칙: provider를 지정하지 않으면 Gateway 기본값이 적용됩니다.**
기본값은 Admin 대시보드에서 언제든 변경할 수 있으며, 클라이언트 코드 수정은 불필요합니다.
