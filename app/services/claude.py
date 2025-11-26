import httpx
import logging
from typing import List, Dict, Any, Optional
from .base import AIService

logger = logging.getLogger(__name__)


class ClaudeService(AIService):
    """Claude (Anthropic) AI Service"""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"
        }

        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages
        }

        if system_prompt:
            payload["system"] = system_prompt

        logger.info(f"[CLAUDE] Model: {self.model}, Max tokens: {max_tokens}")
        logger.info(f"[CLAUDE] Messages: {len(messages)}, System prompt: {len(system_prompt) if system_prompt else 0} chars")

        try:
            # Increased timeout to 300s (5 min) for long analysis requests
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    f"{self.base_url}/messages",
                    headers=headers,
                    json=payload
                )

                if response.status_code != 200:
                    error_text = response.text
                    logger.error(f"[CLAUDE ERROR] Status {response.status_code}: {error_text}")
                    raise Exception(f"Claude API error {response.status_code}: {error_text[:500]}")

                data = response.json()

            logger.info(f"[CLAUDE] Response received, content length: {len(data.get('content', [{}])[0].get('text', ''))}")

            return {
                "content": data["content"][0]["text"],
                "model": data["model"],
                "usage": {
                    "input_tokens": data["usage"]["input_tokens"],
                    "output_tokens": data["usage"]["output_tokens"]
                },
                "provider": "claude"
            }

        except httpx.TimeoutException as e:
            logger.error(f"[CLAUDE TIMEOUT] Request timed out: {str(e)}")
            raise Exception(f"Claude API timeout after 300 seconds")
        except httpx.HTTPStatusError as e:
            logger.error(f"[CLAUDE HTTP ERROR] {e.response.status_code}: {e.response.text[:500]}")
            raise
        except Exception as e:
            logger.error(f"[CLAUDE ERROR] {type(e).__name__}: {str(e)}")
            raise
