from openai import OpenAI
import httpx
import logging
from typing import List, Dict, Any, Optional
from .base import AIService

logger = logging.getLogger(__name__)


class ChatGPTService(AIService):
    """ChatGPT (OpenAI) AI Service"""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        logger.info(f"[OPENAI] Model: {self.model}, Max tokens: {max_tokens}")
        logger.info(f"[OPENAI] Messages: {len(messages)}, System prompt: {len(system_prompt) if system_prompt else 0} chars")

        try:
            # Use configured OpenAI endpoint with extended timeout
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=httpx.Timeout(300.0, connect=60.0)  # 5 min timeout
            )

            # Prepend system message if provided
            all_messages = []
            if system_prompt:
                all_messages.append({"role": "system", "content": system_prompt})
            all_messages.extend(messages)

            logger.info(f"[OPENAI] Calling API...")

            # Call OpenAI API using SDK
            # GPT-5.1 and newer models require max_completion_tokens instead of max_tokens
            response = client.chat.completions.create(
                model=self.model,
                messages=all_messages,
                temperature=temperature,
                max_completion_tokens=max_tokens
            )

            content = response.choices[0].message.content
            logger.info(f"[OPENAI] Response received, content length: {len(content) if content else 0}")

            return {
                "content": content,
                "model": response.model,
                "usage": {
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens
                },
                "provider": "openai"
            }

        except Exception as e:
            logger.error(f"[OPENAI ERROR] {type(e).__name__}: {str(e)}")
            raise
