from google import genai
from google.genai import types
import base64
import logging
import os
from typing import List, Dict, Any, Optional
from .base import AIService
from .clients import get_genai_client

logger = logging.getLogger(__name__)

# SDK-level timeout. google-genai 1.2.0 runs each call as a blocking requests
# call in a worker thread with no timeout; asyncio.wait_for only abandons the
# await, the thread keeps running. Set just above the gateway's per-provider
# deadline so the gateway's own timeout (504 / fallback) fires first.
_SDK_TIMEOUT_MS = int((float(os.getenv("AI_PROVIDER_DEADLINE_S", "15")) + 5) * 1000)

# Thinking control differs per model (verified against the API, 2026-09-19):
# 3.x Flash: thinking_budget=0 disables, "minimal" is rejected; Flash-Lite:
# thinks minimally by default, budget 0 is rejected; Pro: cannot disable.
_BUDGET_25 = {"low": 1024, "medium": 4096, "high": 16384}
_FINISH = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "safety", "RECITATION": "recitation"}


def _thinking_config(model: str, reasoning: Optional[str]):
    """(thinking config kwargs or None, applied reasoning)."""
    if reasoning is None:
        return None, None
    if "flash-lite" in model:
        return {"thinking_level": "minimal" if reasoning == "off" else reasoning}, reasoning
    if model.startswith("gemini-2.5"):
        if reasoning == "off":
            if "pro" in model:
                return {"thinking_budget": 128}, "low"
            return {"thinking_budget": 0}, "off"
        return {"thinking_budget": _BUDGET_25[reasoning]}, reasoning
    if "pro" in model:
        level = "low" if reasoning == "off" else reasoning
        return {"thinking_level": level}, level
    if reasoning == "off":
        return {"thinking_budget": 0}, "off"
    return {"thinking_level": reasoning}, reasoning


def _finish(fr) -> str:
    name = getattr(fr, "name", None) or str(fr or "UNKNOWN").split(".")[-1]
    return _FINISH.get(name, name.lower())


class GeminiService(AIService):
    """Gemini (Google) AI Service - Using new google-genai SDK"""

    def __init__(self, api_key: str, model: str, base_url: str = None):
        super().__init__(api_key, model, base_url)
        self.client = get_genai_client(self.api_key, _SDK_TIMEOUT_MS)
        logger.info(f"[GEMINI] Initialized with model: {model}")

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: Optional[float] = 0.7,
        reasoning: Optional[str] = None,
    ) -> Dict[str, Any]:
        thinking, applied_reasoning = _thinking_config(self.model, reasoning)
        logger.info(f"[GEMINI] Model: {self.model}, Max tokens: {max_tokens}, reasoning: {applied_reasoning}")
        logger.info(f"[GEMINI] Messages: {len(messages)}, System prompt: {len(system_prompt) if system_prompt else 0} chars")

        # Build contents for the API
        contents = []

        # Add system prompt as first user message if provided
        if system_prompt:
            contents.append(types.Content(
                role="user",
                parts=[types.Part(text=f"[System Instruction]\n{system_prompt}\n\n[User Message]")]
            ))

        for msg in messages:
            role = "model" if msg.get("role") == "assistant" else "user"
            content = msg.get("content", "")

            if not content:
                continue

            # Build parts from content (string or multimodal array)
            parts = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(types.Part(text=block.get("text", "")))
                        elif block.get("type") == "image":
                            source = block.get("source", {})
                            media_type = source.get("media_type", "image/jpeg")
                            data_b64 = source.get("data", "")
                            image_bytes = base64.b64decode(data_b64)
                            parts.append(types.Part(
                                inline_data=types.Blob(
                                    mime_type=media_type,
                                    data=image_bytes
                                )
                            ))
            else:
                parts.append(types.Part(text=content))

            if not parts:
                continue

            # Merge if same role as previous
            if contents and contents[-1].role == role:
                contents[-1].parts.extend(parts)
            else:
                contents.append(types.Content(role=role, parts=parts))

        # Generation config
        config_kwargs = dict(
            max_output_tokens=max_tokens,
            safety_settings=[
                types.SafetySetting(
                    category="HARM_CATEGORY_HARASSMENT",
                    threshold="OFF"
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_HATE_SPEECH",
                    threshold="OFF"
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    threshold="OFF"
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_DANGEROUS_CONTENT",
                    threshold="OFF"
                ),
            ]
        )
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if thinking:
            config_kwargs["thinking_config"] = types.ThinkingConfig(**thinking)

        logger.info(f"[GEMINI] Calling API...")
        try:
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_kwargs)
                )
            except Exception as e:
                # Safety net for models not covered above: drop a rejected thinking option once
                msg = str(e)
                if not thinking or "INVALID_ARGUMENT" not in msg or not ("hinking" in msg or "udget" in msg):
                    raise
                logger.warning(f"[GEMINI] {self.model} rejected {thinking}; retrying without it")
                config_kwargs.pop("thinking_config")
                applied_reasoning = None
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_kwargs)
                )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[GEMINI ERROR] {type(e).__name__}: {error_msg}")
            if "504" in error_msg or "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
                raise Exception(f"Gemini API timeout: 요청 시간이 초과되었습니다. 녹취록이 너무 길 수 있습니다.")
            raise Exception(f"Gemini API error: {error_msg}")

        # Extract response with detailed logging

        content_text = None

        # Method 1: Try response.text directly
        try:
            if hasattr(response, 'text') and response.text:
                content_text = response.text
                logger.info(f"[GEMINI] Method 1 (response.text) succeeded, length: {len(content_text)}")
        except Exception as e:
            logger.warning(f"[GEMINI] Method 1 failed: {str(e)}")

        # Method 2: Try candidates[0].content.parts[0].text
        if not content_text and response.candidates:
            candidate = response.candidates[0]

            if hasattr(candidate, 'content') and candidate.content:
                logger.info(f"[GEMINI] Content type: {type(candidate.content)}")
                if hasattr(candidate.content, 'parts') and candidate.content.parts:
                    logger.info(f"[GEMINI] Parts count: {len(candidate.content.parts)}")
                    parts_text = []
                    for i, part in enumerate(candidate.content.parts):
                        if hasattr(part, 'text') and part.text:
                            parts_text.append(part.text)
                            logger.info(f"[GEMINI] Part {i} text length: {len(part.text)}")
                    if parts_text:
                        content_text = "\n".join(parts_text)
                        logger.info(f"[GEMINI] Method 2 succeeded, length: {len(content_text)}")

        # Method 3: Try to serialize and check structure
        if not content_text:
            try:
                if hasattr(response, 'model_dump'):
                    response_dict = response.model_dump()
                    logger.info(f"[GEMINI] Response dict: {str(response_dict)[:1000]}")
                elif hasattr(response, '__dict__'):
                    logger.info(f"[GEMINI] Response __dict__: {str(response.__dict__)[:1000]}")
            except Exception as e:
                logger.warning(f"[GEMINI] Could not serialize response: {str(e)}")

        # Fallback
        if not content_text:
            finish_reason = "UNKNOWN"
            if response.candidates:
                fr = getattr(response.candidates[0], 'finish_reason', None)
                finish_reason = str(fr) if fr else "UNKNOWN"
            content_text = f"[Empty response: {finish_reason}]"
            logger.warning(f"[GEMINI] All extraction methods failed: {content_text}")

        # Extract metadata
        input_tokens = 0
        output_tokens = 0
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
            output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0

        finish_reason = "UNKNOWN"
        if response.candidates:
            finish_reason = str(response.candidates[0].finish_reason) if hasattr(response.candidates[0], 'finish_reason') else "UNKNOWN"

        logger.info(f"[GEMINI] Final response - finish_reason: {finish_reason}, input: {input_tokens}, output: {output_tokens}")

        return {
            "content": content_text,
            "model": self.model,
            "finish_reason": _finish(response.candidates[0].finish_reason) if response.candidates else "unknown",
            "applied_reasoning": applied_reasoning,
            "applied_temperature": config_kwargs.get("temperature"),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            },
            "provider": "gemini"
        }
