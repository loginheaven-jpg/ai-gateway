import os
import time
import logging
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import load_config, get_provider
from ..usage import log_usage
from ..services import WhisperService, ClovaSttService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["STT"])

# Supported audio formats
SUPPORTED_FORMATS = {
    "webm", "mp4", "mp3", "wav", "ogg", "m4a", "flac", "mpeg", "mpga", "aac"
}

# Max file size (default 10MB)
MAX_FILE_SIZE = int(os.getenv("STT_MAX_FILE_SIZE_MB", "10")) * 1024 * 1024

# Default STT provider
DEFAULT_STT_PROVIDER = os.getenv("DEFAULT_STT_PROVIDER", "whisper")

# Fallback chains
STT_FALLBACK = {
    "whisper": ["clova"],
    "clova": ["whisper"],
}

# STT service factory
STT_SERVICE_MAP = {
    "whisper": WhisperService,
    "clova": ClovaSttService,
}


def _get_stt_service(provider_id: str):
    """Factory function to get STT service instance"""
    provider = get_provider(provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail=f"STT provider not found: {provider_id}")

    if not provider.enabled:
        raise HTTPException(status_code=400, detail=f"STT provider is disabled: {provider_id}")

    if not provider.api_key:
        raise HTTPException(status_code=400, detail=f"API key not configured for: {provider_id}")

    service_class = STT_SERVICE_MAP.get(provider_id)
    if not service_class:
        raise HTTPException(status_code=400, detail=f"Unknown STT provider: {provider_id}")

    return service_class(
        api_key=provider.api_key,
        model=provider.model,
        base_url=provider.base_url
    )


def _get_file_extension(filename: str) -> str:
    """Extract file extension from filename"""
    if "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return ""


@router.post("/stt")
async def speech_to_text(
    file: UploadFile = File(...),
    language: str = Form("ko"),
    provider: Optional[str] = Form(None),
    caller: Optional[str] = Form(None),
):
    """
    Convert speech audio to text via STT provider (Whisper or CLOVA Speech).
    Supports fallback: if primary provider fails, tries the next one.
    """
    # Validate file format
    ext = _get_file_extension(file.filename or "")
    if ext not in SUPPORTED_FORMATS:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported audio format: .{ext}", "code": "UNSUPPORTED_FORMAT"}
        )

    # Read file data
    audio_data = await file.read()

    # Validate file size
    if len(audio_data) > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE // (1024 * 1024)
        return JSONResponse(
            status_code=400,
            content={"error": f"File too large: {len(audio_data)} bytes (max {max_mb}MB)", "code": "FILE_TOO_LARGE"}
        )

    if len(audio_data) == 0:
        return JSONResponse(
            status_code=400,
            content={"error": "Empty file uploaded", "code": "INVALID_FILE"}
        )

    # Determine provider
    provider_id = provider or DEFAULT_STT_PROVIDER
    logger.info(f"[STT] Provider: {provider_id}, Language: {language}, File: {file.filename}, Size: {len(audio_data)}")

    # Build attempt list (primary + fallbacks)
    providers_to_try = [provider_id]
    fallbacks = STT_FALLBACK.get(provider_id, [])
    providers_to_try.extend(fallbacks)

    last_error = None
    for pid in providers_to_try:
        start_time = time.time()
        try:
            service = _get_stt_service(pid)
            result = await service.recognize(
                audio_data=audio_data,
                language=language,
                filename=file.filename or f"audio.{ext}"
            )
            elapsed_ms = int((time.time() - start_time) * 1000)

            # Log usage
            log_usage(
                provider=pid,
                model=result.get("model", ""),
                input_tokens=len(audio_data),
                output_tokens=len(result.get("text", "")),
                elapsed_ms=elapsed_ms,
                success=True,
                caller=caller
            )

            if pid != provider_id:
                logger.info(f"[STT FALLBACK] {provider_id} failed, succeeded with {pid}")

            return {
                "text": result["text"],
                "language": result.get("language", language),
                "duration_sec": result.get("duration_sec", 0.0),
                "provider": result.get("provider", pid),
                "model": result.get("model", ""),
                "elapsed_ms": elapsed_ms,
            }

        except HTTPException:
            # Provider not available (no key, disabled, etc.) - skip to fallback
            last_error = str(getattr(last_error, 'detail', str(last_error))) if last_error else "Provider unavailable"
            elapsed_ms = int((time.time() - start_time) * 1000)
            log_usage(
                provider=pid,
                model="",
                elapsed_ms=elapsed_ms,
                success=False,
                error_message=f"Provider unavailable: {pid}",
                caller=caller
            )
            if pid == providers_to_try[-1]:
                break
            logger.warning(f"[STT FALLBACK] {pid} unavailable, trying next...")
            continue

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            last_error = str(e)
            log_usage(
                provider=pid,
                model="",
                elapsed_ms=elapsed_ms,
                success=False,
                error_message=str(e)[:500],
                caller=caller
            )
            if pid == providers_to_try[-1]:
                break
            logger.warning(f"[STT FALLBACK] {pid} failed ({e}), trying next...")
            continue

    return JSONResponse(
        status_code=500,
        content={"error": f"All STT providers failed. Last error: {last_error}", "code": "PROVIDER_ERROR"}
    )


@router.get("/stt/providers")
async def list_stt_providers():
    """List available STT providers"""
    config = load_config()

    providers = []
    for provider_id, provider in config.providers.items():
        if provider.service_type != "stt":
            continue
        providers.append({
            "id": provider_id,
            "name": provider.name,
            "model": provider.model,
            "enabled": provider.enabled,
            "has_api_key": bool(provider.api_key),
            "is_default": provider_id == DEFAULT_STT_PROVIDER,
        })

    return {"providers": providers, "default": DEFAULT_STT_PROVIDER}
