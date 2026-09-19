"""Shared client code for OpenAI-compatible chat APIs called with raw httpx
(DeepSeek, Mistral).

Raw httpx rather than the openai SDK, so the exact payload keys are ours:
Mistral rejects unknown fields (422), DeepSeek silently ignores them (so a
wrong key would fail without an error), and both differ from OpenAI in how
reasoning is switched and returned. Subclasses decide the vendor specifics.
"""
import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from .base import AIService
from .clients import get_http_client

logger = logging.getLogger(__name__)

_FINISH = {"stop": "stop", "length": "length", "model_length": "length",
           "content_filter": "content_filter", "tool_calls": "tool_calls"}
# The server cut the answer short for its own reasons: a failure (the fallback
# chain should run), not a truncated success.
_ABORTED = {"error", "insufficient_system_resource", "aborted"}
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+/=]{80,}")
_ROLES = {"system", "user", "assistant"}


def error_summary(body: Any) -> str:
    """Short description of a vendor error body WITHOUT the request input that
    validation errors echo back (Mistral 422 'input'/'ctx' can hold the user's
    text or a whole image). It ends up in logs, usage rows and the public
    breaker status, so only the vendor's own fields are kept."""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return _LONG_TOKEN.sub("…", body).strip()[:200]
    if not isinstance(body, dict):
        return str(type(body).__name__)
    err = body.get("error") if isinstance(body.get("error"), dict) else body
    parts = [str(err[k]) for k in ("type", "code") if err.get(k)]
    for field in ("message", "detail"):
        value = err.get(field)
        if isinstance(value, dict):
            value = value.get("detail")
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):  # validation items: keep what failed, not the input
            parts += [f"{'.'.join(map(str, item.get('loc', [])))}: {item.get('msg', '')}"
                      for item in value[:3] if isinstance(item, dict)]
    return _LONG_TOKEN.sub("…", " | ".join(parts))[:300]


class ProviderHTTPError(Exception):
    """Non-2xx answer. status_code lets the router classify it."""

    def __init__(self, vendor: str, status_code: int, body: str):
        self.status_code = status_code
        super().__init__(f"{vendor} HTTP {status_code}: {error_summary(body)}")


class UnsupportedInput(Exception):
    """The request cannot be served by this model (e.g. images to a text-only
    model). Not a provider fault: the fallback chain moves on."""


def clamp(value: Optional[float], low: float, high: float) -> Optional[float]:
    return None if value is None else min(max(value, low), high)


def answer_text(content: Any) -> str:
    """Final answer text from message.content / delta.content: a string, or a
    list of typed chunks (Mistral reasoning: 'thinking' chunks are dropped and
    one delta can hold thinking and text together)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(item.get("text") or "" for item in content
                       if isinstance(item, dict) and item.get("type") == "text")
    return ""


def _has_images(messages: List[Dict[str, Any]]) -> bool:
    return any(isinstance(m.get("content"), list) and
               any(isinstance(b, dict) and b.get("type") in ("image", "image_url") for b in m["content"])
               for m in messages)


def _convert_block(block: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(block, dict):
        return None
    kind = block.get("type")
    if kind == "text":
        return {"type": "text", "text": block.get("text") or ""}
    if kind == "image":  # Claude style -> OpenAI image_url (object form: DeepSeek rejects a bare string)
        source = block.get("source", {})
        if source.get("type") == "url":
            url = source.get("url", "")
        else:
            url = f"data:{source.get('media_type', 'image/jpeg')};base64,{source.get('data', '')}"
        return {"type": "image_url", "image_url": {"url": url}}
    if kind == "image_url":
        image = block.get("image_url")
        return {"type": "image_url", "image_url": image if isinstance(image, dict) else {"url": image}}
    return None  # other block types are not supported by these APIs


class OpenAICompatService(AIService):
    VENDOR = "openai-compatible"
    ALLOW_ASSISTANT_PREFIX = False  # Mistral: a trailing assistant turn needs "prefix": true

    # ---- vendor hooks -------------------------------------------------------
    def _options(self, reasoning: Optional[str], temperature: Optional[float]) -> Tuple[Dict[str, Any], Optional[str], Optional[float]]:
        """(extra payload fields, applied reasoning, temperature to send)."""
        return {}, None, temperature

    def _supports_images(self) -> bool:
        return True

    # ---- request ------------------------------------------------------------
    def _messages(self, messages: List[Dict[str, Any]], system_prompt: Optional[str]) -> Tuple[List[Dict[str, Any]], str]:
        """(messages to send, assistant prefix to strip from the answer)."""
        if _has_images(messages) and not self._supports_images():
            raise UnsupportedInput(f"{self.VENDOR} model {self.model} does not support images")
        out: List[Dict[str, Any]] = []
        if system_prompt:
            out.append({"role": "system", "content": system_prompt})
        for m in messages:
            role = "system" if m.get("role") == "developer" else m.get("role", "user")
            if role not in _ROLES:
                raise UnsupportedInput(f"{self.VENDOR}: message role '{role}' is not supported")
            content = m.get("content")
            if isinstance(content, list):
                content = [b for b in (_convert_block(b) for b in content) if b] or ""
            elif not isinstance(content, str):
                raise UnsupportedInput(f"{self.VENDOR}: message content must be a string or a list of blocks")
            out.append({"role": role, "content": content})
        if out and out[-1]["role"] == "assistant" and not answer_text(out[-1]["content"]).strip():
            out.pop()  # an empty trailing assistant turn carries nothing to continue
        prefix = ""
        if self.ALLOW_ASSISTANT_PREFIX and out and out[-1]["role"] == "assistant":
            # Continue the assistant's text (like Claude's prefill); the API
            # returns the prefix too, which is stripped from the answer.
            out[-1]["prefix"] = True
            prefix = answer_text(out[-1]["content"])
        return out, prefix

    def _payload(self, messages, system_prompt, max_tokens, temperature, reasoning, stream):
        msgs, prefix = self._messages(messages, system_prompt)
        extra, applied_reasoning, temperature = self._options(reasoning, temperature)
        payload = {"model": self.model, "messages": msgs, "max_tokens": max_tokens, **extra}
        if temperature is not None:
            payload["temperature"] = temperature
        if stream:
            payload["stream"] = True
        return payload, prefix, applied_reasoning

    def _headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

    def _url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    # ---- calls --------------------------------------------------------------
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = 0.7,
        reasoning: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload, prefix, applied_reasoning = self._payload(
            messages, system_prompt, max_tokens, temperature, reasoning, stream=False)
        logger.info(f"[{self.VENDOR.upper()}] Model: {self.model}, Max tokens: {max_tokens}, reasoning: {applied_reasoning}")

        response = await get_http_client().post(
            self._url(), headers=self._headers(), json=payload,
            timeout=max(120.0, (timeout_s or 0) + 5),
        )
        if response.status_code >= 400:
            raise ProviderHTTPError(self.VENDOR, response.status_code, response.text)
        data = response.json()

        choice = data["choices"][0]
        finish = choice.get("finish_reason")
        if finish in _ABORTED:
            raise Exception(f"{self.VENDOR} stopped the answer early (finish_reason={finish})")
        content = answer_text(choice.get("message", {}).get("content"))
        if prefix and content.startswith(prefix):
            content = content[len(prefix):]
        if not content.strip():
            # Reasoning tokens count against max_tokens and can use all of it:
            # an empty answer is a failure, so the fallback chain runs.
            raise Exception(f"{self.VENDOR} returned an empty answer "
                            f"(finish_reason={finish}, max_tokens={max_tokens})")

        usage = data.get("usage") or {}
        return {
            "content": content,
            "model": data.get("model") or self.model,
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),  # includes reasoning tokens
            },
            "provider": self.VENDOR,
            "finish_reason": _FINISH.get(finish, finish),
            "applied_reasoning": applied_reasoning,
            "applied_temperature": payload.get("temperature"),
        }

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = 0.7,
        reasoning: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        payload, prefix, _ = self._payload(messages, system_prompt, max_tokens, temperature, reasoning, stream=True)
        pending = prefix  # answer text still to be checked against the prefix
        held = ""
        answered = False
        async with get_http_client().stream("POST", self._url(), headers=self._headers(), json=payload) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise ProviderHTTPError(self.VENDOR, response.status_code, body)
            async for line in response.aiter_lines():
                # SSE: skip blank lines and ': keep-alive' comments
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise Exception(f"{self.VENDOR} stream error: {error_summary(chunk)}")
                for choice in chunk.get("choices") or []:
                    text = answer_text((choice.get("delta") or {}).get("content"))
                    if text and pending:
                        held += text
                        if len(held) < len(pending) and pending.startswith(held):
                            text = ""  # still inside the echoed prefix
                        else:
                            text = held[len(pending):] if held.startswith(pending) else held
                            pending, held = "", ""
                    if text:
                        answered = True
                        yield text
                    if choice.get("finish_reason") in _ABORTED:
                        raise Exception(f"{self.VENDOR} stopped the answer early "
                                        f"(finish_reason={choice['finish_reason']})")
        if not answered:
            # e.g. reasoning used up max_tokens: same failure as the non-stream path
            raise Exception(f"{self.VENDOR} returned an empty answer (max_tokens={max_tokens})")
