"""Admin authentication for management endpoints.

Management routes (settings, provider probes, batch tests) require the
`X-Admin-Token` header to match the `GATEWAY_ADMIN_TOKEN` env var.
App-facing routes (/api/ai/chat, /stt, /image, ...) are intentionally NOT
protected here — church apps call them without credentials.

Fail-closed: if GATEWAY_ADMIN_TOKEN is unset, admin routes return 503
instead of silently allowing access.
"""
import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("GATEWAY_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Admin token not configured on server")
    if not x_admin_token or not hmac.compare_digest(
        x_admin_token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")
