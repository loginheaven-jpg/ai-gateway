import io
import logging
from typing import Dict, Any, Optional
import httpx
from .stt_base import STTService
from .clients import get_async_openai

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
            client = get_async_openai(self.api_key, self.base_url, httpx.Timeout(120.0, connect=30.0))

            # Create file-like object from bytes
            audio_file = io.BytesIO(audio_data)
            audio_file.name = filename

            # whisper-1 reports the duration only with verbose_json; the newer
            # transcribe models reject verbose_json and report it as usage.seconds.
            response = await client.audio.transcriptions.create(
                model=self.model,
                file=audio_file,
                language=language,
                response_format="verbose_json" if self.model.startswith("whisper") else "json",
            )

            text = response.text or ""
            duration_sec = getattr(response, "duration", None)
            if not duration_sec:
                usage = getattr(response, "usage", None)
                duration_sec = getattr(usage, "seconds", None) if getattr(usage, "type", None) == "duration" else None
            duration_sec = duration_sec or 0.0

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
