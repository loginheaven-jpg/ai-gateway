"""Per-provider circuit breaker.

After repeated permanent failures (auth/quota/model-not-found), a provider is
"opened" — skipped from the fallback chain for a cooldown window so the chain
doesn't burn time re-discovering it's still broken on every request. Any
success resets the breaker.

In-memory only (per-worker). Sufficient for Railway single-worker uvicorn.
"""
import os
import time
import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

PERMANENT_FAILS_TO_OPEN = int(os.getenv("AI_BREAKER_FAILS", "2"))
OPEN_COOLDOWN_S         = float(os.getenv("AI_BREAKER_COOLDOWN_S", "60"))


class _ProviderState:
    __slots__ = ("permanent_fails", "opened_until", "last_reason")

    def __init__(self):
        self.permanent_fails: int = 0
        self.opened_until: float = 0.0
        self.last_reason: Optional[str] = None


class CircuitBreaker:
    def __init__(self):
        self._states: Dict[str, _ProviderState] = {}
        self._lock = threading.Lock()

    def _get(self, provider: str) -> _ProviderState:
        st = self._states.get(provider)
        if st is None:
            st = _ProviderState()
            self._states[provider] = st
        return st

    def is_open(self, provider: str) -> bool:
        with self._lock:
            st = self._get(provider)
            if st.opened_until and time.time() >= st.opened_until:
                # cooldown passed — half-open: allow next attempt
                st.opened_until = 0.0
                st.permanent_fails = 0
                logger.info(f"[BREAKER] {provider} cooldown elapsed, half-open")
                return False
            return st.opened_until > 0.0

    def opened_until(self, provider: str) -> float:
        with self._lock:
            return self._get(provider).opened_until

    def record_success(self, provider: str) -> None:
        with self._lock:
            st = self._get(provider)
            if st.opened_until or st.permanent_fails:
                logger.info(f"[BREAKER] {provider} success — closing")
            st.permanent_fails = 0
            st.opened_until = 0.0
            st.last_reason = None

    def record_failure(self, provider: str, kind: str, reason: str) -> None:
        """kind ∈ {'permanent','transient','unknown','timeout'}.
        Only permanent failures open the breaker — transient/timeout are
        treated as retryable, no penalty."""
        if kind != "permanent":
            return
        with self._lock:
            st = self._get(provider)
            st.permanent_fails += 1
            st.last_reason = reason[:200]
            if st.permanent_fails >= PERMANENT_FAILS_TO_OPEN:
                st.opened_until = time.time() + OPEN_COOLDOWN_S
                logger.warning(
                    f"[BREAKER] {provider} OPEN for {OPEN_COOLDOWN_S:.0f}s "
                    f"after {st.permanent_fails} permanent fails: {st.last_reason}"
                )

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        now = time.time()
        with self._lock:
            out = {}
            for pid, st in self._states.items():
                open_for = max(0.0, st.opened_until - now) if st.opened_until else 0.0
                out[pid] = {
                    "open": open_for > 0,
                    "open_seconds_remaining": round(open_for, 1),
                    "permanent_fails": st.permanent_fails,
                    "last_reason": st.last_reason,
                }
            return out

    def reset(self, provider: Optional[str] = None) -> None:
        with self._lock:
            if provider:
                self._states.pop(provider, None)
                logger.info(f"[BREAKER] {provider} reset by admin")
            else:
                self._states.clear()
                logger.info("[BREAKER] all reset by admin")


breaker = CircuitBreaker()
