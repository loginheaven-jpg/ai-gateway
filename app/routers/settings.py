from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..config import load_config, save_config, update_provider, reset_providers, ProviderConfig, AIConfig

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class ProviderUpdateRequest(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None


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
            "enabled": provider.enabled
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
        "enabled": provider.enabled
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
                "enabled": updated.enabled
            }
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/default-provider")
async def set_default_provider(request: DefaultProviderRequest):
    """Set the default AI provider"""
    config = load_config()

    if request.provider not in config.providers:
        raise HTTPException(status_code=404, detail=f"Provider not found: {request.provider}")

    config.default_provider = request.provider
    save_config(config)

    return {"success": True, "default_provider": request.provider}


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
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid configuration: {str(e)}")
