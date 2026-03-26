import json
import logging
from typing import Dict, Any, Optional
import httpx
from .stt_base import STTService

logger = logging.getLogger(__name__)

# ISO 639-1 to CLOVA Speech language code mapping
LANGUAGE_MAP = {
    "ko": "ko-KR",
    "en": "en-US",
    "ja": "ja",
    "zh": "zh-cn",
}


class ClovaSttService(STTService):
    """Naver CLOVA Speech Long STT Service.

    Authentication: X-CLOVASPEECH-API-KEY (single key).
    Max audio: 80 minutes.
    Endpoint: {CLOVA_INVOKE_URL}/recognizer/upload
    """

    async def recognize(
        self,
        audio_data: bytes,
        language: str = "ko",
        filename: str = "audio.webm"
    ) -> Dict[str, Any]:
        clova_lang = LANGUAGE_MAP.get(language, "ko-KR")
        logger.info(f"[CLOVA] Language: {language} -> {clova_lang}, File: {filename}, Size: {len(audio_data)} bytes")

        try:
            params = json.dumps({
                "language": clova_lang,
                "completion": "sync",
                "fullText": True,
                "noiseFiltering": True,
            })

            # CLOVA Speech Long uses multipart upload to /recognizer/upload
            upload_url = f"{self.base_url}/recognizer/upload"

            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
                response = await client.post(
                    upload_url,
                    headers={
                        "X-CLOVASPEECH-API-KEY": self.api_key,
                    },
                    files={
                        "media": (filename, audio_data, "application/octet-stream"),
                    },
                    data={
                        "params": params,
                    }
                )

                if response.status_code != 200:
                    error_text = response.text[:500]
                    logger.error(f"[CLOVA ERROR] HTTP {response.status_code}: {error_text}")
                    raise Exception(f"CLOVA Speech API error: {response.status_code} {error_text}")

                result = response.json()

            # Extract text from response
            text = result.get("text", "")

            # Calculate duration from segments if available
            duration_sec = 0.0
            segments = result.get("segments", [])
            if segments:
                last_segment = segments[-1]
                duration_sec = last_segment.get("end", 0) / 1000.0  # ms to sec

            logger.info(f"[CLOVA-SPEECH] Recognized: {len(text)} chars, Duration: {duration_sec}s")

            return {
                "text": text,
                "language": language,
                "duration_sec": round(duration_sec, 1),
                "provider": "clova-speech",
                "model": "clova-speech-long"
            }

        except Exception as e:
            logger.error(f"[CLOVA ERROR] {type(e).__name__}: {str(e)}")
            raise
