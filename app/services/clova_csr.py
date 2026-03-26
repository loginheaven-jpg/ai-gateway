import logging
from typing import Dict, Any
import httpx
from .stt_base import STTService

logger = logging.getLogger(__name__)

# CSR API language codes
CSR_LANGUAGE_MAP = {
    "ko": "Kor",
    "en": "Eng",
    "ja": "Jpn",
    "zh": "Chn",
}


class ClovaCsrService(STTService):
    """Naver CLOVA Speech Recognition (CSR) - Short audio, sync API.

    Authentication: Client ID + Client Secret stored as "client_id:client_secret" in api_key.
    Max audio: 60 seconds, 10MB.
    Endpoint: https://naveropenapi.apigw.ntruss.com/recog/v1/stt?lang={lang}
    """

    async def recognize(
        self,
        audio_data: bytes,
        language: str = "ko",
        filename: str = "audio.webm"
    ) -> Dict[str, Any]:
        csr_lang = CSR_LANGUAGE_MAP.get(language, "Kor")
        logger.info(f"[CLOVA-CSR] Language: {language} -> {csr_lang}, File: {filename}, Size: {len(audio_data)} bytes")

        try:
            # api_key is stored as "client_id:client_secret"
            parts = self.api_key.split(":", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise Exception("CLOVA CSR requires api_key in 'client_id:client_secret' format")

            client_id, client_secret = parts

            url = f"{self.base_url}/recog/v1/stt?lang={csr_lang}"

            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
                response = await client.post(
                    url,
                    headers={
                        "X-NCP-APIGW-API-KEY-ID": client_id,
                        "X-NCP-APIGW-API-KEY": client_secret,
                        "Content-Type": "application/octet-stream",
                    },
                    content=audio_data,
                )

                if response.status_code != 200:
                    error_text = response.text[:500]
                    logger.error(f"[CLOVA-CSR ERROR] HTTP {response.status_code}: {error_text}")
                    raise Exception(f"CLOVA CSR API error: {response.status_code} {error_text}")

                result = response.json()

            text = result.get("text", "")
            logger.info(f"[CLOVA-CSR] Recognized: {len(text)} chars")

            return {
                "text": text,
                "language": language,
                "duration_sec": 0.0,  # CSR API does not return duration
                "provider": "clova-csr",
                "model": "clova-csr"
            }

        except Exception as e:
            logger.error(f"[CLOVA-CSR ERROR] {type(e).__name__}: {str(e)}")
            raise
