import io
import logging
from typing import Dict, Any, Optional
from openai import OpenAI
import httpx
from .stt_base import STTService

logger = logging.getLogger(__name__)


class WhisperService(STTService):
    """OpenAI Whisper Speech-to-Text Service"""

    async def recognize(
        self,
        audio_data: bytes,
        language: str = "ko",
        filename: str = "audio.webm"
    ) -> Dict[str, Any]:
        logger.info(f"[WHISPER] Language: {language}, File: {filename}, Size: {len(audio_data)} bytes")

        try:
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=httpx.Timeout(120.0, connect=30.0)
            )

            # Create file-like object from bytes
            audio_file = io.BytesIO(audio_data)
            audio_file.name = filename

            # Use verbose_json to get duration info
            response = client.audio.transcriptions.create(
                model=self.model,
                file=audio_file,
                language=language,
                response_format="verbose_json"
            )

            text = response.text or ""
            duration_sec = getattr(response, "duration", 0.0) or 0.0

            logger.info(f"[WHISPER] Recognized: {len(text)} chars, Duration: {duration_sec}s")

            return {
                "text": text,
                "language": language,
                "duration_sec": round(duration_sec, 1),
                "provider": "whisper",
                "model": self.model
            }

        except Exception as e:
            logger.error(f"[WHISPER ERROR] {type(e).__name__}: {str(e)}")
            raise
