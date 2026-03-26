from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class STTService(ABC):
    """Base class for Speech-to-Text services"""

    def __init__(self, api_key: str, model: str, base_url: str = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    @abstractmethod
    async def recognize(
        self,
        audio_data: bytes,
        language: str = "ko",
        filename: str = "audio.webm"
    ) -> Dict[str, Any]:
        """
        Recognize speech from audio data.

        Args:
            audio_data: Raw audio binary data
            language: Language code (ISO 639-1: ko, en, ja, zh)
            filename: Original filename for format detection

        Returns:
            Dict with 'text', 'language', 'duration_sec', 'provider', 'model'
        """
        pass
