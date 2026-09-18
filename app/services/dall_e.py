import logging
from typing import Dict, Any
import httpx
from .image_base import ImageService
from .clients import get_async_openai

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


# GPT Image sizes (DALL-E sizes are not accepted)
GPT_IMAGE_SIZE_MAP = {
    "1024x1024": "1024x1024",
    "1080x1080": "1024x1024",
    "1792x1024": "1536x1024",
    "1920x1080": "1536x1024",
    "1024x1792": "1024x1536",
    "1080x1350": "1024x1536",
}


class DallEService(ImageService):
    """OpenAI image generation (provider id 'dall-e').

    DALL-E 2/3 were retired; the provider now defaults to a GPT Image model
    (gpt-image-2.5-flare: ~12s vs ~40s for gpt-image-2 at similar quality),
    which has no style / response_format parameters and always returns base64.
    """

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        style: str = "natural",
    ) -> Dict[str, Any]:
        if self.model.startswith("gpt-image"):
            return await self._generate_gpt_image(prompt, size, style)

        dalle_size = DALLE_SIZE_MAP.get(size, "1024x1024")
        dalle_style = style if style in ("natural", "vivid") else "natural"

        logger.info(f"[DALL-E] Prompt: {prompt[:80]}..., Size: {dalle_size}, Style: {dalle_style}")

        try:
            client = get_async_openai(self.api_key, None, httpx.Timeout(120.0, connect=30.0))

            response = await client.images.generate(
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

    async def _generate_gpt_image(self, prompt: str, size: str, style: str) -> Dict[str, Any]:
        image_size = GPT_IMAGE_SIZE_MAP.get(size, "1024x1024")
        if style == "vivid":
            prompt = prompt + ", vibrant colors, dramatic lighting"
        elif style == "artistic":
            prompt = prompt + ", artistic style, painterly"

        logger.info(f"[GPT-IMAGE] Model: {self.model}, Size: {image_size}, Style: {style}")
        try:
            client = get_async_openai(self.api_key, None, httpx.Timeout(120.0, connect=30.0))
            response = await client.images.generate(
                model=self.model,
                prompt=prompt,
                size=image_size,
                quality="medium",
                n=1,
            )
            image_data = response.data[0]
            logger.info(f"[GPT-IMAGE] Image generated, b64 length: {len(image_data.b64_json)}")
            return {
                "data": image_data.b64_json,
                "media_type": "image/png",
                "provider": "dall-e",
                "model": self.model,
                "size": image_size,
                "revised_prompt": getattr(image_data, "revised_prompt", None),
            }
        except Exception as e:
            logger.error(f"[GPT-IMAGE ERROR] {type(e).__name__}: {str(e)}")
            raise
