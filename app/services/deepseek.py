from typing import Optional

from .openai_compat import OpenAICompatService, clamp

# Verified 2026-09-19 (deepseek-flash = V4.1-Flash, deepseek-v4-pro):
# - Thinking is ON at effort "high" when nothing is sent; off needs an explicit
#   {"thinking": {"type": "disabled"}}.
# - Real effort levels are low / high / max ("medium" is served as "high").
# - Temperature [0, 2]; accepted but ignored while thinking, so it is not sent then.
# - Answer text is always a string; reasoning comes in a separate
#   reasoning_content field, which is never returned.
_EFFORT = {"low": "low", "medium": "high", "high": "high"}


class DeepSeekService(OpenAICompatService):
    """DeepSeek chat API (OpenAI compatible)."""

    VENDOR = "deepseek"

    def _options(self, reasoning: Optional[str], temperature: Optional[float]):
        if reasoning == "off":
            return {"thinking": {"type": "disabled"}}, "off", clamp(temperature, 0.0, 2.0)
        if reasoning in _EFFORT:
            effort = _EFFORT[reasoning]
            return {"thinking": {"type": "enabled"}, "reasoning_effort": effort}, effort, None
        return {}, None, None  # provider default: thinking on (effort high)

    def _supports_images(self) -> bool:
        # deepseek-v4-pro drops images silently and answers anyway (HTTP 200)
        return "pro" not in self.model
