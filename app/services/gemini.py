from google import genai
from google.genai import types
import logging
from typing import List, Dict, Any, Optional
from .base import AIService

logger = logging.getLogger(__name__)


class GeminiService(AIService):
    """Gemini (Google) AI Service - Using new google-genai SDK"""

    def __init__(self, api_key: str, model: str, base_url: str = None):
        super().__init__(api_key, model, base_url)
        self.client = genai.Client(api_key=self.api_key)
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

            # Merge if same role as previous
            if contents and contents[-1].role == role:
                contents[-1].parts[0].text += f"\n\n{content}"
            else:
                contents.append(types.Content(
                    role=role,
                    parts=[types.Part(text=content)]
                ))

        # Generation config
        generation_config = types.GenerateContentConfig(
            temperature=temperature,
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

        logger.info(f"[GEMINI] Calling API...")
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=generation_config
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[GEMINI ERROR] {type(e).__name__}: {error_msg}")
            if "504" in error_msg or "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
                raise Exception(f"Gemini API timeout: 요청 시간이 초과되었습니다. 녹취록이 너무 길 수 있습니다.")
            raise Exception(f"Gemini API error: {error_msg}")

        # Extract response with detailed logging
        logger.info(f"[GEMINI] Response type: {type(response)}")
        logger.info(f"[GEMINI] Response attributes: {dir(response)}")

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
            logger.info(f"[GEMINI] Candidate type: {type(candidate)}")
            logger.info(f"[GEMINI] Candidate attributes: {dir(candidate)}")

            if hasattr(candidate, 'content') and candidate.content:
                logger.info(f"[GEMINI] Content type: {type(candidate.content)}")
                if hasattr(candidate.content, 'parts') and candidate.content.parts:
                    logger.info(f"[GEMINI] Parts count: {len(candidate.content.parts)}")
                    parts_text = []
                    for i, part in enumerate(candidate.content.parts):
                        logger.info(f"[GEMINI] Part {i} type: {type(part)}, attrs: {dir(part)}")
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
            "finish_reason": finish_reason,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            },
            "provider": "gemini"
        }
