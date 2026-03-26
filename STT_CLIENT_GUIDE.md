# AI Gateway — STT 호출 가이드 (클라이언트용)

## 엔드포인트

```
POST https://ai-gateway20251125.up.railway.app/api/ai/stt
```

## 핵심 원칙

- **파라미터 없이 호출하면** → Gateway에서 설정한 기본 STT 엔진이 자동 적용
- **provider 파라미터를 넣으면** → 해당 엔진이 사용됨
- 클라이언트 서비스에 **STT 관련 API Key가 불필요** (Gateway가 관리)

---

## 1. 기본 호출 (가장 간단)

provider를 지정하지 않으면 Gateway의 기본 엔진(현재 `whisper`)이 사용됩니다.

### JavaScript / TypeScript

```typescript
async function stt(audioFile: File | Blob): Promise<string> {
  const formData = new FormData();
  formData.append('file', audioFile, 'audio.webm');

  const res = await fetch('https://ai-gateway20251125.up.railway.app/api/ai/stt', {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Unknown' }));
    throw new Error(`STT 실패: ${err.error}`);
  }

  const data = await res.json();
  return data.text;  // "인식된 텍스트"
}
```

### Python

```python
import requests

with open("recording.webm", "rb") as f:
    res = requests.post(
        "https://ai-gateway20251125.up.railway.app/api/ai/stt",
        files={"file": ("audio.webm", f)},
    )

data = res.json()
print(data["text"])  # "인식된 텍스트"
```

### cURL

```bash
curl -X POST https://ai-gateway20251125.up.railway.app/api/ai/stt \
  -F "file=@recording.webm"
```

---

## 2. 옵션 파라미터

모두 **선택**입니다. 하나도 안 넣어도 동작합니다.

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `file` | File | **(필수)** | 오디오 파일 |
| `language` | string | `ko` | 언어 코드 (`ko`, `en`, `ja`, `zh`) |
| `provider` | string | Gateway 기본값 | STT 엔진 지정 (아래 표 참고) |
| `caller` | string | - | 호출 서비스 식별자 (사용량 추적용) |

### provider 값

| 값 | 엔진 | 특징 |
|---|---|---|
| (미지정) | Gateway 기본값 | **권장.** Admin에서 설정한 엔진 사용 |
| `whisper` | OpenAI Whisper | 다국어, 최대 25MB |
| `clova-csr` | Naver CLOVA CSR | 한국어 특화, 최대 60초, 빠름 |
| `clova-speech` | Naver CLOVA Speech Long | 한국어 특화, 최대 80분, 화자분리 |

---

## 3. 응답 형식

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

| 필드 | 설명 |
|------|------|
| `text` | 인식된 텍스트 |
| `language` | 사용된 언어 코드 |
| `duration_sec` | 오디오 길이 (초). CSR은 0 반환 |
| `provider` | 실제 사용된 엔진 (fallback 시 요청과 다를 수 있음) |
| `model` | 사용된 모델명 |
| `elapsed_ms` | 처리 시간 (ms) |

---

## 4. 에러 응답

```json
{
  "error": "File too large: 15000000 bytes (max 10MB)",
  "code": "FILE_TOO_LARGE"
}
```

| code | 상황 |
|------|------|
| `UNSUPPORTED_FORMAT` | 지원하지 않는 오디오 포맷 |
| `FILE_TOO_LARGE` | 파일 크기 초과 (기본 10MB) |
| `INVALID_FILE` | 빈 파일 |
| `PROVIDER_ERROR` | 모든 STT 엔진 실패 |

---

## 5. 실전 사용 예시

### 5-1. 가장 간단한 호출 (권장)

```typescript
// provider 미지정 → Gateway 기본 엔진 사용
const formData = new FormData();
formData.append('file', audioBlob, 'audio.webm');

const res = await fetch(`${AI_GATEWAY_URL}/api/ai/stt`, {
  method: 'POST',
  body: formData,
});
const { text } = await res.json();
```

### 5-2. 특정 엔진 지정

```typescript
// Whisper를 명시적으로 지정
const formData = new FormData();
formData.append('file', audioBlob, 'audio.webm');
formData.append('provider', 'whisper');

const res = await fetch(`${AI_GATEWAY_URL}/api/ai/stt`, {
  method: 'POST',
  body: formData,
});
```

### 5-3. 한국어 + caller 추적

```typescript
const formData = new FormData();
formData.append('file', audioBlob, 'audio.webm');
formData.append('language', 'ko');
formData.append('caller', 'prayer-house:encouragement');

const res = await fetch(`${AI_GATEWAY_URL}/api/ai/stt`, {
  method: 'POST',
  body: formData,
});
```

### 5-4. 재사용 가능한 유틸 함수 (TypeScript)

```typescript
const AI_GATEWAY_URL = process.env.AI_GATEWAY_URL
  || 'https://ai-gateway20251125.up.railway.app';

interface STTResult {
  text: string;
  language: string;
  duration_sec: number;
  provider: string;
  model: string;
  elapsed_ms: number;
}

/**
 * AI Gateway STT 호출
 * @param file - 오디오 파일 (File 또는 Blob)
 * @param options - 선택 옵션 (language, provider, caller)
 */
export async function callSTT(
  file: File | Blob,
  options?: {
    language?: string;    // 기본: 'ko'
    provider?: string;    // 기본: Gateway 설정값
    caller?: string;      // 사용량 추적용 식별자
  }
): Promise<STTResult> {
  const formData = new FormData();
  const ext = file.type?.includes('mp4') ? 'mp4' : 'webm';
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

**호출 예시:**

```typescript
// 기본 (파라미터 없이)
const { text } = await callSTT(audioBlob);

// 언어 + 추적
const { text } = await callSTT(audioBlob, {
  language: 'ko',
  caller: 'saint-record:memo',
});

// 특정 엔진 지정
const { text } = await callSTT(audioBlob, {
  provider: 'clova-csr',
});
```

---

## 6. 지원 오디오 포맷

| 포맷 | Whisper | CLOVA CSR | CLOVA Speech Long |
|------|---------|-----------|-------------------|
| webm | O | X | X |
| mp4 | O | O | O |
| mp3 | O | O | O |
| wav | O | O | O |
| ogg | O | O | O |
| m4a | O | X | O |
| flac | O | O | O |
| aac | X | O | X |

> **webm**(Chrome/Edge 녹음 기본 포맷)은 Whisper만 지원합니다.
> 브라우저 녹음 → STT라면 **Whisper를 기본 엔진으로 유지**하는 것을 권장합니다.

---

## 7. Fallback 동작

Gateway는 1차 엔진이 실패하면 자동으로 다른 엔진을 시도합니다.

| 1차 | 2차 | 3차 |
|-----|-----|-----|
| whisper | clova-csr | clova-speech |
| clova-csr | whisper | clova-speech |
| clova-speech | whisper | clova-csr |

응답의 `provider` 필드를 확인하면 실제로 어떤 엔진이 사용되었는지 알 수 있습니다.

---

## 8. 환경변수 (클라이언트 서비스)

```env
# 이것만 있으면 됨
AI_GATEWAY_URL=https://ai-gateway20251125.up.railway.app
```

**STT API Key는 필요 없습니다.** Gateway가 모든 인증을 처리합니다.
