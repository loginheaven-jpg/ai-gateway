"""In-memory response cache with TTL for AI Gateway."""
import hashlib
import json
import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class ResponseCache:
    """Simple in-memory cache with TTL (Time-To-Live)."""

    def __init__(self, default_ttl: int = 3600, max_size: int = 1000):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.default_ttl = default_ttl  # seconds
        self.max_size = max_size
        self.hits = 0
        self.misses = 0

    def _make_key(self, provider: str, messages: list, system_prompt: Optional[str],
                  max_tokens: int, temperature: float) -> str:
        """Create a deterministic cache key from request parameters."""
        key_data = {
            "provider": provider,
            "messages": messages,
            "system_prompt": system_prompt or "",
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        key_str = json.dumps(key_data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(key_str.encode()).hexdigest()

    def get(self, provider: str, messages: list, system_prompt: Optional[str],
            max_tokens: int, temperature: float) -> Optional[Dict[str, Any]]:
        """Get cached response if exists and not expired."""
        key = self._make_key(provider, messages, system_prompt, max_tokens, temperature)
        entry = self._cache.get(key)

        if entry is None:
            self.misses += 1
            return None

        if time.time() > entry["expires_at"]:
            del self._cache[key]
            self.misses += 1
            return None

        self.hits += 1
        logger.info(f"[CACHE HIT] provider={provider}, key={key[:12]}...")
        return entry["data"]

    def set(self, provider: str, messages: list, system_prompt: Optional[str],
            max_tokens: int, temperature: float, data: Dict[str, Any],
            ttl: Optional[int] = None):
        """Store a response in the cache."""
        # Evict oldest entries if at capacity
        if len(self._cache) >= self.max_size:
            oldest_key = min(self._cache, key=lambda k: self._cache[k]["created_at"])
            del self._cache[oldest_key]

        key = self._make_key(provider, messages, system_prompt, max_tokens, temperature)
        self._cache[key] = {
            "data": data,
            "created_at": time.time(),
            "expires_at": time.time() + (ttl or self.default_ttl)
        }
        logger.info(f"[CACHE SET] provider={provider}, key={key[:12]}...")

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()
        self.hits = 0
        self.misses = 0

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        now = time.time()
        active = sum(1 for v in self._cache.values() if now < v["expires_at"])
        return {
            "total_entries": len(self._cache),
            "active_entries": active,
            "expired_entries": len(self._cache) - active,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / max(self.hits + self.misses, 1) * 100, 1),
            "max_size": self.max_size,
            "default_ttl": self.default_ttl
        }


# Global cache instance
response_cache = ResponseCache(default_ttl=3600, max_size=1000)
