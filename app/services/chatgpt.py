from openai import OpenAI
from typing import List, Dict, Any, Optional
from .base import AIService


class ChatGPTService(AIService):
    """ChatGPT (OpenAI) AI Service"""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        # Use configured OpenAI endpoint
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

        # Prepend system message if provided
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        # Call OpenAI API using SDK
        response = client.chat.completions.create(
            model=self.model,
            messages=all_messages,
            temperature=temperature,
            max_completion_tokens=max_tokens
        )

        return {
            "content": response.choices[0].message.content,
            "model": response.model,
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens
            },
            "provider": "chatgpt"
        }
