from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any, Literal
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
from ..auth import require_admin
from ..model_options import plan_call, CallPlan, ModelFamilyMismatch, effective_options
from ..services import (
    ClaudeService,
    ChatGPTService,
    GeminiService,
    MoonshotService,
    PerplexityService
)

# INFO, not DEBUG: at DEBUG the openai SDK logs full request bodies (user prompts).
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["AI"])


FALLBACK_CHAINS = {
    "claude-sonnet": ["claude-haiku", "gemini-pro", "chatgpt"],
    "claude-haiku": ["claude-sonnet", "gemini-flash", "chatgpt"],
    "chatgpt": ["claude-haiku", "claude-sonnet", "gemini-pro"],
    "openai": ["claude-haiku", "claude-sonnet", "gemini-pro"],  # legacy alias
    "gemini-pro": ["claude-haiku", "gemini-flash", "claude-sonnet", "chatgpt"],
    "gemini-flash": ["claude-haiku", "gemini-pro", "chatgpt"],
    "moonshot": ["claude-haiku", "claude-sonnet", "chatgpt"],
    "perplexity": ["claude-haiku", "claude-sonnet", "chatgpt"],
}

# Time budgets — keep total below typical 30s client timeout so we can return
# a structured error before the client gives up.
PROVIDER_DEADLINE_S = float(os.getenv("AI_PROVIDER_DEADLINE_S", "15"))
TOTAL_BUDGET_S      = float(os.getenv("AI_TOTAL_BUDGET_S", "28"))

# Patterns that indicate a permanent provider failure — no retry, no point trying
# this provider again, immediately fall over to the next one.
_PERMANENT_PATTERNS = re.compile(
    r"insufficient_quota|invalid_api_key|authentication|not_found_error|"
    r"model_not_found|permission_denied|billing|account_deactivated|"
    r"NOT_FOUND|is not found",
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


class ChatOptions(BaseModel):
    # Portable reasoning / thinking level, translated per provider.
    # Omitted: the alias default (admin setting, else the recommended default).
    reasoning: Optional[Literal["off", "low", "medium", "high"]] = None
    # Per-attempt time limit for long generations (default AI_PROVIDER_DEADLINE_S).
    # The whole request (with fallbacks) may then take up to 2x this, max 600s.
    timeout_s: Optional[float] = Field(default=None, gt=0, le=300)


class ChatRequest(BaseModel):
    provider: Optional[str] = None  # If None, use default
    messages: List[Dict[str, Any]]
    system_prompt: Optional[str] = None
    max_tokens: int = 4096
    temperature: Optional[float] = 0.7  # null: don't send; dropped where the model rejects it
    use_fallback: bool = True       # Enable automatic fallback on failure
    use_cache: bool = True          # Enable response caching
    caller: Optional[str] = None    # Caller identifier for usage tracking
    model: Optional[str] = None     # Optional model of the provider's own vendor (primary provider only)
    options: Optional[ChatOptions] = None


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
    finish_reason: Optional[str] = None    # "stop" | "length" | provider-specific
    applied: Optional[Dict[str, Any]] = None  # model / options actually used


    class Config:
        extra = "ignore"  # Ignore extra fields from AI services


def get_ai_service(provider_id: str, model: Optional[str] = None):
    """Factory function to get the appropriate AI service (optionally for a
    specific model of the same vendor)."""
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
        model=model or provider.model,
        base_url=provider.base_url
    )


def _plan(provider_id: str, request: "ChatRequest", is_primary: bool) -> CallPlan:
    """Model and options for this provider. The request's model applies to the
    primary provider only; fallback providers use their own defaults plus the
    request's portable options."""
    provider = get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")
    reasoning = request.options.reasoning if request.options else None
    try:
        return plan_call(provider_id, provider, request.model if is_primary else None, reasoning)
    except ModelFamilyMismatch as e:
        raise HTTPException(status_code=400, detail=str(e))


def _cache_scope(provider_id: str, request: "ChatRequest") -> str:
    """Cache namespace: alias + the model/options that would answer."""
    try:
        plan = _plan(provider_id, request, True)
        return f"{provider_id}|{plan.model}|{plan.reasoning}|{plan.fallback_model}"
    except HTTPException:
        return provider_id


async def _try_provider(
    provider_id: str, request: ChatRequest, deadline_s: float, is_primary: bool = True
) -> Dict[str, Any]:
    """Try one provider within a hard deadline. If the alias has a same-family
    fallback model (e.g. gemini-3.8-flash -> gemini-3.5-flash-lite), the first
    model gets `fallback_after_s` and the fallback model the rest. Same vendor,
    so it also applies when use_fallback is false. Raises on failure."""
    plan = _plan(provider_id, request, is_primary)
    attempts = [plan.model] + ([plan.fallback_model] if plan.fallback_model else [])
    timeout_s = request.options.timeout_s if request.options else None
    chain_start = time.time()
    for i, model in enumerate(attempts):
        service = get_ai_service(provider_id, model)
        remaining = deadline_s - (time.time() - chain_start)
        # A caller-set timeout means a long answer is expected: switch to the
        # same-family fallback only on failure, not after fallback_after_s.
        quick_switch = i == 0 and plan.fallback_model and not timeout_s
        timeout = min(plan.fallback_after_s, remaining) if quick_switch else remaining
        start_time = time.time()
        try:
            result = await asyncio.wait_for(
                service.chat(
                    messages=request.messages,
                    system_prompt=request.system_prompt,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    reasoning=plan.reasoning,
                    timeout_s=timeout_s,
                ),
                timeout=timeout,
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
            result["applied"] = {
                "provider": provider_id,
                "model": model,
                "reasoning": result.pop("applied_reasoning", None),
                "temperature": result.pop("applied_temperature", None),
                "fallback_from": attempts[0] if i > 0 else None,
            }
            if timeout_s:
                result["applied"]["timeout_s"] = timeout_s
            return result
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            reason = "timeout" if isinstance(e, asyncio.TimeoutError) else str(e)
            log_usage(
                provider=provider_id,
                model=model,
                elapsed_ms=elapsed_ms,
                success=False,
                error_message=reason[:500],
                caller=request.caller
            )
            if i + 1 < len(attempts) and deadline_s - (time.time() - chain_start) > 1.0:
                logger.warning(f"[FALLBACK] {provider_id}: {model} failed ({reason[:80]}), "
                               f"trying {attempts[i + 1]}")
                continue
            raise


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a chat request to an AI provider.
    Supports caching, fallback chain, and usage logging.
    """
    # Config is in memory; an explicit provider skips even that lookup.
    provider_id = request.provider or load_config().default_provider

    logger.info(f"[CHAT] Provider: {provider_id}, Messages: {len(request.messages)}")

    # A model of another vendor is a client error: reject before any attempt
    # (and before it could count against the provider's circuit breaker).
    if request.model and get_provider(provider_id):
        _plan(provider_id, request, True)

    # Check cache (skip for vision requests - images are too large to cache)
    has_images = _has_image_content(request.messages)
    cache_scope = _cache_scope(provider_id, request)
    if request.use_cache and not has_images:
        cached = response_cache.get(
            cache_scope, request.messages, request.system_prompt,
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

    timeout_s = request.options.timeout_s if request.options else None
    provider_deadline = timeout_s or PROVIDER_DEADLINE_S
    total_budget = min(max(TOTAL_BUDGET_S, 2 * timeout_s), 600.0) if timeout_s else TOTAL_BUDGET_S

    attempts: List[Dict[str, Any]] = []
    chain_start = time.time()
    for pid in providers_to_try:
        remaining = total_budget - (time.time() - chain_start)
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

        attempt_deadline = min(provider_deadline, remaining)
        attempt_start = time.time()
        try:
            result = await _try_provider(pid, request, attempt_deadline, is_primary=(pid == provider_id))
            breaker.record_success(pid)

            if request.use_cache and not has_images and pid == provider_id:
                response_cache.set(
                    cache_scope, request.messages, request.system_prompt,
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
    provider_id = request.provider or load_config().default_provider

    try:
        plan = _plan(provider_id, request, True)
        service = get_ai_service(provider_id, plan.model)
    except HTTPException as e:
        detail = e.detail

        async def error_gen():
            yield f"data: {json.dumps({'error': detail})}\n\n"
        return StreamingResponse(error_gen(), media_type="text/event-stream")

    async def event_generator():
        try:
            async for chunk in service.stream(
                messages=request.messages,
                system_prompt=request.system_prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                reasoning=plan.reasoning,
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


@router.get("/health/providers", dependencies=[Depends(require_admin)])
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
                    reasoning=effective_options(pid, get_provider(pid)).get("reasoning"),
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


@router.post("/health/breaker/reset", dependencies=[Depends(require_admin)])
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
            "is_default": provider_id == config.default_provider,
            "default_options": effective_options(provider_id, provider),
        })

    return {"providers": providers, "default": config.default_provider}


@router.post("/batch-chat", dependencies=[Depends(require_admin)])
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
                temperature=request.temperature,
                reasoning=effective_options(provider_id, get_provider(provider_id)).get("reasoning"),
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
