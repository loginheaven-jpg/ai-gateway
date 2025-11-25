from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import asyncio
import time

from ..config import load_config, get_provider
from ..services import (
    ClaudeService,
    ChatGPTService,
    GeminiService,
    MoonshotService,
    PerplexityService
)

router = APIRouter(prefix="/api/ai", tags=["AI"])


class ChatRequest(BaseModel):
    provider: Optional[str] = None  # If None, use default
    messages: List[Dict[str, str]]
    system_prompt: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.7


class BatchChatRequest(BaseModel):
    providers: List[str]
    test_message: str = "Hello, please respond with a brief greeting."
    max_tokens: int = 100
    temperature: float = 0.7


class ChatResponse(BaseModel):
    content: str
    model: str
    provider: str
    usage: Dict[str, int]


def get_ai_service(provider_id: str):
    """Factory function to get the appropriate AI service"""
    provider = get_provider(provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

    if not provider.enabled:
        raise HTTPException(status_code=400, detail=f"Provider is disabled: {provider_id}")

    if not provider.api_key:
        raise HTTPException(status_code=400, detail=f"API key not configured for: {provider_id}")

    service_map = {
        "claude": ClaudeService,
        "openai": ChatGPTService,
        "gemini-pro": GeminiService,
        "gemini-flash": GeminiService,
        "moonshot": MoonshotService,
        "perplexity": PerplexityService
    }

    service_class = service_map.get(provider_id)
    if not service_class:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")

    return service_class(
        api_key=provider.api_key,
        model=provider.model,
        base_url=provider.base_url
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a chat request to an AI provider.

    If provider is not specified, uses the default provider from config.
    """
    try:
        # Determine provider
        config = load_config()
        provider_id = request.provider or config.default_provider

        # Get service
        service = get_ai_service(provider_id)

        # Make request
        result = await service.chat(
            messages=request.messages,
            system_prompt=request.system_prompt,
            max_tokens=request.max_tokens,
            temperature=request.temperature
        )

        return ChatResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/providers")
async def list_providers():
    """List all available AI providers with their status"""
    config = load_config()

    providers = []
    for provider_id, provider in config.providers.items():
        providers.append({
            "id": provider_id,
            "name": provider.name,
            "model": provider.model,
            "enabled": provider.enabled,
            "has_api_key": bool(provider.api_key),
            "is_default": provider_id == config.default_provider
        })

    return {"providers": providers, "default": config.default_provider}


@router.post("/batch-chat")
async def batch_chat(request: BatchChatRequest):
    """
    Send chat requests to multiple AI providers simultaneously.
    Returns results from all providers with timing information.
    """
    async def test_provider(provider_id: str):
        start_time = time.time()
        try:
            service = get_ai_service(provider_id)
            result = await service.chat(
                messages=[{"role": "user", "content": request.test_message}],
                max_tokens=request.max_tokens,
                temperature=request.temperature
            )
            elapsed = time.time() - start_time
            return {
                "provider": provider_id,
                "success": True,
                "response": result["content"][:500] if len(result["content"]) > 500 else result["content"],
                "model": result.get("model", ""),
                "elapsed_ms": int(elapsed * 1000)
            }
        except HTTPException as e:
            elapsed = time.time() - start_time
            return {
                "provider": provider_id,
                "success": False,
                "error": e.detail,
                "elapsed_ms": int(elapsed * 1000)
            }
        except Exception as e:
            elapsed = time.time() - start_time
            return {
                "provider": provider_id,
                "success": False,
                "error": str(e),
                "elapsed_ms": int(elapsed * 1000)
            }

    # Execute all provider tests concurrently
    tasks = [test_provider(p) for p in request.providers]
    results = await asyncio.gather(*tasks)

    return {"results": results}
