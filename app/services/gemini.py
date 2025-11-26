import google.generativeai as genai
import logging
from typing import List, Dict, Any, Optional
from .base import AIService

logger = logging.getLogger(__name__)


class GeminiService(AIService):
    """Gemini (Google) AI Service - Stateless Implementation"""

    def __init__(self, api_key: str, model: str, base_url: str = None):
        super().__init__(api_key, model, base_url)
        # Configure with extended timeout for long requests
        genai.configure(api_key=self.api_key)
        logger.info(f"[GEMINI] Initialized with model: {model}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        logger.info(f"[GEMINI] Model: {self.model}, Max tokens: {max_tokens}")
        logger.info(f"[GEMINI] Messages: {len(messages)}, System prompt: {len(system_prompt) if system_prompt else 0} chars")

        # 1. Create model (without system_instruction for compatibility)
        model = genai.GenerativeModel(self.model)

        # 2. Preprocess messages (merge consecutive same roles)
        gemini_contents = []

        # Add system prompt as first user message if provided
        if system_prompt:
            gemini_contents.append({
                "role": "user",
                "parts": [f"[System Instruction]\n{system_prompt}\n\n[User Message]"]
            })

        for msg in messages:
            role = "model" if msg.get("role") == "assistant" else "user"
            content = msg.get("content", "")

            # Skip empty messages
            if not content:
                continue

            # Merge if same role as previous (prevents User -> User)
            if gemini_contents and gemini_contents[-1]["role"] == role:
                gemini_contents[-1]["parts"][0] += f"\n\n{content}"
            else:
                gemini_contents.append({
                    "role": role,
                    "parts": [content]
                })

        # 3. Safety settings (Block None)
        safety_settings = {
            genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
            genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH: genai.types.HarmBlockThreshold.BLOCK_NONE,
            genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: genai.types.HarmBlockThreshold.BLOCK_NONE,
            genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
        }

        # 4. Generate content (stateless, send all messages at once)
        logger.info(f"[GEMINI] Calling API...")
        try:
            # Note: timeout is handled by the underlying httpx client in newer versions
            response = await model.generate_content_async(
                gemini_contents,
                generation_config=genai.types.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
                safety_settings=safety_settings
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[GEMINI ERROR] {type(e).__name__}: {error_msg}")
            # Check if it's a timeout error
            if "504" in error_msg or "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
                raise Exception(f"Gemini API timeout: 요청 시간이 초과되었습니다. 녹취록이 너무 길 수 있습니다.")
            # Re-raise the exception so it's properly handled by the router
            raise Exception(f"Gemini API error: {error_msg}")

        # 5. Extract response text with error handling
        try:
            content_text = response.text
            logger.info(f"[GEMINI] Response extracted successfully, length: {len(content_text)}")
        except ValueError as ve:
            logger.warning(f"[GEMINI] ValueError extracting response.text: {str(ve)}")
            # ValueError occurs when response.text can't be extracted
            if response.candidates:
                candidate = response.candidates[0]
                finish_reason = candidate.finish_reason.name
                logger.info(f"[GEMINI] Finish reason: {finish_reason}")

                # Check if there's actual content in parts
                if candidate.content and candidate.content.parts:
                    # Try to extract text from parts directly
                    parts_text = []
                    for part in candidate.content.parts:
                        if hasattr(part, 'text') and part.text:
                            parts_text.append(part.text)
                    if parts_text:
                        content_text = "\n".join(parts_text)
                        logger.info(f"[GEMINI] Extracted from parts, length: {len(content_text)}")
                    else:
                        content_text = f"[Empty response: {finish_reason}]"
                        logger.warning(f"[GEMINI] No text in parts: {content_text}")
                elif finish_reason in ("SAFETY", "RECITATION", "OTHER"):
                    content_text = f"[Blocked: {finish_reason}]"
                    logger.warning(f"[GEMINI] Content blocked: {content_text}")
                else:
                    content_text = f"[Empty response: {finish_reason}]"
                    logger.warning(f"[GEMINI] Empty response: {content_text}")
            else:
                content_text = "[No content returned]"
                logger.warning(f"[GEMINI] No candidates in response")

        # 6. Extract metadata
        usage_metadata = getattr(response, 'usage_metadata', None)
        input_tokens = getattr(usage_metadata, 'prompt_token_count', 0) if usage_metadata else 0
        output_tokens = getattr(usage_metadata, 'candidates_token_count', 0) if usage_metadata else 0

        finish_reason = "UNKNOWN"
        if response.candidates:
            finish_reason = response.candidates[0].finish_reason.name

        logger.info(f"[GEMINI] Final response - finish_reason: {finish_reason}, input: {input_tokens}, output: {output_tokens}")

        return {
            "content": content_text,
            "model": self.model,
            "finish_reason": finish_reason,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            },
            "provider": "gemini"
        }
