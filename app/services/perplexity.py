import httpx
from typing import List, Dict, Any, Optional
from .base import AIService


class PerplexityService(AIService):
    """Perplexity AI Service - OpenAI compatible API"""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        # Prepend system message if provided
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": all_messages
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()

        # 전체 응답 디버그 로깅
        import logging
        import json
        logging.warning(f"[Perplexity] TOP-LEVEL KEYS: {list(data.keys())}")
        if "choices" in data and data["choices"]:
            choice = data["choices"][0]
            logging.warning(f"[Perplexity] CHOICE KEYS: {list(choice.keys())}")
            if "message" in choice:
                logging.warning(f"[Perplexity] MESSAGE KEYS: {list(choice['message'].keys())}")

        # citations 추출 - Perplexity API는 citations를 최상위 레벨에 반환
        citations = data.get("citations", [])
        logging.warning(f"[Perplexity] Top-level citations: {citations[:3] if citations else 'EMPTY'}")

        # 대체 위치들도 확인
        if not citations:
            citations = data.get("choices", [{}])[0].get("citations", [])
            logging.warning(f"[Perplexity] choices[0].citations: {citations[:3] if citations else 'EMPTY'}")
        if not citations:
            citations = data.get("choices", [{}])[0].get("message", {}).get("citations", [])
            logging.warning(f"[Perplexity] message.citations: {citations[:3] if citations else 'EMPTY'}")

        logging.warning(f"[Perplexity] FINAL citations count: {len(citations)}")

        return {
            "content": data["choices"][0]["message"]["content"],
            "model": data["model"],
            "usage": {
                "input_tokens": data["usage"].get("prompt_tokens", 0),
                "output_tokens": data["usage"].get("completion_tokens", 0)
            },
            "provider": "perplexity",
            "citations": citations
        }
