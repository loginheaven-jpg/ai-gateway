import logging
from typing import Dict, Any
from openai import OpenAI
import httpx
from .image_base import ImageService

logger = logging.getLogger(__name__)

# Map requested size to DALL-E 3 supported sizes
DALLE_SIZE_MAP = {
    "1024x1024": "1024x1024",
    "1792x1024": "1792x1024",
    "1024x1792": "1024x1792",
    # Common aliases
    "1080x1350": "1024x1792",  # portrait → closest DALL-E portrait
    "1080x1080": "1024x1024",
    "1920x1080": "1792x1024",
}


class DallEService(ImageService):
    """OpenAI DALL-E 3 Image Generation Service"""

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        style: str = "natural",
    ) -> Dict[str, Any]:
        dalle_size = DALLE_SIZE_MAP.get(size, "1024x1024")
        dalle_style = style if style in ("natural", "vivid") else "natural"

        logger.info(f"[DALL-E] Prompt: {prompt[:80]}..., Size: {dalle_size}, Style: {dalle_style}")

        try:
            client = OpenAI(
                api_key=self.api_key,
                timeout=httpx.Timeout(120.0, connect=30.0)
            )

            response = client.images.generate(
                model=self.model,
                prompt=prompt,
                size=dalle_size,
                style=dalle_style,
                quality="standard",
                n=1,
                response_format="b64_json"
            )

            image_data = response.data[0]
            revised_prompt = getattr(image_data, "revised_prompt", None)

            logger.info(f"[DALL-E] Image generated, b64 length: {len(image_data.b64_json)}")

            return {
                "data": image_data.b64_json,
                "media_type": "image/png",
                "provider": "dall-e",
                "model": self.model,
                "size": dalle_size,
                "revised_prompt": revised_prompt,
            }

        except Exception as e:
            logger.error(f"[DALL-E ERROR] {type(e).__name__}: {str(e)}")
            raise
