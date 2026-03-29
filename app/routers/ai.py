from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import asyncio
import json
import time
import traceback

from ..config import load_config, get_provider
from ..usage import log_usage
from ..cache import response_cache
from ..services import (
    ClaudeService,
    ChatGPTService,
    GeminiService,
    MoonshotService,
    PerplexityService
)

# Debug logging
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["AI"])


FALLBACK_CHAINS = {
    "claude-sonnet": ["claude-haiku", "chatgpt", "gemini-pro"],
    "claude-haiku": ["claude-sonnet", "chatgpt", "gemini-flash"],
    "chatgpt": ["claude-sonnet", "gemini-pro"],
    "openai": ["claude-sonnet", "gemini-pro"],  # legacy alias
    "gemini-pro": ["gemini-flash", "claude-sonnet", "chatgpt"],
    "gemini-flash": ["gemini-pro", "claude-haiku", "chatgpt"],
    "moonshot": ["claude-sonnet", "chatgpt"],
    "perplexity": ["chatgpt", "claude-sonnet"],
}


class ChatRequest(BaseModel):
    provider: Optional[str] = None  # If None, use default
    messages: List[Dict[str, str]]
    system_prompt: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.7
    use_fallback: bool = True       # Enable automatic fallback on failure
    use_cache: bool = True          # Enable response caching
    caller: Optional[str] = None    # Caller identifier for usage tracking


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
    citations: Optional[List[str]] = None  # Perplexity citations

    class Config:
        extra = "ignore"  # Ignore extra fields from AI services


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
        "claude-sonnet": ClaudeService,
        "claude-haiku": ClaudeService,
        "chatgpt": ChatGPTService,
        "openai": ChatGPTService,  # legacy alias
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


async def _try_provider(provider_id: str, request: ChatRequest) -> Dict[str, Any]:
    """Try a single provider and return result. Raises on failure."""
    service = get_ai_service(provider_id)
    start_time = time.time()
    try:
        result = await service.chat(
            messages=request.messages,
            system_prompt=request.system_prompt,
            max_tokens=request.max_tokens,
            temperature=request.temperature
        )
        elapsed_ms = int((time.time() - start_time) * 1000)

        # Log successful usage
        log_usage(
            provider=provider_id,
            model=result.get("model", ""),
            input_tokens=result.get("usage", {}).get("input_tokens", 0),
            output_tokens=result.get("usage", {}).get("output_tokens", 0),
            elapsed_ms=elapsed_ms,
            success=True,
            caller=request.caller
        )

        return result
    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        log_usage(
            provider=provider_id,
            model="",
            elapsed_ms=elapsed_ms,
            success=False,
            error_message=str(e)[:500],
            caller=request.caller
        )
        raise


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a chat request to an AI provider.
    Supports caching, fallback chain, and usage logging.
    """
    config = load_config()
    provider_id = request.provider or config.default_provider

    logger.info(f"[CHAT] Provider: {provider_id}, Messages: {len(request.messages)}")

    # Check cache
    if request.use_cache:
        cached = response_cache.get(
            provider_id, request.messages, request.system_prompt,
            request.max_tokens, request.temperature
        )
        if cached:
            cached["_cached"] = True
            return ChatResponse(**cached)

    # Build provider attempt list (primary + fallbacks)
    providers_to_try = [provider_id]
    if request.use_fallback:
        fallbacks = FALLBACK_CHAINS.get(provider_id, [])
        providers_to_try.extend(fallbacks)

    last_error = None
    for pid in providers_to_try:
        try:
            result = await _try_provider(pid, request)

            # Cache the result
            if request.use_cache:
                response_cache.set(
                    provider_id, request.messages, request.system_prompt,
                    request.max_tokens, request.temperature, result
                )

            if pid != provider_id:
                logger.info(f"[FALLBACK] {provider_id} failed, succeeded with {pid}")

            return ChatResponse(**result)

        except HTTPException as e:
            last_error = e
            if not request.use_fallback or pid == providers_to_try[-1]:
                raise
            logger.warning(f"[FALLBACK] {pid} failed ({e.detail}), trying next...")
        except Exception as e:
            last_error = e
            if not request.use_fallback or pid == providers_to_try[-1]:
                logger.error(f"[CHAT ERROR] {type(e).__name__}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")
            logger.warning(f"[FALLBACK] {pid} failed ({e}), trying next...")

    raise HTTPException(status_code=500, detail=f"All providers failed. Last error: {last_error}")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Stream a chat response via Server-Sent Events (SSE).
    Each chunk is sent as: data: {"text": "..."}\n\n
    Final event: data: {"done": true}\n\n
    """
    config = load_config()
    provider_id = request.provider or config.default_provider

    try:
        service = get_ai_service(provider_id)
    except HTTPException as e:
        async def error_gen():
            yield f"data: {json.dumps({'error': e.detail})}\n\n"
        return StreamingResponse(error_gen(), media_type="text/event-stream")

    async def event_generator():
        try:
            async for chunk in service.stream(
                messages=request.messages,
                system_prompt=request.system_prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature
            ):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            logger.error(f"[STREAM ERROR] {type(e).__name__}: {str(e)}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


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
                "response": result["content"],
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
