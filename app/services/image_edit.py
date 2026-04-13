"""Image Edit Service — Text removal pipeline.

Pipeline:
1. Gemini Vision → detect text bounding boxes (JSON)
2. PIL/Pillow → generate mask image
3. Inpainting → DALL-E 2 (OpenAI) or Imagen 3 (Vertex AI)

Provider selection:
- provider="dall-e"  → OpenAI DALL-E 2 inpainting (1024x1024 square)
- provider="imagen"  → Vertex AI Imagen 3 inpainting (original size preserved)
- provider=None      → default (imagen)
"""
import io
import os
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

# Prompt for Gemini text detection — generous bounding boxes
TEXT_DETECTION_PROMPT = (
    "Detect ALL text regions in this image. Include signs, watermarks, labels, logos, "
    "handwriting, printed text, subtitles, captions, and any visible characters or symbols. "
    "Make bounding boxes GENEROUS — extend each box well beyond the text edges to fully cover "
    "shadows, outlines, and decorative elements around the text. "
    "Return a JSON array. Each item: {\"box_2d\": [ymin, xmin, ymax, xmax], \"label\": \"detected text\"}. "
    "Coordinates normalized to 0-1000 scale. "
    "If no text is found, return an empty array []."
)

# Inpainting prompts
DALLE_INPAINT_PROMPT = (
    "Completely remove all text, letters, characters, watermarks, and any writing. "
    "Fill the entire masked area with the surrounding background texture, colors, and patterns seamlessly. "
    "The result must look completely natural with absolutely no trace of any text remaining."
)

IMAGEN_INPAINT_PROMPT = (
    "Remove all text and writing from this area. "
    "Fill with the surrounding background seamlessly."
)


class ImageEditService:
    """Text removal service with provider selection (DALL-E or Imagen)."""

    def __init__(
        self,
        google_api_key: str,
        openai_api_key: str = "",
        vertex_project: str = "",
        vertex_location: str = "us-central1",
    ):
        self.google_api_key = google_api_key
        self.openai_api_key = openai_api_key
        self.vertex_project = vertex_project
        self.vertex_location = vertex_location

    async def remove_text(
        self,
        image_b64: str,
        media_type: str = "image/jpeg",
        mask_b64: Optional[str] = None,
        provider: str = "imagen",
    ) -> Dict[str, Any]:
        """Remove text from an image.

        Args:
            image_b64: Original image as base64 string
            media_type: MIME type of the image
            mask_b64: Optional manual mask (base64 PNG). If provided, skip text detection.
            provider: "imagen" (Vertex AI) or "dall-e" (OpenAI)
        """
        # Decode original image
        image_bytes = base64.b64decode(image_b64)
        original = Image.open(io.BytesIO(image_bytes))
        orig_width, orig_height = original.size
        logger.info(f"[IMAGE-EDIT] Original: {orig_width}x{orig_height}, provider: {provider}")

        if mask_b64:
            regions_found = -1
            mask_bytes = base64.b64decode(mask_b64)
            logger.info(f"[IMAGE-EDIT] Using provided mask")
        else:
            # Step 1: Detect text regions with Gemini Vision
            boxes = await self._detect_text_regions(image_b64, media_type)
            regions_found = len(boxes)
            logger.info(f"[IMAGE-EDIT] Detected {regions_found} text regions")

            if regions_found == 0:
                return {
                    "data": image_b64,
                    "media_type": media_type,
                    "edit_type": "remove_text",
                    "regions_found": 0,
                    "provider": provider,
                }

            # Step 2: Generate mask from bounding boxes
            mask_bytes = self._create_mask(boxes, orig_width, orig_height)

        # Step 3: Inpaint with selected provider
        if provider == "dall-e":
            result_b64 = await self._inpaint_dalle(original, mask_bytes, orig_width, orig_height)
            model_name = "dall-e-2"
        else:
            result_b64 = await self._inpaint_imagen(image_bytes, mask_bytes, media_type)
            model_name = "imagen-3.0-capability-001"

        return {
            "data": result_b64,
            "media_type": "image/png",
            "edit_type": "remove_text",
            "regions_found": regions_found,
            "provider": provider,
            "model": model_name,
        }

    # ── Text Detection (Gemini Vision) ─────────────────────────

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

    # ── Mask Generation (PIL) ──────────────────────────────────

    def _merge_overlapping_boxes(self, boxes: List[Dict], padding: int, width: int, height: int) -> List[tuple]:
        """Merge overlapping/nearby bounding boxes into larger regions."""
        if not boxes:
            return []

        rects = []
        for box in boxes:
            coords = box["box_2d"]
            y1 = max(0, int(coords[0] / 1000 * height) - padding)
            x1 = max(0, int(coords[1] / 1000 * width) - padding)
            y2 = min(height, int(coords[2] / 1000 * height) + padding)
            x2 = min(width, int(coords[3] / 1000 * width) + padding)
            rects.append((x1, y1, x2, y2))

        merged = True
        while merged:
            merged = False
            new_rects = []
            used = set()
            for i in range(len(rects)):
                if i in used:
                    continue
                r1 = rects[i]
                for j in range(i + 1, len(rects)):
                    if j in used:
                        continue
                    r2 = rects[j]
                    gap = padding
                    if (r1[0] - gap <= r2[2] and r2[0] - gap <= r1[2] and
                            r1[1] - gap <= r2[3] and r2[1] - gap <= r1[3]):
                        r1 = (min(r1[0], r2[0]), min(r1[1], r2[1]),
                               max(r1[2], r2[2]), max(r1[3], r2[3]))
                        used.add(j)
                        merged = True
                new_rects.append(r1)
                used.add(i)
            rects = new_rects

        return rects

    def _create_mask(self, boxes: List[Dict], width: int, height: int) -> bytes:
        """Create mask from bounding boxes. White = areas to edit (Imagen convention)."""
        # Imagen convention: white = edit, black = keep (opposite of OpenAI)
        # We store in Imagen format and convert for OpenAI when needed
        mask = Image.new("L", (width, height), 0)  # black = keep
        draw = ImageDraw.Draw(mask)

        padding = max(15, int(min(width, height) * 0.05))
        merged_rects = self._merge_overlapping_boxes(boxes, padding, width, height)
        logger.info(f"[IMAGE-EDIT] {len(boxes)} boxes merged into {len(merged_rects)} regions")

        for (x1, y1, x2, y2) in merged_rects:
            draw.rectangle([x1, y1, x2, y2], fill=255)  # white = edit

        buf = io.BytesIO()
        mask.save(buf, format="PNG")
        return buf.getvalue()

    def _mask_to_openai_format(self, mask_bytes: bytes, size: int) -> bytes:
        """Convert Imagen mask (white=edit) to OpenAI mask (transparent=edit), resized to square."""
        mask_l = Image.open(io.BytesIO(mask_bytes)).convert("L")
        orig_w, orig_h = mask_l.size

        # Resize to fit in square
        ratio = min(size / orig_w, size / orig_h)
        new_w = int(orig_w * ratio)
        new_h = int(orig_h * ratio)
        resized = mask_l.resize((new_w, new_h), Image.Resampling.NEAREST)

        # Create RGBA square mask (fully opaque = keep by default)
        square = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        offset_x = (size - new_w) // 2
        offset_y = (size - new_h) // 2

        # Convert: white pixels in L mask → transparent in RGBA
        for y in range(new_h):
            for x in range(new_w):
                if resized.getpixel((x, y)) > 128:
                    square.putpixel((x + offset_x, y + offset_y), (0, 0, 0, 0))

        buf = io.BytesIO()
        square.save(buf, format="PNG")
        return buf.getvalue()

    def _resize_to_square(self, img: Image.Image, size: int) -> bytes:
        """Resize image to square, maintaining aspect ratio with padding."""
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        square = Image.new("RGBA", (size, size), (0, 0, 0, 255))
        ratio = min(size / img.width, size / img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        offset_x = (size - new_w) // 2
        offset_y = (size - new_h) // 2
        square.paste(resized, (offset_x, offset_y))

        buf = io.BytesIO()
        square.save(buf, format="PNG")
        return buf.getvalue()

    # ── Inpainting: DALL-E 2 (OpenAI) ─────────────────────────

    async def _inpaint_dalle(self, original: Image.Image, mask_bytes: bytes, orig_w: int, orig_h: int) -> str:
        """Inpaint using OpenAI DALL-E 2."""
        try:
            client = OpenAI(
                api_key=self.openai_api_key,
                timeout=httpx.Timeout(120.0, connect=30.0),
            )

            image_bytes = self._resize_to_square(original, 1024)
            openai_mask = self._mask_to_openai_format(mask_bytes, 1024)

            image_file = io.BytesIO(image_bytes)
            image_file.name = "image.png"
            mask_file = io.BytesIO(openai_mask)
            mask_file.name = "mask.png"

            response = client.images.edit(
                model="dall-e-2",
                image=image_file,
                mask=mask_file,
                prompt=DALLE_INPAINT_PROMPT,
                size="1024x1024",
                n=1,
                response_format="b64_json",
            )

            result_b64 = response.data[0].b64_json
            logger.info(f"[IMAGE-EDIT/DALL-E] Inpainting complete, b64 length: {len(result_b64)}")
            return result_b64

        except Exception as e:
            logger.error(f"[IMAGE-EDIT/DALL-E] Inpainting failed: {type(e).__name__}: {e}")
            raise Exception(f"DALL-E inpainting failed: {e}")

    # ── Inpainting: Imagen 3 (Vertex AI) ──────────────────────

    async def _inpaint_imagen(self, image_bytes: bytes, mask_bytes: bytes, media_type: str) -> str:
        """Inpaint using Vertex AI Imagen 3. Preserves original image size."""
        try:
            # Vertex AI requires service account auth, not API key
            creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")
            if creds_json:
                # Write temp credentials file
                import tempfile
                creds_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
                creds_file.write(creds_json)
                creds_file.close()
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_file.name

            client = genai.Client(
                vertexai=True,
                project=self.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT", ""),
                location=self.vertex_location,
            )

            # Build reference images
            from google.genai.types import (
                RawReferenceImage,
                MaskReferenceImage,
                MaskReferenceConfig,
                EditImageConfig,
            )

            raw_ref = RawReferenceImage(
                reference_image=types.Image(image_bytes=image_bytes),
                reference_id=0,
            )

            mask_ref = MaskReferenceImage(
                reference_id=1,
                reference_image=types.Image(image_bytes=mask_bytes),
                config=MaskReferenceConfig(
                    mask_mode="MASK_MODE_USER_PROVIDED",
                    mask_dilation=0.03,  # slight dilation for better coverage
                ),
            )

            response = client.models.edit_image(
                model="imagen-3.0-capability-001",
                prompt=IMAGEN_INPAINT_PROMPT,
                reference_images=[raw_ref, mask_ref],
                config=EditImageConfig(
                    edit_mode="EDIT_MODE_INPAINT_REMOVAL",
                    number_of_images=1,
                    output_mime_type="image/png",
                ),
            )

            if not response.generated_images:
                raise Exception("Imagen returned no images (possibly blocked by safety filter)")

            result_bytes = response.generated_images[0].image.image_bytes
            result_b64 = base64.b64encode(result_bytes).decode("utf-8")

            logger.info(f"[IMAGE-EDIT/IMAGEN] Inpainting complete, bytes: {len(result_bytes)}")
            return result_b64

        except Exception as e:
            logger.error(f"[IMAGE-EDIT/IMAGEN] Inpainting failed: {type(e).__name__}: {e}")
            raise Exception(f"Imagen inpainting failed: {e}")
