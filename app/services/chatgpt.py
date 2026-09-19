import openai
from openai import AsyncOpenAI
import httpx
import logging
import re
from typing import List, Dict, Any, Optional, AsyncGenerator
from .base import AIService
from .clients import get_async_openai

logger = logging.getLogger(__name__)


def _get_client(api_key: str, base_url: str) -> AsyncOpenAI:
    # Shared async client (max_retries=0: the gateway handles fallback). It never
    # blocks the event loop, and asyncio.wait_for can cancel it.
    return get_async_openai(api_key, base_url, httpx.Timeout(300.0, connect=60.0))


# Reasoning models (verified 2026-09-19): gpt-5.1/5.2/5.4 default to effort
# "none", gpt-5.5/5.6 to "medium"; the original gpt-5 family has no "none"
# (lowest is "minimal"). Temperature is only accepted with effort "none".
_REASONING_MODEL = re.compile(r"^(gpt-5|o[134])")
_NO_NONE_EFFORT = re.compile(r"^gpt-5(-mini|-nano)?($|-\d{4})")
_DEFAULT_NONE = re.compile(r"^gpt-5\.(1|2|4)($|-|\b)")
_FINISH = {"stop": "stop", "length": "length", "content_filter": "content_filter"}

# Learned at runtime: models that rejected a parameter (400 with that param).
_NO_TEMP_MODELS: set = set()
_NO_EFFORT_MODELS: set = set()


def _openai_options(model: str, reasoning, temperature):
    """(extra kwargs, applied reasoning, temperature to send)."""
    extra = {}
    if not _REASONING_MODEL.match(model):
        return extra, None, temperature  # non-reasoning model: nothing to translate
    effort = None
    if reasoning == "off":
        effort = "minimal" if _NO_NONE_EFFORT.match(model) else "none"
    elif reasoning in ("low", "medium", "high"):
        effort = reasoning
    if effort and model not in _NO_EFFORT_MODELS:
        extra["reasoning_effort"] = effort
    else:
        effort = None
    effective = effort or ("none" if _DEFAULT_NONE.match(model) else "medium")
    if effective != "none":
        temperature = None
    return extra, reasoning if effort else None, temperature


async def _create(client: AsyncOpenAI, model: str, **kwargs):
    """chat.completions.create; if the model rejects temperature or
    reasoning_effort, remember that and retry once without it."""
    if model in _NO_TEMP_MODELS:
        kwargs.pop("temperature", None)
    try:
        return await client.chat.completions.create(model=model, **kwargs)
    except openai.BadRequestError as e:
        param = getattr(e, "param", None)
        if param == "temperature" and "temperature" in kwargs:
            _NO_TEMP_MODELS.add(model)
            kwargs.pop("temperature")
        elif param == "reasoning_effort" and "reasoning_effort" in kwargs:
            _NO_EFFORT_MODELS.add(model)
            kwargs.pop("reasoning_effort")
        else:
            raise
        logger.warning(f"[OPENAI] {model} rejects {param}; retrying without it")
        return await client.chat.completions.create(model=model, **kwargs)


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
        temperature: Optional[float] = 0.7,
        reasoning: Optional[str] = None,
        timeout_s: Optional[float] = None,  # HTTP client allows 300s, the API maximum
    ) -> Dict[str, Any]:
        extra, applied_reasoning, temperature = _openai_options(self.model, reasoning, temperature)
        logger.info(f"[OPENAI] Model: {self.model}, Max tokens: {max_tokens}, reasoning: {applied_reasoning}")
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
            kwargs = dict(messages=all_messages, max_completion_tokens=max_tokens, **extra)
            if temperature is not None:
                kwargs["temperature"] = temperature
            response = await _create(client, self.model, **kwargs)

            choice = response.choices[0]
            content = choice.message.content or ""
            if choice.finish_reason == "length":
                logger.warning(f"[OPENAI] {self.model} hit max_completion_tokens={max_tokens} "
                               f"(content length {len(content)})")
            if not content.strip():
                # An empty answer is a failure, not a success: reasoning tokens can use up
                # max_completion_tokens. Raising keeps the fallback chain working.
                raise Exception(f"OpenAI returned an empty answer (finish_reason={choice.finish_reason}, "
                                f"max_completion_tokens={max_tokens})")
            logger.info(f"[OPENAI] Response received, content length: {len(content)}")

            return {
                "content": content,
                "model": response.model,
                "usage": {
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens
                },
                "provider": "openai",
                "finish_reason": _FINISH.get(choice.finish_reason, choice.finish_reason),
                "applied_reasoning": applied_reasoning,
                "applied_temperature": kwargs.get("temperature"),
            }

        except Exception as e:
            logger.error(f"[OPENAI ERROR] {type(e).__name__}: {str(e)}")
            raise

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = 0.7,
        reasoning: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        extra, _, temperature = _openai_options(self.model, reasoning, temperature)
        client = _get_client(self.api_key, self.base_url)

        messages = self._transform_messages_for_openai(messages)

        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        kwargs = dict(messages=all_messages, max_completion_tokens=max_tokens, stream=True, **extra)
        if temperature is not None:
            kwargs["temperature"] = temperature
        stream = await _create(client, self.model, **kwargs)

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
