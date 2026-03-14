from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncGenerator


class AIService(ABC):
    """Base class for AI services"""

    def __init__(self, api_key: str, model: str, base_url: str = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """
        Send a chat request to the AI provider.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature

        Returns:
            Dict with 'content', 'model', 'usage' keys
        """
        pass

    async def stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        """
        Stream a chat response from the AI provider via SSE.
        Yields JSON strings for each chunk.
        Default implementation falls back to non-streaming chat.
        """
        result = await self.chat(messages, system_prompt, max_tokens, temperature)
        yield result["content"]
