import os
import time
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import load_config, get_provider
from ..usage import log_usage
from ..services import DallEService, ImagenService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["Image"])

# Default image provider (env fallback)
_DEFAULT_IMAGE_ENV = os.getenv("DEFAULT_IMAGE_PROVIDER", "dall-e")


def _get_default_image_provider() -> str:
    """Get default image provider from DB settings, fallback to env var."""
    from ..config import USE_POSTGRES, _get_pg_connection, _get_sqlite_connection
    try:
        if USE_POSTGRES:
            conn = _get_pg_connection()
        else:
            conn = _get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'default_image_provider'")
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else _DEFAULT_IMAGE_ENV
    except Exception:
        return _DEFAULT_IMAGE_ENV

# Fallback chains
IMAGE_FALLBACK = {
    "dall-e": ["imagen"],
    "imagen": ["dall-e"],
}

# Image service factory
IMAGE_SERVICE_MAP = {
    "dall-e": DallEService,
    "imagen": ImagenService,
}


class ImageRequest(BaseModel):
    prompt: str
    size: str = "1024x1024"
    style: str = "natural"
    provider: Optional[str] = None
    caller: Optional[str] = None


def _get_image_service(provider_id: str):
    """Factory function to get image generation service instance"""
    provider = get_provider(provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail=f"Image provider not found: {provider_id}")

    if not provider.enabled:
        raise HTTPException(status_code=400, detail=f"Image provider is disabled: {provider_id}")

    if not provider.api_key:
        raise HTTPException(status_code=400, detail=f"API key not configured for: {provider_id}")

    service_class = IMAGE_SERVICE_MAP.get(provider_id)
    if not service_class:
        raise HTTPException(status_code=400, detail=f"Unknown image provider: {provider_id}")

    return service_class(
        api_key=provider.api_key,
        model=provider.model,
        base_url=provider.base_url
    )


@router.post("/image")
async def generate_image(request: ImageRequest):
    """
    Generate an image from a text prompt.
    Supports fallback between providers.
    """
    provider_id = request.provider or _get_default_image_provider()
    logger.info(f"[IMAGE] Provider: {provider_id}, Prompt: {request.prompt[:80]}, Size: {request.size}")

    # Build attempt list
    providers_to_try = [provider_id]
    fallbacks = IMAGE_FALLBACK.get(provider_id, [])
    providers_to_try.extend(fallbacks)

    last_error = None
    for pid in providers_to_try:
        start_time = time.time()
        try:
            service = _get_image_service(pid)
            result = await service.generate(
                prompt=request.prompt,
                size=request.size,
                style=request.style,
            )
            elapsed_ms = int((time.time() - start_time) * 1000)

            # Log usage
            log_usage(
                provider=pid,
                model=result.get("model", ""),
                input_tokens=len(request.prompt),
                output_tokens=0,
                elapsed_ms=elapsed_ms,
                success=True,
                caller=request.caller
            )

            if pid != provider_id:
                logger.info(f"[IMAGE FALLBACK] {provider_id} failed, succeeded with {pid}")

            return {
                "data": result["data"],
                "media_type": result.get("media_type", "image/png"),
                "provider": result.get("provider", pid),
                "model": result.get("model", ""),
                "size": result.get("size", request.size),
                "revised_prompt": result.get("revised_prompt"),
                "elapsed_ms": elapsed_ms,
            }

        except HTTPException:
            elapsed_ms = int((time.time() - start_time) * 1000)
            last_error = f"Provider unavailable: {pid}"
            log_usage(provider=pid, model="", elapsed_ms=elapsed_ms, success=False,
                      error_message=last_error, caller=request.caller)
            if pid == providers_to_try[-1]:
                break
            logger.warning(f"[IMAGE FALLBACK] {pid} unavailable, trying next...")
            continue

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            last_error = str(e)
            log_usage(provider=pid, model="", elapsed_ms=elapsed_ms, success=False,
                      error_message=str(e)[:500], caller=request.caller)
            if pid == providers_to_try[-1]:
                break
            logger.warning(f"[IMAGE FALLBACK] {pid} failed ({e}), trying next...")
            continue

    return JSONResponse(
        status_code=500,
        content={"error": f"All image providers failed. Last error: {last_error}", "code": "PROVIDER_ERROR"}
    )


@router.get("/image/providers")
async def list_image_providers():
    """List available image generation providers"""
    config = load_config()
    default_image = _get_default_image_provider()

    providers = []
    for provider_id, provider in config.providers.items():
        if provider.service_type != "image":
            continue
        providers.append({
            "id": provider_id,
            "name": provider.name,
            "model": provider.model,
            "enabled": provider.enabled,
            "has_api_key": bool(provider.api_key),
            "is_default": provider_id == default_image,
        })

    return {"providers": providers, "default": default_image}


# ── Image Edit ─────────────────────────────────────────────

class ImageEditRequest(BaseModel):
    image: str              # base64 encoded image
    media_type: str         # image/jpeg or image/png
    edit_type: str = "remove_text"
    provider: Optional[str] = None  # "imagen" or "dall-e" (default: from settings)
    mask: Optional[str] = None  # optional manual mask (base64 PNG)
    caller: Optional[str] = None


# Default image edit provider
_DEFAULT_IMAGE_EDIT_ENV = os.getenv("DEFAULT_IMAGE_EDIT_PROVIDER", "imagen")


def _get_default_image_edit_provider() -> str:
    """Get default image edit provider from DB settings."""
    from ..config import USE_POSTGRES, _get_pg_connection, _get_sqlite_connection
    try:
        if USE_POSTGRES:
            conn = _get_pg_connection()
        else:
            conn = _get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'default_image_edit_provider'")
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else _DEFAULT_IMAGE_EDIT_ENV
    except Exception:
        return _DEFAULT_IMAGE_EDIT_ENV


@router.post("/image/edit")
async def edit_image(request: ImageEditRequest):
    """
    Edit an image. Currently supports 'remove_text' (text/watermark removal).
    Provider: 'imagen' (Vertex AI, high quality) or 'dall-e' (OpenAI).
    """
    if request.edit_type != "remove_text":
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported edit_type: {request.edit_type}. Currently only 'remove_text' is supported.", "code": "UNSUPPORTED_EDIT"}
        )

    if not request.image:
        return JSONResponse(
            status_code=400,
            content={"error": "No image provided", "code": "INVALID_REQUEST"}
        )

    # Determine provider
    edit_provider = request.provider or _get_default_image_edit_provider()
    if edit_provider not in ("imagen", "dall-e"):
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported edit provider: {edit_provider}. Use 'imagen' or 'dall-e'.", "code": "INVALID_PROVIDER"}
        )

    # Get API keys from providers
    config = load_config()
    google_key = None
    openai_key = None

    for pid in ["gemini-flash", "gemini-pro", "imagen"]:
        p = config.providers.get(pid)
        if p and p.api_key:
            google_key = p.api_key
            break

    for pid in ["dall-e", "chatgpt"]:
        p = config.providers.get(pid)
        if p and p.api_key:
            openai_key = p.api_key
            break

    if not google_key:
        return JSONResponse(status_code=400, content={"detail": "Google API key not configured (needed for text detection)", "error": "Google API key not configured", "code": "MISSING_KEY"})

    if edit_provider == "dall-e" and not openai_key:
        return JSONResponse(status_code=400, content={"detail": "OpenAI API key not configured (needed for DALL-E inpainting)", "error": "OpenAI API key not configured", "code": "MISSING_KEY"})

    start_time = time.time()
    try:
        from ..services.image_edit import ImageEditService
        service = ImageEditService(
            google_api_key=google_key,
            openai_api_key=openai_key or "",
            vertex_project=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
            vertex_location=os.getenv("VERTEX_AI_LOCATION", "us-central1"),
        )

        result = await service.remove_text(
            image_b64=request.image,
            media_type=request.media_type,
            mask_b64=request.mask,
            provider=edit_provider,
        )

        elapsed_ms = int((time.time() - start_time) * 1000)

        log_usage(
            provider=f"image-edit-{edit_provider}",
            model=result.get("model", ""),
            input_tokens=len(request.image),
            output_tokens=result.get("regions_found", 0),
            elapsed_ms=elapsed_ms,
            success=True,
            caller=request.caller,
        )

        return {
            **result,
            "elapsed_ms": elapsed_ms,
        }

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        log_usage(
            provider=f"image-edit-{edit_provider}",
            model="",
            elapsed_ms=elapsed_ms,
            success=False,
            error_message=str(e)[:500],
            caller=request.caller,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"Image edit failed: {str(e)}", "error": str(e), "code": "EDIT_ERROR"}
        )


@router.get("/image/edit/providers")
async def list_image_edit_providers():
    """List available image edit providers and current default."""
    default_edit = _get_default_image_edit_provider()
    return {
        "providers": [
            {"id": "imagen", "name": "Imagen 3 (Vertex AI)", "description": "High quality, original size preserved"},
            {"id": "dall-e", "name": "DALL-E 2 (OpenAI)", "description": "1024x1024 square output"},
        ],
        "default": default_edit
    }
