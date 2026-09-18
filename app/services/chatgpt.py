from openai import AsyncOpenAI
import httpx
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator, Tuple
from .base import AIService

logger = logging.getLogger(__name__)

# One async client per (api_key, base_url), reused across requests. The async
# client never blocks the event loop, and asyncio.wait_for can cancel it.
_clients: Dict[Tuple[str, str], AsyncOpenAI] = {}


def _get_client(api_key: str, base_url: str) -> AsyncOpenAI:
    key = (api_key, base_url)
    client = _clients.get(key)
    if client is None:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(300.0, connect=60.0),  # 5 min timeout
            max_retries=0,  # gateway handles fallback; SDK retries on permanent errors waste time
        )
        _clients[key] = client
    return client


class ChatGPTService(AIService):
    """ChatGPT (OpenAI) AI Service"""

    def _transform_messages_for_openai(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Transform Claude-format image blocks to OpenAI format."""
        transformed = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                new_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        source = block.get("source", {})
                        media_type = source.get("media_type", "image/jpeg")
                        data = source.get("data", "")
                        new_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"}
                        })
                    else:
                        new_blocks.append(block)
                transformed.append({"role": msg["role"], "content": new_blocks})
            else:
                transformed.append(msg)
        return transformed

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        logger.info(f"[OPENAI] Model: {self.model}, Max tokens: {max_tokens}")
        logger.info(f"[OPENAI] Messages: {len(messages)}, System prompt: {len(system_prompt) if system_prompt else 0} chars")

        try:
            client = _get_client(self.api_key, self.base_url)

            # Transform image blocks to OpenAI format
            messages = self._transform_messages_for_openai(messages)

            # Prepend system message if provided
            all_messages = []
            if system_prompt:
                all_messages.append({"role": "system", "content": system_prompt})
            all_messages.extend(messages)

            logger.info(f"[OPENAI] Calling API...")

            # Call OpenAI API using SDK
            # GPT-5.1 and newer models require max_completion_tokens instead of max_tokens
            response = await client.chat.completions.create(
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

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        client = _get_client(self.api_key, self.base_url)

        messages = self._transform_messages_for_openai(messages)

        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        stream = await client.chat.completions.create(
            model=self.model,
            messages=all_messages,
            temperature=temperature,
            max_completion_tokens=max_tokens,
            stream=True
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
