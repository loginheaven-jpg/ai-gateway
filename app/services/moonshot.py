import httpx
from typing import List, Dict, Any, Optional
from .base import AIService
from .clients import get_http_client


class MoonshotService(AIService):
    """Moonshot (Kimi) AI Service - OpenAI compatible API"""

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = 0.7,
        reasoning: Optional[str] = None,  # not applicable
    ) -> Dict[str, Any]:
        # Moonshot does not support vision/image content
        for msg in messages:
            if isinstance(msg.get("content"), list):
                raise Exception("Moonshot does not support vision/image content. Use claude-sonnet, chatgpt, or gemini-pro instead.")

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
            "messages": all_messages
        }
        if temperature is not None:
            payload["temperature"] = temperature

        response = await get_http_client().post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()

        return {
            "content": data["choices"][0]["message"]["content"],
            "model": data["model"],
            "usage": {
                "input_tokens": data["usage"]["prompt_tokens"],
                "output_tokens": data["usage"]["completion_tokens"]
            },
            "provider": "moonshot",
            "finish_reason": data["choices"][0].get("finish_reason"),
            "applied_temperature": payload.get("temperature"),
        }
