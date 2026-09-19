from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Dict, Optional

import asyncio
import os

from ..config import (
    load_config, save_config, update_provider, reset_providers, set_default_provider as set_default_provider_config,
    get_setting, set_setting, ProviderConfig, AIConfig, ConfigUnavailable,
)
from ..usage import get_usage_stats, get_recent_logs
from ..cache import response_cache
from ..auth import require_admin
from ..model_options import effective_options, REASONING_LEVELS

router = APIRouter(
    prefix="/api/settings",
    tags=["Settings"],
    dependencies=[Depends(require_admin)],
)


class ProviderUpdateRequest(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None
    # Default call options for this alias; {} clears them (back to recommended):
    # {"reasoning": "off|low|medium|high", "fallback_model": "...", "fallback_after_s": 6}
    options: Optional[Dict[str, Any]] = None


class DefaultProviderRequest(BaseModel):
    provider: str


@router.get("/providers")
async def get_all_providers():
    """Get all provider configurations (API keys masked)"""
    config = load_config()

    providers = {}
    for provider_id, provider in config.providers.items():
        providers[provider_id] = {
            "name": provider.name,
            "api_key": mask_api_key(provider.api_key),
            "model": provider.model,
            "base_url": provider.base_url,
            "enabled": provider.enabled,
            "service_type": provider.service_type,
            "options": provider.options,
            "effective_options": effective_options(provider_id, provider) if provider.service_type == "chat" else {},
        }

    return {
        "providers": providers,
        "default_provider": config.default_provider
    }


@router.get("/provider/{provider_id}")
async def get_provider(provider_id: str):
    """Get a specific provider configuration"""
    config = load_config()

    if provider_id not in config.providers:
        raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

    provider = config.providers[provider_id]
    return {
        "id": provider_id,
        "name": provider.name,
        "api_key": mask_api_key(provider.api_key),
        "model": provider.model,
        "base_url": provider.base_url,
        "enabled": provider.enabled,
        "options": provider.options,
        "effective_options": effective_options(provider_id, provider) if provider.service_type == "chat" else {},
    }


@router.put("/provider/{provider_id}")
async def update_provider_config(provider_id: str, request: ProviderUpdateRequest):
    """Update a provider configuration"""
    try:
        updates = {}

        if request.api_key is not None:
            updates["api_key"] = request.api_key

        if request.model is not None:
            updates["model"] = request.model

        if request.enabled is not None:
            updates["enabled"] = request.enabled

        if request.options is not None:
            opts = {k: v for k, v in request.options.items() if v is not None}
            if "reasoning" in opts and opts["reasoning"] not in REASONING_LEVELS:
                raise HTTPException(status_code=400, detail=f"reasoning must be one of {REASONING_LEVELS}")
            if "fallback_after_s" in opts and not isinstance(opts["fallback_after_s"], (int, float)):
                raise HTTPException(status_code=400, detail="fallback_after_s must be a number")
            updates["options"] = opts

        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")

        updated = update_provider(provider_id, updates)

        return {
            "success": True,
            "provider": {
                "id": provider_id,
                "name": updated.name,
                "api_key": mask_api_key(updated.api_key),
                "model": updated.model,
                "enabled": updated.enabled,
                "options": updated.options,
                "effective_options": effective_options(provider_id, updated),
            }
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (HTTPException, ConfigUnavailable):
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/default-provider")
async def set_default_provider(request: DefaultProviderRequest):
    """Set the default AI provider"""
    try:
        set_default_provider_config(request.provider)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Provider not found: {request.provider}")

    return {"success": True, "default_provider": request.provider}


@router.put("/default-stt-provider")
async def set_default_stt_provider(request: DefaultProviderRequest):
    """Set the default STT provider"""
    config = load_config()

    if request.provider not in config.providers:
        raise HTTPException(status_code=404, detail=f"Provider not found: {request.provider}")

    if config.providers[request.provider].service_type != "stt":
        raise HTTPException(status_code=400, detail=f"Not an STT provider: {request.provider}")

    set_setting("default_stt_provider", request.provider)

    return {"success": True, "default_stt_provider": request.provider}


@router.get("/default-stt-provider")
async def get_default_stt_provider():
    """Get the default STT provider"""
    default = get_setting("default_stt_provider") or os.getenv("DEFAULT_STT_PROVIDER", "whisper")
    return {"default_stt_provider": default}


@router.put("/default-image-provider")
async def set_default_image_provider(request: DefaultProviderRequest):
    """Set the default Image generation provider"""
    config = load_config()

    if request.provider not in config.providers:
        raise HTTPException(status_code=404, detail=f"Provider not found: {request.provider}")

    if config.providers[request.provider].service_type != "image":
        raise HTTPException(status_code=400, detail=f"Not an Image provider: {request.provider}")

    set_setting("default_image_provider", request.provider)

    return {"success": True, "default_image_provider": request.provider}


@router.get("/default-image-provider")
async def get_default_image_provider():
    """Get the default Image generation provider"""
    default = get_setting("default_image_provider") or os.getenv("DEFAULT_IMAGE_PROVIDER", "dall-e")
    return {"default_image_provider": default}


@router.put("/default-image-edit-provider")
async def set_default_image_edit_provider(request: DefaultProviderRequest):
    """Set the default Image Edit provider (imagen or dall-e)"""
    if request.provider not in ("imagen", "dall-e"):
        raise HTTPException(status_code=400, detail=f"Invalid image edit provider: {request.provider}. Use 'imagen' or 'dall-e'.")

    set_setting("default_image_edit_provider", request.provider)

    return {"success": True, "default_image_edit_provider": request.provider}


def mask_api_key(api_key: str) -> str:
    """Mask API key for display (show first 8 and last 4 characters)"""
    if not api_key:
        return ""
    if len(api_key) <= 12:
        return "*" * len(api_key)
    return api_key[:8] + "*" * (len(api_key) - 12) + api_key[-4:]


@router.post("/reset")
async def reset_all_providers():
    """Reset all providers to default configuration"""
    config = reset_providers()
    return {
        "success": True,
        "message": "All providers reset to default configuration",
        "providers": list(config.providers.keys())
    }


@router.get("/export")
async def export_config():
    """Export all provider configurations as JSON (includes API keys)"""
    config = load_config()
    return {
        "providers": {k: v.model_dump() for k, v in config.providers.items()},
        "default_provider": config.default_provider
    }


@router.post("/import")
async def import_config(data: dict):
    """Import provider configurations from JSON"""
    try:
        providers = {}
        for key, value in data.get("providers", {}).items():
            providers[key] = ProviderConfig(**value)

        config = AIConfig(
            providers=providers,
            default_provider=data.get("default_provider", "claude")
        )
        save_config(config)

        return {
            "success": True,
            "message": "Configuration imported successfully",
            "providers": list(config.providers.keys())
        }
    except ConfigUnavailable:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid configuration: {str(e)}")


# ── Usage Stats ──────────────────────────────────────────────

@router.get("/usage/stats")
async def usage_stats(days: int = 7, provider: Optional[str] = None):
    """Get usage statistics for the given period."""
    try:
        return await asyncio.to_thread(get_usage_stats, days=days, provider=provider)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/usage/logs")
async def usage_logs(limit: int = Query(50, ge=1, le=500)):
    """Get recent usage log entries."""
    try:
        return {"logs": await asyncio.to_thread(get_recent_logs, limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Cache Management ─────────────────────────────────────────

@router.get("/cache/stats")
async def cache_stats():
    """Get cache statistics."""
    return response_cache.stats()


@router.post("/cache/clear")
async def cache_clear():
    """Clear all cached responses."""
    response_cache.clear()
    return {"success": True, "message": "Cache cleared"}
