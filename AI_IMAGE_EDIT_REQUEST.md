# AI Gateway — 이미지 편집(글자 제거) 엔드포인트 추가 요청

> **요청 서비스**: 예봄성경 (bible.yebom.org)
> **요청일**: 2026-04-13
> **선행 요건**: `/api/ai/image` 엔드포인트 (이미 구현됨)
> **우선순위**: Phase 3

---

## 1. 배경

예봄성경의 카드 만들기 기능에서 사용자가 **직접 촬영한 사진**을 배경으로 사용할 수 있게 되었다.
문제는 사용자 사진에 **기존 글자/워터마크/간판 등 텍스트**가 포함된 경우가 많다는 점이다.

카드에 성경 말씀(흰 텍스트)을 올리면 원본 사진의 글자와 겹쳐 가독성이 크게 떨어진다.
현재는 40~55% 어두운 오버레이를 적용하지만, 원본 글자가 굵거나 밝으면 여전히 보인다.

**요청**: 사용자 사진에서 텍스트 영역을 자동 탐지하고 주변 배경으로 채워 넣는
이미지 편집 기능(text removal / inpainting)을 AI Gateway에 추가해 달라.

---

## 2. 요청 사양

### 엔드포인트

```
POST {AI_GATEWAY_URL}/api/ai/image/edit
Content-Type: application/json
```

### Request

```json
{
  "image": "iVBORw0KGgoAAAA...(base64)",
  "media_type": "image/jpeg",
  "edit_type": "remove_text",
  "caller": "yebom-card:upload"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `image` | string | O | 원본 이미지 (base64) |
| `media_type` | string | O | `image/jpeg` / `image/png` |
| `edit_type` | string | O | 편집 유형 (현재 `remove_text`만 필요) |
| `mask` | string | X | 수동 마스크 이미지 (base64). 없으면 자동 탐지 |
| `caller` | string | X | 호출자 식별 |

### Response

```json
{
  "data": "iVBORw0KGgoAAAA...(편집된 이미지 base64)",
  "media_type": "image/png",
  "provider": "imagen",
  "model": "imagen-3",
  "edit_type": "remove_text",
  "regions_found": 3,
  "elapsed_ms": 4200
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `data` | string | 편집된 이미지 (base64) |
| `regions_found` | number | 탐지된 텍스트 영역 수 (0이면 글자 없음 → 원본 반환) |
| `elapsed_ms` | number | 처리 시간 |

---

## 3. 내부 파이프라인 제안

### 2단계 처리

```
[원본 이미지]
    ↓
[1단계: OCR / 텍스트 영역 탐지]
    → 텍스트 bounding box 좌표 추출
    → 마스크 이미지 생성 (텍스트 영역 = 흰색, 나머지 = 검정)
    ↓
[2단계: Inpainting]
    → 원본 이미지 + 마스크 → 텍스트 영역을 주변 배경으로 채움
    ↓
[편집된 이미지 반환]
```

### 1단계: 텍스트 탐지 옵션

| 방식 | 특징 | 비용 |
|------|------|------|
| **Google Cloud Vision OCR** | TEXT_DETECTION → bounding poly 반환 | ~$1.50/1000장 |
| **Gemini Vision** | 이미지 입력 + "텍스트 영역 좌표 반환" 프롬프트 | 기존 Gemini API 비용 |
| **CRAFT / EasyOCR** | 오픈소스, 셀프호스팅 | 서버 비용만 |

**추천**: Gemini Vision (기존 API 체계 재활용)
- `generateContent`에 이미지 첨부 + 프롬프트: "이 이미지에서 모든 텍스트 영역의 bounding box를 JSON으로 반환해줘"
- 결과를 마스크 이미지로 변환

### 2단계: Inpainting 옵션

| 엔진 | 특징 | 비용 |
|------|------|------|
| **Google Imagen 3 (edit)** | `editImage` API, 마스크 기반 인페인팅 | ~$0.04/장 |
| **DALL-E 2 (edit)** | 마스크 기반 인페인팅, 1024x1024 제한 | ~$0.04/장 |
| **Stable Diffusion Inpainting** | 오픈소스, Replicate에서 호스팅 가능 | ~$0.01/장 |
| **LaMa (Large Mask Inpainting)** | 오픈소스, 텍스트 제거에 특화 | 서버 비용만 |

**추천**: Google Imagen 3 editImage (Gemini API 통합)
```python
# Vertex AI 예시
from vertexai.preview.vision_models import ImageGenerationModel

model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-002")
result = model.edit_image(
    prompt="Remove all text and watermarks, fill with surrounding background",
    base_image=original_image,
    mask=text_mask_image,
)
```

### 대안: 단일 모델 방식 (탐지+제거 통합)

Gemini의 멀티모달 기능이 충분히 발전하면:
```json
{
  "prompt": "Remove all visible text, watermarks, and writing from this image. Fill removed areas with surrounding background seamlessly.",
  "image": "(base64)",
  "edit_mode": "inpaint"
}
```
단일 호출로 처리 가능. 현재 Imagen 3의 editImage가 이 방식에 가장 가까움.

---

## 4. 예봄성경 측 연동 계획

Gateway에 엔드포인트가 추가되면:

```typescript
// lib/aiGateway.ts 에 추가
export async function callImageEdit(
  imageBase64: string,
  mediaType: string,
  editType: string = "remove_text"
): Promise<ImageResult> {
  const res = await fetch(`${AI_GATEWAY_URL}/api/ai/image/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      image: imageBase64,
      media_type: mediaType,
      edit_type: editType,
      caller: "yebom-card:upload",
    }),
  });
  if (!res.ok) throw new Error(`IMAGE_EDIT_ERROR: ${res.status}`);
  return res.json();
}
```

CardPreview.tsx의 upload 흐름:
```
[사용자 사진 선택]
    ↓
[미리보기 표시 (원본 + 오버레이)]
    ↓
["글자 제거" 버튼 클릭] (선택적)
    ↓
[callImageEdit() → 편집된 이미지로 교체]
    ↓
[카드 배경으로 사용]
```

글자 제거는 **선택적 기능**으로 제공 (버튼 클릭 시에만 실행).
원본 사진에 글자가 없으면 불필요하므로, 자동 실행보다 사용자 선택이 적절.

---

## 5. 비용 / Rate Limit

- 예상 사용량: 10~30건/일 (업로드 사진 중 글자 제거 요청 비율)
- OCR(1단계): ~$0.0015/건
- Inpainting(2단계): ~$0.04/건
- **합계**: ~$0.04/건, 일 $0.40~$1.20

캐싱 효과 낮음 (사용자마다 다른 사진이므로).
비용 제한이 필요하면 사용자당 일일 N회 제한 가능.

---

## 6. 구현 우선순위

| 단계 | 내용 | 시점 |
|------|------|------|
| **지금** | 사용자 사진 업로드 + 오버레이 적용 (Gateway 변경 불필요) | 완료 |
| **Phase 3a** | Gateway에 `/api/ai/image/edit` 추가 (remove_text) | Gateway 개발 필요 |
| **Phase 3b** | 예봄성경에 "글자 제거" 버튼 추가 | Phase 3a 완료 후 |
| **향후** | 자동 글자 탐지 → 글자가 있으면 자동 제안 | 사용량 데이터 기반 |
