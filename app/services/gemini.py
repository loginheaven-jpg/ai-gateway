import google.generativeai as genai
from typing import List, Dict, Any, Optional
from .base import AIService


class GeminiService(AIService):
    """Gemini (Google) AI Service - Stateless Implementation"""

    def __init__(self, api_key: str, model: str, base_url: str = None):
        super().__init__(api_key, model, base_url)
        genai.configure(api_key=self.api_key)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        # 1. Create model with system instruction
        model = genai.GenerativeModel(
            self.model,
            system_instruction=system_prompt
        )

        # 2. Preprocess messages (merge consecutive same roles)
        gemini_contents = []

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
        try:
            response = await model.generate_content_async(
                gemini_contents,
                generation_config=genai.types.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
                safety_settings=safety_settings
            )
        except Exception as e:
            return {"error": str(e), "content": f"Error: {str(e)}", "provider": "gemini", "model": self.model}

        # 5. Extract response text with error handling
        try:
            content_text = response.text
        except ValueError:
            if response.candidates:
                finish_reason = response.candidates[0].finish_reason.name
                content_text = f"[Blocked/Error: {finish_reason}]"
            else:
                content_text = "[No content returned]"

        # 6. Extract metadata
        usage_metadata = getattr(response, 'usage_metadata', None)
        input_tokens = getattr(usage_metadata, 'prompt_token_count', 0) if usage_metadata else 0
        output_tokens = getattr(usage_metadata, 'candidates_token_count', 0) if usage_metadata else 0

        finish_reason = "UNKNOWN"
        if response.candidates:
            finish_reason = response.candidates[0].finish_reason.name

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
