# AI Gateway — STT(음성→텍스트) 엔드포인트 개발 가이드

## 1. 개요

기존 AI Gateway (`/api/ai/chat`)에 **음성→텍스트 변환 엔드포인트**를 추가한다.

```
기존: POST /api/ai/chat    → 텍스트 AI (Claude, GPT, Gemini)
추가: POST /api/ai/stt     → 음성→텍스트 (Whisper, CLOVA Speech)
```

### 왜 게이트웨이인가?

- **API 키 일원 관리**: 각 서비스(교적부, 기도의 집 등)에 API 키를 배포하지 않음
- **Provider 전환**: Whisper ↔ CLOVA 전환이 클라이언트 코드 수정 없이 가능
- **Fallback**: Whisper 장애 시 CLOVA로 자동 전환
- **사용량 로깅**: 호출 건수, 처리 시간, 비용 추적
- **재사용**: 모든 서비스에서 동일 인터페이스로 STT 사용 가능

---

## 2. 엔드포인트 명세

### `POST /api/ai/stt`

#### Request

**Content-Type**: `multipart/form-data`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `file` | File (binary) | ✅ | 오디오 파일 (webm, mp4, ogg, mp3, wav) |
| `language` | string | ❌ | 언어 코드 (기본값: `ko`). ISO 639-1 |
| `provider` | string | ❌ | STT 프로바이더 (기본값: 설정값 또는 `whisper`) |
| `caller` | string | ❌ | 호출 서비스 식별자 (예: `prayer-house:encouragement`) |

#### Response (200 OK)

```json
{
  "text": "기도해 주셔서 감사합니다. 하나님께서 꼭 회복시켜 주실 거예요.",
  "language": "ko",
  "duration_sec": 12.5,
  "provider": "whisper",
  "model": "whisper-1",
  "elapsed_ms": 3200
}
```

#### Error Response (4xx / 5xx)

```json
{
  "error": "에러 메시지",
  "code": "INVALID_FILE" | "PROVIDER_ERROR" | "FILE_TOO_LARGE" | "UNSUPPORTED_FORMAT"
}
```

---

## 3. Provider 구현

### 3-1. OpenAI Whisper

```javascript
// POST https://api.openai.com/v1/audio/transcriptions
const formData = new FormData();
formData.append('file', audioFile, 'audio.webm');
formData.append('model', 'whisper-1');
formData.append('language', 'ko');
formData.append('response_format', 'json');

const res = await fetch('https://api.openai.com/v1/audio/transcriptions', {
  method: 'POST',
  headers: { Authorization: `Bearer ${OPENAI_API_KEY}` },
  body: formData,
});

const data = await res.json();
// data.text = "인식된 텍스트"
```

**환경변수**: `OPENAI_API_KEY`
**가격**: $0.006/분 (~7원)
**파일 제한**: 최대 25MB
**지원 포맷**: mp3, mp4, mpeg, mpga, m4a, wav, webm

### 3-2. Naver CLOVA Speech (향후)

```javascript
// POST https://clovaspeech-gw.ncloud.com/recog/v1/stt
const res = await fetch('https://clovaspeech-gw.ncloud.com/recog/v1/stt', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/octet-stream',
    'X-CLOVASPEECH-API-KEY': CLOVA_API_KEY,
  },
  body: audioBuffer,  // raw binary
  params: { lang: 'ko-KR' },
});

const data = await res.json();
// data.text = "인식된 텍스트"
```

**환경변수**: `CLOVA_API_KEY`, `CLOVA_INVOKE_URL`
**가격**: 15초당 5원 (~분당 20원), 월 300건 무료
**파일 제한**: 60초 (CSR) / 80분 (CLOVA Speech Long)
**지원 포맷**: wav, mp3, aac, ogg, flac

---

## 4. 게이트웨이 내부 로직

```
[요청 수신]
    ↓
[파일 검증] → 크기 제한 (10MB), 포맷 확인
    ↓
[Provider 결정] → 요청의 provider > 환경변수 기본값 > 'whisper'
    ↓
[API 호출] → Whisper 또는 CLOVA
    ↓
[Fallback] → 1차 실패 시 다른 Provider로 재시도 (선택적)
    ↓
[사용량 로깅] → caller, provider, duration, elapsed, 비용 추정
    ↓
[응답 반환] → 통일된 JSON 형식
```

### 4-1. Provider 선택 우선순위

```javascript
function resolveProvider(requestProvider, config) {
  // 1. 요청에서 명시적으로 지정
  if (requestProvider) return requestProvider;
  // 2. 환경변수 기본값
  if (config.DEFAULT_STT_PROVIDER) return config.DEFAULT_STT_PROVIDER;
  // 3. 하드코딩 기본값
  return 'whisper';
}
```

### 4-2. Fallback 로직

```javascript
const providers = ['whisper', 'clova'];

async function transcribeWithFallback(file, language, primaryProvider) {
  const ordered = [primaryProvider, ...providers.filter(p => p !== primaryProvider)];

  for (const provider of ordered) {
    try {
      return await transcribe(provider, file, language);
    } catch (err) {
      console.error(`[STT] ${provider} failed:`, err.message);
      continue;
    }
  }
  throw new Error('All STT providers failed');
}
```

### 4-3. 사용량 로깅

```javascript
// 호출 완료 후 로깅
await logUsage({
  type: 'stt',
  caller: 'prayer-house:encouragement',
  provider: 'whisper',
  model: 'whisper-1',
  language: 'ko',
  duration_sec: 12.5,        // 오디오 길이
  elapsed_ms: 3200,          // 처리 시간
  file_size_bytes: 198400,   // 파일 크기
  estimated_cost_krw: 2,     // 추정 비용 (원)
  timestamp: new Date(),
});
```

---

## 5. 환경변수

### AI Gateway (Railway)에 추가할 환경변수

```env
# STT Providers
OPENAI_API_KEY=sk-...              # Whisper용 (기존에 있으면 공유)
CLOVA_API_KEY=...                  # CLOVA용 (향후)
CLOVA_INVOKE_URL=...               # CLOVA 엔드포인트 (향후)

# STT 설정
DEFAULT_STT_PROVIDER=whisper       # 기본 Provider
STT_MAX_FILE_SIZE_MB=10            # 최대 파일 크기
STT_USE_FALLBACK=true              # Fallback 활성화
```

### 클라이언트 서비스 (prayer-house 등)

```env
# 추가 환경변수 없음!
# AI_GATEWAY_URL은 이미 설정되어 있음
```

**핵심: 클라이언트 서비스에는 STT 관련 환경변수가 불필요하다.**

---

## 6. 클라이언트 호출 예시 (prayer-house)

### ai.ts에 추가할 함수

```typescript
/**
 * AI Gateway를 통해 음성→텍스트 변환
 */
export async function callSTT(
  audioFile: File | Blob,
  feature: string,
  language = 'ko',
): Promise<{ text: string; duration_sec: number; elapsed_ms: number; provider: string }> {
  const formData = new FormData();
  const ext = audioFile.type?.includes('mp4') ? 'mp4' : 'webm';
  formData.append('file', audioFile, `audio.${ext}`);
  formData.append('language', language);
  formData.append('caller', `prayer-house:${feature}`);

  const res = await fetch(`${AI_GATEWAY_URL}/api/ai/stt`, {
    method: 'POST',
    body: formData,  // Content-Type은 브라우저가 자동 설정 (multipart boundary)
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Unknown' }));
    throw new Error(`STT_ERROR: ${res.status} ${err.error}`);
  }

  return res.json();
}
```

### 격려 메시지 전송 시 활용 예시

```typescript
// 1. 녹음 완료 → audioBlob 확보
// 2. STT로 텍스트 추출
const { text: transcribedText } = await callSTT(audioBlob, 'encouragement');

// 3. 격려 메시지 전송 (음성 + 텍스트 동시)
const formData = new FormData();
formData.append('prayer_request_id', prayerId);
formData.append('audio', audioBlob, 'recording.webm');
formData.append('audio_duration_sec', String(duration));
formData.append('content', transcribedText);  // STT 결과를 텍스트로 저장

await fetch('/api/actions/encourage', { method: 'POST', body: formData });
```

---

## 7. 파일 포맷 호환성 매트릭스

| 브라우저 녹음 포맷 | Whisper | CLOVA |
|-------------------|---------|-------|
| `audio/webm;codecs=opus` (Chrome, Edge) | ✅ | ❌ (변환 필요) |
| `audio/mp4` (Safari, iOS) | ✅ | ✅ |
| `audio/ogg` (Firefox) | ✅ | ✅ |

**CLOVA 사용 시**: webm → wav/mp3 변환이 필요할 수 있음. Gateway에서 `ffmpeg` 또는 `fluent-ffmpeg`로 처리하거나, Whisper를 기본으로 유지.

---

## 8. 구현 체크리스트

- [ ] `/api/ai/stt` 라우트 생성 (POST, multipart/form-data)
- [ ] Whisper provider 구현 (OpenAI API 호출)
- [ ] 파일 검증 (크기, 포맷)
- [ ] 통일된 응답 형식 반환
- [ ] 사용량 로깅 (기존 로깅 테이블 확장 또는 신규)
- [ ] 에러 처리 (Provider 오류, 타임아웃)
- [ ] 환경변수 추가 (OPENAI_API_KEY, DEFAULT_STT_PROVIDER)
- [ ] (선택) CLOVA provider 구현
- [ ] (선택) Fallback 로직
- [ ] (선택) webm → wav 변환 (CLOVA용)

---

## 9. 테스트

### 수동 테스트 (curl)

```bash
# Whisper로 STT
curl -X POST https://ai-gateway20251125.up.railway.app/api/ai/stt \
  -F "file=@recording.webm" \
  -F "language=ko" \
  -F "caller=test"
```

### 기대 응답

```json
{
  "text": "기도해 주셔서 정말 감사합니다",
  "language": "ko",
  "duration_sec": 5.2,
  "provider": "whisper",
  "model": "whisper-1",
  "elapsed_ms": 2100
}
```

---

## 10. 비용 추정

| 시나리오 | 월 사용량 | Whisper 비용 | CLOVA 비용 |
|----------|----------|-------------|-----------|
| 격려 메시지 30건/일 × 20초 | ~300분/월 | ~2,100원 | 무료 (300건 이내) |
| 격려 메시지 100건/일 × 30초 | ~1,500분/월 | ~10,500원 | ~20,000원 |

**초기에는 Whisper로 시작하고, 사용량이 월 300건을 초과하면 CLOVA 무료 티어 활용을 검토한다.**
