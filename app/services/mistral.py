import re
from typing import Optional

from .openai_compat import OpenAICompatService, clamp

# Verified 2026-09-19 (mistral-medium-latest = Medium 3.5, mistral-small-latest
# = Small 4, mistral-large-latest = Large 3):
# - Only Small 4 / Medium 3.5 reason. reasoning_effort accepts just "none" and
#   "high"; other values, or any value on a non-reasoning model, return 400.
#   Nothing sent = no reasoning.
# - With reasoning on, content is a list of thinking/text chunks (handled in
#   answer_text). Temperature range [0, 1.5]. Unknown body fields return 422.
_REASONING = re.compile(r"^(mistral-(small|medium)|magistral-)")
_VISION = re.compile(r"^(mistral-(small|medium|large)|magistral-|ministral-|pixtral-)")


class MistralService(OpenAICompatService):
    """Mistral chat API (OpenAI compatible)."""

    VENDOR = "mistral"
    ALLOW_ASSISTANT_PREFIX = True

    def _options(self, reasoning: Optional[str], temperature: Optional[float]):
        temperature = clamp(temperature, 0.0, 1.5)
        if reasoning is None or not _REASONING.match(self.model):
            return {}, None, temperature
        if reasoning == "off":
            return {"reasoning_effort": "none"}, "off", temperature
        return {"reasoning_effort": "high"}, "high", temperature

    def _supports_images(self) -> bool:
        return bool(_VISION.match(self.model))
