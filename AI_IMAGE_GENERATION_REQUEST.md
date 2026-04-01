# AI Gateway — 이미지 생성 엔드포인트 추가 요청

> **요청 서비스**: 예봄카드 (Yebom Card)
> **요청일**: 2026-04-01
> **우선순위**: Phase 2

---

## 1. 배경

예봄카드는 성경 말씀을 이미지 카드로 생성하는 서비스입니다.
현재 카드 배경으로 **CSS 그라데이션**과 **Unsplash 사진**을 제공하고 있으며,
**AI 생성 이미지 배경**을 3번째 옵션으로 추가하려 합니다.

현재 AI Gateway의 Chat API로 CSS gradient 코드를 텍스트로 생성하고 있으나,
실제 이미지가 아닌 단순 그라데이션이라 차별점이 없습니다.

---

## 2. 요청 사양

### 엔드포인트

```
POST {AI_GATEWAY_URL}/api/ai/image
Content-Type: application/json
```

### Request

```json
{
  "prompt": "A serene mountain landscape at golden dawn with soft clouds, peaceful and spiritual atmosphere",
  "size": "1080x1350",
  "style": "natural",
  "provider": "imagen",
  "caller": "yebom-card:background"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `prompt` | string | O | 영문 이미지 생성 프롬프트 |
| `size` | string | X | 이미지 크기 (기본: `1024x1024`) |
| `style` | string | X | `natural` / `vivid` / `artistic` |
| `provider` | string | X | 이미지 생성 엔진 별칭 |
| `caller` | string | X | 호출자 식별 |

### Response

```json
{
  "url": "https://storage.googleapis.com/...(생성된 이미지 URL)",
  "provider": "imagen",
  "model": "imagen-3",
  "size": "1080x1350",
  "elapsed_ms": 8500
}
```

또는 base64 방식:

```json
{
  "data": "iVBORw0KGgoAAAA...(base64)",
  "media_type": "image/png",
  "provider": "imagen",
  "model": "imagen-3"
}
```

---

## 3. 추천 엔진

| 엔진 | 특징 | 비용 | 비고 |
|------|------|------|------|
| **Google Imagen 3** | Gemini API 통합, 고품질 | ~$0.04/장 | Vertex AI 또는 Gemini API |
| **DALL-E 3** | OpenAI, 범용 | ~$0.04/장 (1024x1024) | API Key 필요 |
| **Stable Diffusion 3** | 오픈소스, 셀프호스팅 가능 | 서버 비용만 | Replicate 또는 직접 호스팅 |

### 추천: **Google Imagen 3** (Gemini API)

- 기존 Gemini Flash/Pro와 같은 API 체계
- `generateContent`에 이미지 생성 모드 추가된 형태
- 풍경/자연 이미지 품질 우수

---

## 4. 예봄카드 사용 시나리오

```
[사용자가 성경 구절 선택]
  ↓
[키워드 추출] "평안", "안식"
  ↓
[영문 프롬프트 생성]
  "A peaceful lake at sunset with golden light reflecting on calm water,
   spiritual and serene atmosphere, suitable for Bible verse card background"
  ↓
[POST /api/ai/image]
  ↓
[생성된 이미지 URL → 카드 배경으로 사용]
```

### 프롬프트 생성 규칙

- 항상 **풍경/자연** 위주 (인물 X)
- `spiritual`, `serene`, `peaceful` 등 분위기 키워드 포함
- `suitable for text overlay` — 텍스트 가독성 고려
- 카드 비율 4:5 (1080x1350) — 세로형 이미지

---

## 5. Fallback 제안

| 1차 | 2차 | 3차 |
|-----|-----|-----|
| imagen | dall-e | stable-diffusion |

이미지 생성 실패 시 → 기존 CSS gradient로 자동 fallback (예봄카드 클라이언트에서 처리)

---

## 6. Rate Limit 고려

- 예봄카드 예상 사용량: 초기 50~100건/일
- 이미지 생성은 비용이 있으므로 **결과 캐싱** 권장
  - 동일 프롬프트 → 캐시된 이미지 URL 반환
  - TTL: 24시간

---

## 7. 예봄카드 측 준비 사항 (완료)

- `/api/ai/background` 라우트에 이미지 생성 호출 코드 준비
- Gateway에 `/api/ai/image` 추가되면 즉시 전환 가능
- 전환 전까지 CSS gradient 방식으로 동작 유지
