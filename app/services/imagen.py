import base64
import logging
from typing import Dict, Any
from google import genai
from google.genai import types
from .image_base import ImageService

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


class ImagenService(ImageService):
    """Google Imagen 3 Image Generation Service via Gemini API"""

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        style: str = "natural",
    ) -> Dict[str, Any]:
        aspect_ratio = IMAGEN_ASPECT_MAP.get(size, "1:1")

        logger.info(f"[IMAGEN] Prompt: {prompt[:80]}..., Aspect: {aspect_ratio}, Style: {style}")

        try:
            client = genai.Client(api_key=self.api_key)

            config = types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                safety_filter_level="BLOCK_ONLY_HIGH",
                person_generation="DONT_ALLOW",
                output_mime_type="image/png",
            )

            # Add style hint to prompt
            style_suffix = ""
            if style == "vivid":
                style_suffix = ", vibrant colors, dramatic lighting"
            elif style == "artistic":
                style_suffix = ", artistic style, painterly"

            response = client.models.generate_images(
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
