# AI Gateway Vision API 추가 요청 가이드

## 배경

재정부(church-finance)에서 **영수증 이미지를 AI로 분석**하여 청구 입력값과 자동 대조하는 기능을 구현하려 합니다.

현재 AI Gateway의 Chat API는 `content: string`만 지원하여, Claude Vision(이미지 분석)에 필요한 multimodal content 배열을 전달할 수 없습니다.

```
# 현재 (텍스트만)
{ "role": "user", "content": "안녕하세요" }  ← string만 가능

# 필요 (이미지 + 텍스트)
{ "role": "user", "content": [
    { "type": "image", "source": { "type": "base64", ... } },
    { "type": "text", "text": "이 영수증을 분석해줘" }
  ]}  ← 배열 불가 (pydantic validation 에러)
```

## 요청사항

### 방안 A: Chat API content 타입 확장 (추천)

기존 Chat API의 `content` 필드를 `string | list` 유니온 타입으로 확장.

```python
# 현재
class Message(BaseModel):
    role: str
    content: str

# 변경
class Message(BaseModel):
    role: str
    content: Union[str, list]  # string 또는 content block 배열
```

Claude API는 이미 content 배열을 지원하므로, Gateway에서 그대로 전달만 하면 됩니다.
GPT도 동일한 multimodal content 형식을 지원합니다.

**장점**: 기존 API 호환 유지 (string도 그대로 동작), 새 엔드포인트 불필요

### 방안 B: Vision 전용 엔드포인트 신설

```
POST /api/ai/vision
Content-Type: application/json

{
  "provider": "claude-haiku",
  "image_base64": "...",
  "image_media_type": "image/jpeg",
  "prompt": "이 영수증의 금액, 가맹점명, 날짜를 JSON으로 추출해줘",
  "max_tokens": 500,
  "caller": "church-finance:receipt-verify"
}
```

**장점**: 단순한 인터페이스, 이미지 전용 최적화 가능

## 재정부 사용 시나리오

```
성도가 영수증 촬영 → 업로드 → 압축(~300KB)
→ base64 변환 → AI Gateway 호출 (claude-haiku)
→ AI가 영수증에서 추출: { amount: 25000, store: "이마트", date: "2026-03-28" }
→ 사용자 입력값과 자동 대조 → 일치/불일치 표시
```

예상 호출량: 월 100~300건, 이미지 크기: 200~400KB (압축 후)

## 테스트 요청

구현 후 아래 curl로 테스트 가능:

```bash
# 방안 A 테스트
curl -X POST https://ai-gateway20251125.up.railway.app/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "claude-haiku",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "/9j/4AAQ..."}},
        {"type": "text", "text": "이 영수증의 금액과 가맹점을 JSON으로 알려줘"}
      ]
    }],
    "max_tokens": 500,
    "caller": "church-finance:receipt-verify"
  }'

# 방안 B 테스트
curl -X POST https://ai-gateway20251125.up.railway.app/api/ai/vision \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "claude-haiku",
    "image_base64": "/9j/4AAQ...",
    "image_media_type": "image/jpeg",
    "prompt": "이 영수증의 금액과 가맹점을 JSON으로 알려줘",
    "caller": "church-finance:receipt-verify"
  }'
```

## 우선순위

방안 A를 추천합니다. 기존 코드 변경 최소화 + 향후 다른 서비스에서도 이미지 분석을 바로 사용 가능.
