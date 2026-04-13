"""Image Edit Service — Text removal pipeline.

Pipeline:
1. Gemini Vision → detect text bounding boxes (JSON)
2. PIL/Pillow → generate mask image (transparent = edit area)
3. OpenAI images.edit → inpaint masked regions with background
"""
import io
import json
import base64
import logging
from typing import Dict, Any, Optional, List

from PIL import Image, ImageDraw
from google import genai
from google.genai import types
from openai import OpenAI
import httpx

logger = logging.getLogger(__name__)

# Prompt for Gemini text detection
TEXT_DETECTION_PROMPT = (
    "Detect ALL text regions in this image including signs, watermarks, labels, "
    "handwriting, printed text, and any visible characters. "
    "Return a JSON array. Each item: {\"box_2d\": [ymin, xmin, ymax, xmax], \"label\": \"detected text\"}. "
    "Coordinates normalized to 0-1000 scale. "
    "If no text is found, return an empty array []."
)

# Prompt for OpenAI inpainting
INPAINT_PROMPT = (
    "Remove all text and fill the area with the surrounding background texture seamlessly. "
    "The result should look natural as if there was never any text."
)


class ImageEditService:
    """Text removal service using Gemini (detection) + PIL (mask) + OpenAI (inpainting)."""

    def __init__(self, google_api_key: str, openai_api_key: str):
        self.google_api_key = google_api_key
        self.openai_api_key = openai_api_key

    async def remove_text(
        self,
        image_b64: str,
        media_type: str = "image/jpeg",
        mask_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove text from an image.

        Args:
            image_b64: Original image as base64 string
            media_type: MIME type of the image
            mask_b64: Optional manual mask (base64 PNG). If provided, skip text detection.

        Returns:
            Dict with 'data' (edited image base64), 'media_type', 'regions_found', etc.
        """
        # Decode original image
        image_bytes = base64.b64decode(image_b64)
        original = Image.open(io.BytesIO(image_bytes))
        orig_width, orig_height = original.size
        logger.info(f"[IMAGE-EDIT] Original size: {orig_width}x{orig_height}, type: {media_type}")

        if mask_b64:
            # Manual mask provided — skip detection
            regions_found = -1  # unknown
            mask_bytes = base64.b64decode(mask_b64)
            logger.info(f"[IMAGE-EDIT] Using provided mask")
        else:
            # Step 1: Detect text regions with Gemini Vision
            boxes = await self._detect_text_regions(image_b64, media_type)
            regions_found = len(boxes)
            logger.info(f"[IMAGE-EDIT] Detected {regions_found} text regions")

            if regions_found == 0:
                # No text found — return original image as-is
                return {
                    "data": image_b64,
                    "media_type": media_type,
                    "edit_type": "remove_text",
                    "regions_found": 0,
                }

            # Step 2: Generate mask from bounding boxes
            mask_bytes = self._create_mask(boxes, orig_width, orig_height)

        # Step 3: Resize image to 1024x1024 square (DALL-E requirement)
        resized_image_bytes = self._resize_to_square(original, 1024)
        resized_mask_bytes = self._resize_mask_to_square(mask_bytes, orig_width, orig_height, 1024)

        # Step 4: Inpaint with OpenAI
        result_b64 = await self._inpaint(resized_image_bytes, resized_mask_bytes)

        return {
            "data": result_b64,
            "media_type": "image/png",
            "edit_type": "remove_text",
            "regions_found": regions_found,
        }

    async def _detect_text_regions(self, image_b64: str, media_type: str) -> List[Dict]:
        """Use Gemini Vision to detect text bounding boxes."""
        try:
            client = genai.Client(api_key=self.google_api_key)

            image_part = types.Part(
                inline_data=types.Blob(
                    mime_type=media_type,
                    data=base64.b64decode(image_b64),
                )
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[image_part, TEXT_DETECTION_PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )

            text = response.text.strip()
            boxes = json.loads(text)

            if not isinstance(boxes, list):
                logger.warning(f"[IMAGE-EDIT] Gemini returned non-list: {text[:200]}")
                return []

            # Validate box format
            valid_boxes = []
            for box in boxes:
                if isinstance(box, dict) and "box_2d" in box:
                    coords = box["box_2d"]
                    if isinstance(coords, list) and len(coords) == 4:
                        valid_boxes.append(box)

            return valid_boxes

        except Exception as e:
            logger.error(f"[IMAGE-EDIT] Text detection failed: {type(e).__name__}: {e}")
            raise Exception(f"Text detection failed: {e}")

    def _create_mask(self, boxes: List[Dict], width: int, height: int) -> bytes:
        """Create RGBA mask from bounding boxes. Transparent = areas to edit."""
        # OpenAI convention: opaque = keep, transparent (alpha=0) = edit
        mask = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(mask)

        padding = max(10, int(min(width, height) * 0.015))  # ~1.5% padding

        for box in boxes:
            coords = box["box_2d"]  # [ymin, xmin, ymax, xmax] normalized 0-1000
            y1 = int(coords[0] / 1000 * height)
            x1 = int(coords[1] / 1000 * width)
            y2 = int(coords[2] / 1000 * height)
            x2 = int(coords[3] / 1000 * width)

            # Add padding
            y1 = max(0, y1 - padding)
            x1 = max(0, x1 - padding)
            y2 = min(height, y2 + padding)
            x2 = min(width, x2 + padding)

            # Transparent rectangle = area to edit
            draw.rectangle([x1, y1, x2, y2], fill=(0, 0, 0, 0))

        buf = io.BytesIO()
        mask.save(buf, format="PNG")
        return buf.getvalue()

    def _resize_to_square(self, img: Image.Image, size: int) -> bytes:
        """Resize image to square, maintaining aspect ratio with padding."""
        # Convert to RGBA for OpenAI compatibility
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Create square canvas
        square = Image.new("RGBA", (size, size), (0, 0, 0, 255))

        # Calculate resize dimensions (fit within square)
        ratio = min(size / img.width, size / img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Center on canvas
        offset_x = (size - new_w) // 2
        offset_y = (size - new_h) // 2
        square.paste(resized, (offset_x, offset_y))

        buf = io.BytesIO()
        square.save(buf, format="PNG")
        return buf.getvalue()

    def _resize_mask_to_square(self, mask_bytes: bytes, orig_w: int, orig_h: int, size: int) -> bytes:
        """Resize mask to match the square image dimensions."""
        mask = Image.open(io.BytesIO(mask_bytes))
        if mask.mode != "RGBA":
            mask = mask.convert("RGBA")

        # Create square mask (fully opaque = keep everything by default)
        square_mask = Image.new("RGBA", (size, size), (255, 255, 255, 255))

        # Resize mask to fit
        ratio = min(size / orig_w, size / orig_h)
        new_w = int(orig_w * ratio)
        new_h = int(orig_h * ratio)
        resized_mask = mask.resize((new_w, new_h), Image.Resampling.NEAREST)

        # Center on canvas
        offset_x = (size - new_w) // 2
        offset_y = (size - new_h) // 2
        square_mask.paste(resized_mask, (offset_x, offset_y))

        buf = io.BytesIO()
        square_mask.save(buf, format="PNG")
        return buf.getvalue()

    async def _inpaint(self, image_bytes: bytes, mask_bytes: bytes) -> str:
        """Use OpenAI images.edit to inpaint masked regions."""
        try:
            client = OpenAI(
                api_key=self.openai_api_key,
                timeout=httpx.Timeout(120.0, connect=30.0),
            )

            image_file = io.BytesIO(image_bytes)
            image_file.name = "image.png"

            mask_file = io.BytesIO(mask_bytes)
            mask_file.name = "mask.png"

            response = client.images.edit(
                model="dall-e-2",
                image=image_file,
                mask=mask_file,
                prompt=INPAINT_PROMPT,
                size="1024x1024",
                n=1,
                response_format="b64_json",
            )

            result_b64 = response.data[0].b64_json
            logger.info(f"[IMAGE-EDIT] Inpainting complete, b64 length: {len(result_b64)}")
            return result_b64

        except Exception as e:
            logger.error(f"[IMAGE-EDIT] Inpainting failed: {type(e).__name__}: {e}")
            raise Exception(f"Inpainting failed: {e}")
