import httpx
import logging
from typing import List, Dict, Any, Optional
from .base import AIService
from .clients import get_http_client

logger = logging.getLogger(__name__)


class PerplexityService(AIService):
    """Perplexity AI Service - OpenAI compatible API"""

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        # Perplexity does not support vision/image content
        for msg in messages:
            if isinstance(msg.get("content"), list):
                raise Exception("Perplexity does not support vision/image content. Use claude-sonnet, chatgpt, or gemini-pro instead.")

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

        response = await get_http_client().post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()

        # 응답 구조 디버그 로깅 (DEBUG 수준 — 운영 INFO 에서는 찍히지 않음)
        logger.debug(f"[Perplexity] TOP-LEVEL KEYS: {list(data.keys())}")
        if "choices" in data and data["choices"]:
            choice = data["choices"][0]
            logger.debug(f"[Perplexity] CHOICE KEYS: {list(choice.keys())}")
            if "message" in choice:
                logger.debug(f"[Perplexity] MESSAGE KEYS: {list(choice['message'].keys())}")

        # citations 추출 - Perplexity API는 citations를 최상위 레벨에 반환
        citations = data.get("citations", [])
        logger.debug(f"[Perplexity] Top-level citations: {citations[:3] if citations else 'EMPTY'}")

        # 대체 위치들도 확인
        if not citations:
            citations = data.get("choices", [{}])[0].get("citations", [])
            logger.debug(f"[Perplexity] choices[0].citations: {citations[:3] if citations else 'EMPTY'}")
        if not citations:
            citations = data.get("choices", [{}])[0].get("message", {}).get("citations", [])
            logger.debug(f"[Perplexity] message.citations: {citations[:3] if citations else 'EMPTY'}")

        logger.debug(f"[Perplexity] FINAL citations count: {len(citations)}")

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
