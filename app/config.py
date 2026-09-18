import os
import json
import socket
import threading
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse
from pydantic import BaseModel

# Database configuration
# Priority: DATABASE_URL (PostgreSQL) > SQLite file
DATABASE_URL = os.getenv("DATABASE_URL")
DB_CONNECT_TIMEOUT_S = int(os.getenv("DB_CONNECT_TIMEOUT_S", "5"))

# For SQLite fallback
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "config.db"
CONFIG_FILE = DATA_DIR / "ai-config.json"

# Determine database type
USE_POSTGRES = DATABASE_URL is not None
print(f"[CONFIG] Database: {'PostgreSQL' if USE_POSTGRES else 'SQLite'}")
if not USE_POSTGRES:
    print(f"[CONFIG] SQLite path: {DB_FILE}")


class ProviderConfig(BaseModel):
    name: str
    api_key: str
    model: str
    base_url: str
    enabled: bool = True
    service_type: str = "chat"  # "chat" or "stt"


class AIConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    default_provider: str = "claude-sonnet"


class ConfigUnavailable(Exception):
    """Raised on a config write while the in-memory config did not come from the DB
    (startup DB failure). Writing then would overwrite the DB with env defaults."""


def _get_pg_connection():
    """Get PostgreSQL connection"""
    import psycopg2
    try:
        return psycopg2.connect(DATABASE_URL, connect_timeout=DB_CONNECT_TIMEOUT_S)
    except Exception as e:
        print(f"[CONFIG] PostgreSQL connection failed: {type(e).__name__}: {e}")
        raise


def _get_sqlite_connection():
    """Get SQLite connection"""
    import sqlite3
    return sqlite3.connect(str(DB_FILE))


def _connect():
    return _get_pg_connection() if USE_POSTGRES else _get_sqlite_connection()


def init_db():
    """Create / migrate the config schema. Startup only — never on a request path.

    Idempotent without relying on a failing statement: a failed ALTER inside the
    transaction used to abort it, so on a fresh DB the CREATE TABLEs were rolled
    back too and the tables were never created.
    """
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                api_key TEXT NOT NULL,
                model TEXT NOT NULL,
                base_url TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                service_type TEXT DEFAULT 'chat'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        # Add service_type column if missing (older schemas)
        if USE_POSTGRES:
            cursor.execute("ALTER TABLE providers ADD COLUMN IF NOT EXISTS service_type TEXT DEFAULT 'chat'")
        else:
            cursor.execute("PRAGMA table_info(providers)")
            if "service_type" not in {row[1] for row in cursor.fetchall()}:
                cursor.execute("ALTER TABLE providers ADD COLUMN service_type TEXT DEFAULT 'chat'")
        conn.commit()
    finally:
        conn.close()


def log_db_timing() -> None:
    """Log DNS / connect / SELECT 1 timings once at startup. Never logs host or URL,
    only what kind of host it is (Railway private network vs public proxy)."""
    if not USE_POSTGRES:
        return
    try:
        host = urlparse(DATABASE_URL).hostname or ""
        port = urlparse(DATABASE_URL).port or 5432
        if host.endswith(".railway.internal"):
            kind = "railway_private"
        elif host.endswith(".rlwy.net"):
            kind = "railway_public_proxy"
        elif host in ("localhost", "127.0.0.1", "::1"):
            kind = "local"
        else:
            kind = "other"

        t0 = time.perf_counter()
        socket.getaddrinfo(host, port)
        dns_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        conn = _get_pg_connection()
        connect_ms = (time.perf_counter() - t0) * 1000
        try:
            t0 = time.perf_counter()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            select_ms = (time.perf_counter() - t0) * 1000
        finally:
            conn.close()
        print(f"[DB TIMING] host_kind={kind} dns_ms={dns_ms:.0f} "
              f"connect_ms={connect_ms:.0f} select1_ms={select_ms:.0f}")
    except Exception as e:
        print(f"[DB TIMING] failed: {type(e).__name__}")


def _get_default_providers():
    """Get default provider configurations from environment or defaults"""
    return {
        "claude-sonnet": ProviderConfig(
            name="Claude Sonnet (Anthropic)",
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-5"),
            base_url="https://api.anthropic.com/v1",
            enabled=True
        ),
        "claude-haiku": ProviderConfig(
            name="Claude Haiku (Anthropic)",
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001"),
            base_url="https://api.anthropic.com/v1",
            enabled=True
        ),
        "chatgpt": ProviderConfig(
            name="ChatGPT (OpenAI)",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "gpt-5.1"),
            base_url="https://api.openai.com/v1",
            enabled=True
        ),
        "gemini-pro": ProviderConfig(
            name="Gemini (Pro)",
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            model=os.getenv("GEMINI_PRO_MODEL", "gemini-pro-latest"),
            base_url="https://generativelanguage.googleapis.com/v1beta",
            enabled=True
        ),
        "gemini-flash": ProviderConfig(
            name="Gemini (Flash)",
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            model=os.getenv("GEMINI_FLASH_MODEL", "gemini-flash-latest"),
            base_url="https://generativelanguage.googleapis.com/v1beta",
            enabled=True
        ),
        "moonshot": ProviderConfig(
            name="Moonshot (Kimi)",
            api_key=os.getenv("MOONSHOT_API_KEY", ""),
            model=os.getenv("MOONSHOT_MODEL", "kimi-k2-0905-preview"),
            base_url="https://api.moonshot.ai/v1",
            enabled=True
        ),
        "perplexity": ProviderConfig(
            name="Perplexity",
            api_key=os.getenv("PERPLEXITY_API_KEY", ""),
            model=os.getenv("PERPLEXITY_MODEL", "sonar-pro"),
            base_url="https://api.perplexity.ai",
            enabled=True
        ),
        # STT Providers
        "whisper": ProviderConfig(
            name="Whisper (OpenAI)",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model="whisper-1",
            base_url="https://api.openai.com/v1",
            enabled=True,
            service_type="stt"
        ),
        "clova-csr": ProviderConfig(
            name="CLOVA CSR (Naver, Short)",
            api_key=f"{os.getenv('CLOVA_CSR_CLIENT_ID', '')}:{os.getenv('CLOVA_CSR_CLIENT_SECRET', '')}",
            model="clova-csr",
            base_url="https://naveropenapi.apigw.ntruss.com",
            enabled=True,
            service_type="stt"
        ),
        "clova-speech": ProviderConfig(
            name="CLOVA Speech (Naver, Long)",
            api_key=os.getenv("CLOVA_SPEECH_SECRET_KEY", ""),
            model="clova-speech-long",
            base_url=os.getenv("CLOVA_SPEECH_INVOKE_URL", ""),
            enabled=True,
            service_type="stt"
        ),
        # Image Generation Providers
        "dall-e": ProviderConfig(
            name="DALL-E 3 (OpenAI)",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model="dall-e-3",
            base_url="https://api.openai.com/v1",
            enabled=True,
            service_type="image"
        ),
        "imagen": ProviderConfig(
            name="Imagen 3 (Google)",
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            model="imagen-3.0-generate-002",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            enabled=True,
            service_type="image"
        )
    }


def _load_from_db() -> Optional[AIConfig]:
    """Read config from the DB. Returns None only when the providers table is
    empty; raises on DB errors (callers must not mistake an error for 'empty')."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM providers")
        if cursor.fetchone()[0] == 0:
            return None

        cursor.execute("SELECT id, name, api_key, model, base_url, enabled, service_type FROM providers")
        providers = {}
        for row in cursor.fetchall():
            providers[row[0]] = ProviderConfig(
                name=row[1],
                api_key=row[2],
                model=row[3],
                base_url=row[4],
                enabled=bool(row[5]),
                service_type=row[6] or "chat"
            )

        cursor.execute("SELECT value FROM settings WHERE key = 'default_provider'")
        result = cursor.fetchone()
        default_provider = result[0] if result else "claude"

        return AIConfig(providers=providers, default_provider=default_provider)
    finally:
        conn.close()


def _load_settings_from_db() -> Dict[str, str]:
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        return {row[0]: row[1] for row in cursor.fetchall()}
    finally:
        conn.close()


def _save_to_db(config: AIConfig):
    """Save configuration to database (PostgreSQL or SQLite) in one transaction."""
    conn = _connect()
    try:
        cursor = conn.cursor()

        # Clear and insert providers
        cursor.execute("DELETE FROM providers")
        for provider_id, provider in config.providers.items():
            if USE_POSTGRES:
                cursor.execute('''
                    INSERT INTO providers (id, name, api_key, model, base_url, enabled, service_type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (provider_id, provider.name, provider.api_key, provider.model,
                      provider.base_url, 1 if provider.enabled else 0, provider.service_type))
            else:
                cursor.execute('''
                    INSERT INTO providers (id, name, api_key, model, base_url, enabled, service_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (provider_id, provider.name, provider.api_key, provider.model,
                      provider.base_url, 1 if provider.enabled else 0, provider.service_type))

        _upsert_setting(cursor, "default_provider", config.default_provider)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _upsert_setting(cursor, key: str, value: str):
    if USE_POSTGRES:
        cursor.execute('''
            INSERT INTO settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        ''', (key, value))
    else:
        cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
        ''', (key, value))


def _load_from_json() -> Optional[AIConfig]:
    """Load AI configuration from JSON file (legacy support)"""
    if not CONFIG_FILE.exists():
        return None

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    providers = {}
    for key, value in data.get("providers", {}).items():
        providers[key] = ProviderConfig(**value)

    return AIConfig(
        providers=providers,
        default_provider=data.get("default_provider", "claude")
    )


# ── In-memory config ──────────────────────────────────────────
# Config is read from the DB once at startup and served from memory; request
# paths never touch the DB. Write paths persist first, then swap in a new
# snapshot. The snapshot is shared: treat what load_config() returns as
# read-only and change config only through the write functions below.
#
# Assumes a single worker / single replica (as do the response cache and the
# circuit breaker). If the DB is edited by hand, restart the service to reload.

_state_lock = threading.RLock()
_snapshot: Optional[AIConfig] = None
_settings: Dict[str, str] = {}
_config_source = "unloaded"   # "db" | "env_fallback" | "unloaded"
_retry_thread: Optional[threading.Thread] = None


def _migrate(config: AIConfig) -> bool:
    """Startup-time fixups. Returns True if the DB copy needs saving."""
    changed = False

    # Auto-merge new default providers not yet in DB
    added = []
    for pid, pconfig in _get_default_providers().items():
        if pid not in config.providers:
            config.providers[pid] = pconfig
            added.append(pid)
    if added:
        changed = True
        print(f"[CONFIG] Auto-added new providers: {added}")

    # Auto-migrate legacy aliases (openai → chatgpt) — a copy, not a shared object
    if "openai" in config.providers and "chatgpt" not in config.providers:
        config.providers["chatgpt"] = config.providers["openai"].model_copy(
            update={"name": "ChatGPT (OpenAI)"}
        )

    # Auto-heal known-retired model IDs that caused the 2026-06-13 outage.
    # Only rewrites exact matches; custom user-set models are untouched.
    RETIRED_MODELS = {
        "gemini-pro":   ("gemini-3-pro-preview", "gemini-pro-latest"),
        "gemini-flash": ("gemini-2.5-flash",     "gemini-flash-latest"),
        "claude-haiku": ("claude-haiku-4-6",     "claude-haiku-4-5-20251001"),
    }
    healed = []
    for pid, (bad, good) in RETIRED_MODELS.items():
        p = config.providers.get(pid)
        if p and p.model == bad:
            config.providers[pid] = p.model_copy(update={"model": good})
            healed.append(f"{pid}:{bad}->{good}")
    if healed:
        changed = True
        print(f"[CONFIG] Auto-healed retired models: {healed}")

    return changed


def _bootstrap_from_db() -> AIConfig:
    """Load (and if needed seed / migrate) config from the DB. Raises on DB errors."""
    init_db()

    config = _load_from_db()
    if config:
        if _migrate(config):
            _save_to_db(config)
    else:
        # Genuinely empty DB: seed from legacy JSON, else from environment.
        config = _load_from_json() or AIConfig(
            providers=_get_default_providers(),
            default_provider=os.getenv("DEFAULT_AI_PROVIDER", "claude-sonnet")
        )
        _save_to_db(config)

    settings = _load_settings_from_db()
    _swap(config, settings, "db")
    return config


def _swap(config: AIConfig, settings: Optional[Dict[str, str]], source: str):
    global _snapshot, _settings, _config_source
    with _state_lock:
        _snapshot = config.model_copy(deep=True)
        if settings is not None:
            _settings = dict(settings)
        _config_source = source


def _retry_bootstrap_loop():
    delay = 5.0
    while True:
        time.sleep(delay)
        try:
            _bootstrap_from_db()
            print("[CONFIG] DB reachable again - config reloaded from DB")
            return
        except Exception as e:
            print(f"[CONFIG] DB still unavailable ({type(e).__name__}), retry in {min(delay * 2, 300):.0f}s")
            delay = min(delay * 2, 300.0)


def bootstrap_config() -> AIConfig:
    """Startup entry point. Never raises: on DB failure, serve env defaults from
    memory (without saving them) and keep retrying in the background."""
    global _retry_thread
    try:
        return _bootstrap_from_db()
    except Exception as e:
        print(f"[CONFIG] DB unavailable at startup ({type(e).__name__}: {e}) - "
              f"serving env defaults from memory, not saving; retrying in background")
        config = AIConfig(
            providers=_get_default_providers(),
            default_provider=os.getenv("DEFAULT_AI_PROVIDER", "claude-sonnet")
        )
        # Legacy alias still used by apps (prayer-house); normally a DB row
        config.providers["openai"] = config.providers["chatgpt"].model_copy(update={"name": "GPT (OpenAI)"})
        _swap(config, {}, "env_fallback")
        if _retry_thread is None or not _retry_thread.is_alive():
            _retry_thread = threading.Thread(target=_retry_bootstrap_loop, name="config-retry", daemon=True)
            _retry_thread.start()
        return _snapshot


def config_source() -> str:
    return _config_source


def load_config() -> AIConfig:
    """Current config snapshot from memory (no DB access). Read-only."""
    if _snapshot is None:
        bootstrap_config()
    return _snapshot


def get_provider(provider_id: str) -> Optional[ProviderConfig]:
    """Get a specific provider configuration"""
    return load_config().providers.get(provider_id)


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Value from the settings table (in memory), e.g. 'default_stt_provider'."""
    load_config()
    return _settings.get(key, default)


def _require_db_source():
    if _config_source != "db":
        raise ConfigUnavailable(
            f"Config is not loaded from the DB (source={_config_source}); refusing to write"
        )


def save_config(config: AIConfig) -> None:
    """Persist a full configuration, then make it the live snapshot.
    Applies the same fixups as startup (missing default providers, retired
    model IDs), which the old code ran on every load."""
    with _state_lock:
        _require_db_source()
        _migrate(config)
        _save_to_db(config)
        _swap(config, None, "db")


def set_setting(key: str, value: str) -> None:
    with _state_lock:
        _require_db_source()
        conn = _connect()
        try:
            _upsert_setting(conn.cursor(), key, value)
            conn.commit()
        finally:
            conn.close()
        new_settings = dict(_settings)
        new_settings[key] = value
        _swap(_snapshot, new_settings, "db")


def set_default_provider(provider_id: str) -> None:
    with _state_lock:
        config = load_config().model_copy(deep=True)
        if provider_id not in config.providers:
            raise ValueError(f"Provider not found: {provider_id}")
        config.default_provider = provider_id
        save_config(config)


def update_provider(provider_id: str, updates: dict) -> ProviderConfig:
    """Update a specific provider configuration"""
    with _state_lock:
        config = load_config().model_copy(deep=True)

        if provider_id not in config.providers:
            raise ValueError(f"Provider not found: {provider_id}")

        updated_data = config.providers[provider_id].model_dump()
        updated_data.update(updates)
        config.providers[provider_id] = ProviderConfig(**updated_data)

        save_config(config)
        return config.providers[provider_id]


def add_provider(provider_id: str, provider_config: ProviderConfig) -> ProviderConfig:
    """Add a new provider configuration"""
    with _state_lock:
        config = load_config().model_copy(deep=True)

        if provider_id in config.providers:
            raise ValueError(f"Provider already exists: {provider_id}")

        config.providers[provider_id] = provider_config
        save_config(config)
        return provider_config


def reset_providers():
    """Reset providers to default configuration"""
    default_config = AIConfig(
        providers=_get_default_providers(),
        default_provider="claude-sonnet"
    )
    save_config(default_config)
    return default_config
