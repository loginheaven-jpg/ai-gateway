from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import asyncio
import json
import os
import re
import time
import traceback

from ..config import load_config, get_provider
from ..usage import log_usage
from ..cache import response_cache
from ..circuit_breaker import breaker
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
    "claude-sonnet": ["claude-haiku", "gemini-pro", "chatgpt"],
    "claude-haiku": ["gemini-flash", "claude-sonnet", "chatgpt"],
    "chatgpt": ["claude-sonnet", "gemini-pro"],
    "openai": ["claude-sonnet", "gemini-pro"],  # legacy alias
    "gemini-pro": ["gemini-flash", "claude-sonnet", "chatgpt"],
    "gemini-flash": ["claude-haiku", "gemini-pro", "chatgpt"],
    "moonshot": ["claude-sonnet", "chatgpt"],
    "perplexity": ["chatgpt", "claude-sonnet"],
}

# Time budgets — keep total below typical 30s client timeout so we can return
# a structured error before the client gives up.
PROVIDER_DEADLINE_S = float(os.getenv("AI_PROVIDER_DEADLINE_S", "15"))
TOTAL_BUDGET_S      = float(os.getenv("AI_TOTAL_BUDGET_S", "28"))

# Patterns that indicate a permanent provider failure — no retry, no point trying
# this provider again, immediately fall over to the next one.
_PERMANENT_PATTERNS = re.compile(
    r"insufficient_quota|invalid_api_key|authentication|not_found_error|"
    r"model_not_found|permission_denied|billing|account_deactivated",
    re.IGNORECASE,
)
_PERMANENT_STATUSES = {400, 401, 403, 404}


def _classify_error(exc: BaseException) -> str:
    """Return 'permanent' | 'transient' | 'unknown'."""
    msg = str(exc)
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
    if isinstance(status, int) and status in _PERMANENT_STATUSES:
        return "permanent"
    if _PERMANENT_PATTERNS.search(msg):
        return "permanent"
    if isinstance(exc, (asyncio.TimeoutError,)):
        return "transient"
    if isinstance(status, int) and (status == 429 or 500 <= status < 600):
        return "transient"
    return "unknown"


def _has_image_content(messages: List[Dict[str, Any]]) -> bool:
    """Check if any message contains image content blocks."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    return True
    return False


class ChatRequest(BaseModel):
    provider: Optional[str] = None  # If None, use default
    messages: List[Dict[str, Any]]
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


async def _try_provider(
    provider_id: str, request: ChatRequest, deadline_s: float
) -> Dict[str, Any]:
    """Try a single provider with a hard deadline. Raises on failure or timeout."""
    service = get_ai_service(provider_id)
    start_time = time.time()
    try:
        result = await asyncio.wait_for(
            service.chat(
                messages=request.messages,
                system_prompt=request.system_prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            ),
            timeout=deadline_s,
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

    # Check cache (skip for vision requests - images are too large to cache)
    has_images = _has_image_content(request.messages)
    if request.use_cache and not has_images:
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

    attempts: List[Dict[str, Any]] = []
    chain_start = time.time()
    for pid in providers_to_try:
        remaining = TOTAL_BUDGET_S - (time.time() - chain_start)
        if remaining <= 1.0:
            attempts.append({
                "provider": pid, "status": "skipped",
                "reason": "total_budget_exhausted", "elapsed_ms": 0,
            })
            logger.warning(f"[FALLBACK] budget exhausted before trying {pid}")
            break

        # Skip providers the breaker has marked as known-broken — avoids
        # burning the time budget on a provider we already know will fail.
        # Primary provider is exempt (user explicitly asked for it).
        if pid != provider_id and breaker.is_open(pid):
            attempts.append({
                "provider": pid, "status": "skipped",
                "reason": "circuit_breaker_open", "elapsed_ms": 0,
            })
            logger.info(f"[BREAKER] skipping {pid} (open)")
            continue

        attempt_deadline = min(PROVIDER_DEADLINE_S, remaining)
        attempt_start = time.time()
        try:
            result = await _try_provider(pid, request, attempt_deadline)
            breaker.record_success(pid)

            if request.use_cache and not has_images:
                response_cache.set(
                    provider_id, request.messages, request.system_prompt,
                    request.max_tokens, request.temperature, result
                )

            if pid != provider_id:
                logger.info(f"[FALLBACK] {provider_id} failed, succeeded with {pid}")

            return ChatResponse(**result)

        except HTTPException as e:
            kind = _classify_error(e)
            reason = str(e.detail)[:200]
            breaker.record_failure(pid, kind, reason)
            attempts.append({
                "provider": pid, "status": kind, "reason": reason,
                "elapsed_ms": int((time.time() - attempt_start) * 1000),
            })
            if not request.use_fallback:
                raise
            logger.warning(f"[FALLBACK] {pid} {kind} ({e.detail}), trying next...")
        except asyncio.TimeoutError:
            breaker.record_failure(pid, "transient", "timeout")
            attempts.append({
                "provider": pid, "status": "timeout",
                "reason": f"exceeded {attempt_deadline:.1f}s deadline",
                "elapsed_ms": int((time.time() - attempt_start) * 1000),
            })
            if not request.use_fallback:
                raise HTTPException(
                    status_code=504,
                    detail=f"{pid} timed out after {attempt_deadline:.1f}s",
                )
            logger.warning(f"[FALLBACK] {pid} timeout, trying next...")
        except Exception as e:
            kind = _classify_error(e)
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            breaker.record_failure(pid, kind, reason)
            attempts.append({
                "provider": pid, "status": kind, "reason": reason,
                "elapsed_ms": int((time.time() - attempt_start) * 1000),
            })
            if not request.use_fallback:
                logger.error(f"[CHAT ERROR] {type(e).__name__}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")
            logger.warning(f"[FALLBACK] {pid} {kind} ({e}), trying next...")

    # All providers failed. Return structured 503 so client can distinguish
    # "down" from "slow" — see AI_GATEWAY_ERROR_REPORT.md §4-B-6.
    last = attempts[-1] if attempts else {"reason": "no providers configured"}
    raise HTTPException(
        status_code=503,
        detail={
            "error": "all_providers_failed",
            "tried": attempts,
            "last_reason": last.get("reason", "unknown"),
            "total_elapsed_ms": int((time.time() - chain_start) * 1000),
        },
    )


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


@router.get("/health/providers")
async def health_providers():
    """Probe each enabled chat provider with a tiny request. Used by monitoring
    and as a pre-flight check — surfaces stale model IDs / quota issues before
    a real user request hits the fallback chain."""
    config = load_config()
    chat_providers = [
        pid for pid, p in config.providers.items()
        if p.service_type == "chat" and p.enabled and p.api_key
    ]

    async def probe(pid: str):
        start = time.time()
        try:
            service = get_ai_service(pid)
            result = await asyncio.wait_for(
                service.chat(
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=8,
                    temperature=0.0,
                ),
                timeout=10.0,
            )
            breaker.record_success(pid)
            return {
                "provider": pid,
                "ok": True,
                "model": result.get("model", ""),
                "elapsed_ms": int((time.time() - start) * 1000),
            }
        except Exception as e:
            kind = _classify_error(e)
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            breaker.record_failure(pid, kind, reason)
            return {
                "provider": pid,
                "ok": False,
                "error_kind": kind,
                "error": reason,
                "elapsed_ms": int((time.time() - start) * 1000),
            }

    results = await asyncio.gather(*(probe(p) for p in chat_providers))
    healthy = sum(1 for r in results if r["ok"])
    return {
        "healthy": healthy,
        "total": len(results),
        "providers": results,
        "breaker": breaker.snapshot(),
    }


@router.get("/health/breaker")
async def breaker_state():
    """Inspect the circuit breaker — which providers are currently skipped."""
    return {"breaker": breaker.snapshot()}


@router.post("/health/breaker/reset")
async def breaker_reset(provider: Optional[str] = None):
    """Manually close the breaker for one provider (or all if omitted).
    Useful after fixing a credential / model ID without waiting for cooldown."""
    breaker.reset(provider)
    return {"reset": provider or "all", "breaker": breaker.snapshot()}


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
