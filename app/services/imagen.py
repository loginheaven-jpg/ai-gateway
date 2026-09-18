import asyncio
import base64
import io
import logging
from typing import Dict, Any
from google import genai
from google.genai import types
from .image_base import ImageService
from .clients import get_genai_client

logger = logging.getLogger(__name__)

# Map requested size to Imagen aspect ratio
IMAGEN_ASPECT_MAP = {
    "1024x1024": "1:1",
    "1080x1080": "1:1",
    "1080x1350": "3:4",    # portrait 4:5 → closest 3:4
    "1024x1792": "9:16",
    "1792x1024": "16:9",
    "1920x1080": "16:9",
    # Direct aspect ratios
    "1:1": "1:1",
    "3:4": "3:4",
    "4:3": "4:3",
    "9:16": "9:16",
    "16:9": "16:9",
}


def _to_png(data: bytes) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).save(buf, format="PNG")
    return buf.getvalue()


class ImagenService(ImageService):
    """Google image generation via the Gemini API.

    Imagen models ("imagen-*") use generate_images. Imagen 3 was removed from
    the Gemini API, so the provider now defaults to a Gemini-native image model
    ("gemini-*-image"), which generates through generate_content with an IMAGE
    response modality.
    """

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        style: str = "natural",
    ) -> Dict[str, Any]:
        aspect_ratio = IMAGEN_ASPECT_MAP.get(size, "1:1")

        logger.info(f"[IMAGEN] Prompt: {prompt[:80]}..., Aspect: {aspect_ratio}, Style: {style}")

        # Add style hint to prompt
        style_suffix = ""
        if style == "vivid":
            style_suffix = ", vibrant colors, dramatic lighting"
        elif style == "artistic":
            style_suffix = ", artistic style, painterly"

        if not self.model.startswith("imagen"):
            return await self._generate_gemini(prompt + style_suffix, aspect_ratio)

        try:
            client = get_genai_client(self.api_key)

            config = types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                safety_filter_level="BLOCK_ONLY_HIGH",
                person_generation="DONT_ALLOW",
                output_mime_type="image/png",
            )

            response = await client.aio.models.generate_images(
                model=self.model,
                prompt=prompt + style_suffix,
                config=config,
            )

            if not response.generated_images:
                raise Exception("Imagen returned no images (possibly blocked by safety filter)")

            image_bytes = response.generated_images[0].image.image_bytes
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            logger.info(f"[IMAGEN] Image generated, bytes: {len(image_bytes)}")

            return {
                "data": image_b64,
                "media_type": "image/png",
                "provider": "imagen",
                "model": self.model,
                "size": aspect_ratio,
            }

        except Exception as e:
            logger.error(f"[IMAGEN ERROR] {type(e).__name__}: {str(e)}")
            raise

    async def _generate_gemini(self, prompt: str, aspect_ratio: str) -> Dict[str, Any]:
        try:
            client = get_genai_client(self.api_key)
            response = await client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                    ),
                ),
            )

            image_bytes, media_type = None, "image/png"
            for candidate in response.candidates or []:
                parts = candidate.content.parts if candidate.content else None
                for part in parts or []:
                    if part.inline_data and part.inline_data.data:
                        image_bytes = part.inline_data.data
                        media_type = part.inline_data.mime_type or media_type
                        break
                if image_bytes:
                    break
            if not image_bytes:
                reason = response.candidates[0].finish_reason if response.candidates else "no candidates"
                raise Exception(f"Gemini image model returned no image (finish_reason={reason})")
            if media_type != "image/png":
                # Gemini API can't be asked for PNG; keep the endpoint's PNG output
                image_bytes = await asyncio.to_thread(_to_png, image_bytes)
                media_type = "image/png"

            logger.info(f"[IMAGEN] Image generated with {self.model}, bytes: {len(image_bytes)}")
            return {
                "data": base64.b64encode(image_bytes).decode("utf-8"),
                "media_type": media_type,
                "provider": "imagen",
                "model": self.model,
                "size": aspect_ratio,
            }

        except Exception as e:
            logger.error(f"[IMAGEN ERROR] {type(e).__name__}: {str(e)}")
            raise
