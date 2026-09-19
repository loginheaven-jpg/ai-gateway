import httpx
import json
import logging
import re
from typing import List, Dict, Any, Optional, AsyncGenerator, Tuple
from .base import AIService
from .clients import get_http_client

logger = logging.getLogger(__name__)

# Model capabilities (verified against the API, 2026-09-19):
# - Claude 5 family / Opus 4.7+: a temperature parameter is rejected (400).
# - Thinking is on by default for the 5 family; off for 4.x.
# - "adaptive" thinking + output_config.effort for newer models; 4.5 models use
#   thinking.enabled + budget_tokens, which only allows temperature 1.
_NO_TEMPERATURE = re.compile(r"^claude-(sonnet-5|opus-5|fable|opus-4-[78])")
_THINKS_BY_DEFAULT = re.compile(r"^claude-(sonnet-5|opus-5|fable)")
_ADAPTIVE = re.compile(r"^claude-(sonnet-5|opus-5|fable|opus-4-[678]|sonnet-4-6)")
_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}
_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "refusal": "refusal"}


def _claude_options(model: str, reasoning: Optional[str], temperature: Optional[float],
                    max_tokens: int) -> Tuple[Dict[str, Any], Optional[str], Optional[float]]:
    """(extra payload, applied reasoning, temperature to send)."""
    extra: Dict[str, Any] = {}
    applied = reasoning
    if reasoning == "off":
        if _THINKS_BY_DEFAULT.match(model):
            extra["thinking"] = {"type": "disabled"}
    elif reasoning in _BUDGET:
        if _ADAPTIVE.match(model):
            extra["thinking"] = {"type": "adaptive"}
            extra["output_config"] = {"effort": reasoning}
        else:
            budget = min(_BUDGET[reasoning], max_tokens - 256)
            if budget >= 1024:
                extra["thinking"] = {"type": "enabled", "budget_tokens": budget}
                temperature = None  # only temperature 1 is allowed while thinking
            else:
                applied = "off"  # token limit too small to think
    if _NO_TEMPERATURE.match(model):
        temperature = None
    return extra, applied, temperature


class ClaudeService(AIService):
    """Claude (Anthropic) AI Service"""

    def _payload(self, messages, system_prompt, max_tokens, temperature, reasoning, stream=False):
        extra, applied_reasoning, temperature = _claude_options(self.model, reasoning, temperature, max_tokens)
        payload = {"model": self.model, "max_tokens": max_tokens, "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        if system_prompt:
            payload["system"] = system_prompt
        if stream:
            payload["stream"] = True
        payload.update(extra)
        return payload, applied_reasoning

    async def _post(self, payload: Dict[str, Any]) -> httpx.Response:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"
        }
        # 300s (5 min) timeout for long analysis requests
        response = await get_http_client().post(
            f"{self.base_url}/messages", headers=headers, json=payload, timeout=300.0)
        # Safety net for models whose capabilities are not in the tables above:
        # drop a rejected temperature / thinking option once and retry.
        if response.status_code == 400:
            msg = response.text
            retry = dict(payload)
            if "temperature" in msg and "temperature" in retry:
                retry.pop("temperature")
            elif ("thinking" in msg or "effort" in msg) and ("thinking" in retry or "output_config" in retry):
                retry.pop("thinking", None)
                retry.pop("output_config", None)
            else:
                return response
            logger.warning(f"[CLAUDE] {self.model} rejected an option; retrying without it: {msg[:160]}")
            response = await get_http_client().post(
                f"{self.base_url}/messages", headers=headers, json=retry, timeout=300.0)
        return response

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = 0.7,
        reasoning: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload, applied_reasoning = self._payload(messages, system_prompt, max_tokens, temperature, reasoning)

        logger.info(f"[CLAUDE] Model: {self.model}, Max tokens: {max_tokens}, reasoning: {applied_reasoning}")
        logger.info(f"[CLAUDE] Messages: {len(messages)}, System prompt: {len(system_prompt) if system_prompt else 0} chars")

        try:
            response = await self._post(payload)

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"[CLAUDE ERROR] Status {response.status_code}: {error_text}")
                raise Exception(f"Claude API error {response.status_code}: {error_text[:500]}")

            data = response.json()
            # Thinking responses start with a thinking block; the answer is the text blocks
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

            logger.info(f"[CLAUDE] Response received, content length: {len(text)}")

            return {
                "content": text,
                "model": data["model"],
                "usage": {
                    "input_tokens": data["usage"]["input_tokens"],
                    "output_tokens": data["usage"]["output_tokens"]
                },
                "provider": "claude-haiku" if "haiku" in data["model"] else "claude-sonnet",
                "finish_reason": _FINISH.get(data.get("stop_reason"), data.get("stop_reason")),
                "applied_reasoning": applied_reasoning,
                "applied_temperature": payload.get("temperature"),
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

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = 0.7,
        reasoning: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"
        }
        payload, _ = self._payload(messages, system_prompt, max_tokens, temperature, reasoning, stream=True)

        async with get_http_client().stream(
            "POST",
            f"{self.base_url}/messages",
            headers=headers,
            json=payload,
            timeout=300.0,
        ) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                raise Exception(f"Claude API error {response.status_code}: {error_text.decode()[:500]}")

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                event_type = data.get("type")

                if event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield delta["text"]
                elif event_type == "message_stop":
                    break
