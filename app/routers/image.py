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

# Default image provider
DEFAULT_IMAGE_PROVIDER = os.getenv("DEFAULT_IMAGE_PROVIDER", "dall-e")

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
    provider_id = request.provider or DEFAULT_IMAGE_PROVIDER
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
            "is_default": provider_id == DEFAULT_IMAGE_PROVIDER,
        })

    return {"providers": providers, "default": DEFAULT_IMAGE_PROVIDER}
