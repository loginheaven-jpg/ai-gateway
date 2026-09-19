import os
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from pydantic import BaseModel, Field

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
    # Per-alias default call options, overriding the recommended defaults in
    # model_options.ALIAS_DEFAULTS: {"reasoning": "off|low|medium|high",
    # "fallback_model": "<same-family model>", "fallback_after_s": 6}
    options: Dict[str, Any] = Field(default_factory=dict)


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
        # Add columns missing from older schemas (additive only)
        if USE_POSTGRES:
            cursor.execute("ALTER TABLE providers ADD COLUMN IF NOT EXISTS service_type TEXT DEFAULT 'chat'")
            cursor.execute("ALTER TABLE providers ADD COLUMN IF NOT EXISTS options TEXT")
        else:
            cursor.execute("PRAGMA table_info(providers)")
            cols = {row[1] for row in cursor.fetchall()}
            if "service_type" not in cols:
                cursor.execute("ALTER TABLE providers ADD COLUMN service_type TEXT DEFAULT 'chat'")
            if "options" not in cols:
                cursor.execute("ALTER TABLE providers ADD COLUMN options TEXT")
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
            model=os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-5"),
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
            model=os.getenv("OPENAI_MODEL", "gpt-5.6-terra"),
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
            model=os.getenv("GEMINI_FLASH_MODEL", "gemini-3.8-flash"),
            base_url="https://generativelanguage.googleapis.com/v1beta",
            enabled=True
        ),
        "gemini-lite": ProviderConfig(
            name="Gemini (Flash-Lite)",
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            model=os.getenv("GEMINI_LITE_MODEL", "gemini-3.5-flash-lite"),
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
        "deepseek": ProviderConfig(
            name="DeepSeek",
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            base_url="https://api.deepseek.com",
            enabled=True
        ),
        "mistral": ProviderConfig(
            name="Mistral",
            api_key=os.getenv("MISTRAL_API_KEY", ""),
            model=os.getenv("MISTRAL_MODEL", "mistral-medium-latest"),
            base_url="https://api.mistral.ai/v1",
            enabled=True
        ),
        # STT Providers
        "whisper": ProviderConfig(
            name="Whisper (OpenAI)",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model="gpt-transcribe",
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
            name="GPT Image (OpenAI)",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model="gpt-image-2.5-flare",
            base_url="https://api.openai.com/v1",
            enabled=True,
            service_type="image"
        ),
        "imagen": ProviderConfig(
            name="Gemini Image (Google)",
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            model="gemini-3.1-flash-image",
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

        cursor.execute("SELECT id, name, api_key, model, base_url, enabled, service_type, options FROM providers")
        providers = {}
        for row in cursor.fetchall():
            providers[row[0]] = ProviderConfig(
                name=row[1],
                api_key=row[2],
                model=row[3],
                base_url=row[4],
                enabled=bool(row[5]),
                service_type=row[6] or "chat",
                options=_parse_options(row[7]),
            )

        cursor.execute("SELECT value FROM settings WHERE key = 'default_provider'")
        result = cursor.fetchone()
        default_provider = result[0] if result else "claude"

        return AIConfig(providers=providers, default_provider=default_provider)
    finally:
        conn.close()


def _parse_options(raw) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


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
            ph = "%s" if USE_POSTGRES else "?"
            cursor.execute(f'''
                INSERT INTO providers (id, name, api_key, model, base_url, enabled, service_type, options)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            ''', (provider_id, provider.name, provider.api_key, provider.model,
                  provider.base_url, 1 if provider.enabled else 0, provider.service_type,
                  json.dumps(provider.options, ensure_ascii=False) if provider.options else None))

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


# Known-retired model IDs per alias -> replacement.
RETIRED_MODELS = {
    "gemini-pro":   ({"gemini-3-pro-preview", "gemini-3.1-pro"}, "gemini-pro-latest"),
    "gemini-flash": ({"gemini-2.5-flash"},                       "gemini-flash-latest"),
    "claude-haiku": ({"claude-haiku-4-6"},                       "claude-haiku-4-5-20251001"),
    # Retired image models (OpenAI DALL-E 2/3; Imagen 3 removed from the Gemini API)
    "dall-e":       ({"dall-e-3", "dall-e-2", "gpt-image-1"},    "gpt-image-2.5-flare"),
    "imagen":       ({"imagen-3.0-generate-002", "imagen-3.0-generate-001"}, "gemini-3.1-flash-image"),
    # Old DeepSeek names (V3 / V4 era); some still work upstream, but only "temporarily"
    "deepseek":     ({"deepseek-chat", "deepseek-reasoner", "deepseek-v3", "deepseek-v4",
                      "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}, "deepseek-flash"),
    "mistral":      ({"magistral-medium-2509", "magistral-medium-2507", "mistral-medium-2508"}, "mistral-medium-latest"),
}
# Display names that described the retired model; replaced only if unchanged
RENAMED = {
    "dall-e": ("DALL-E 3 (OpenAI)", "GPT Image (OpenAI)"),
    "imagen": ("Imagen 3 (Google)", "Gemini Image (Google)"),
}
_RETIRED_BY_ID = {bad: good for bad_ids, good in RETIRED_MODELS.values() for bad in bad_ids}
# Retired IDs whose successor is not the alias's usual model (verified to
# return 400 upstream on 2026-09-19); healed in requests and alias configs.
RETIRED_BY_ID_EXTRA = {
    "mistral-large-2411": "mistral-large-latest",
    "pixtral-large-latest": "mistral-large-latest",
    "pixtral-large-2411": "mistral-large-latest",
    "magistral-small-2509": "mistral-small-latest",
    "magistral-small-2507": "mistral-small-latest",
}
_RETIRED_BY_ID.update(RETIRED_BY_ID_EXTRA)


def heal_model_id(model: str) -> str:
    """Replacement for a known-retired model ID (used for request-level models)."""
    return _RETIRED_BY_ID.get(model, model)


# Recommended model changes, each version applied ONCE to existing configs
# (exact matches of the previous values only). The settings marker
# model_defaults_version records the last applied version, so a later manual
# change back is never overwritten. Versions sort as strings; append only.
MODEL_UPGRADE_VERSIONS = [
    ("2026-09-19", {  # chat benchmark
        "gemini-flash":  ({"gemini-flash-latest", "gemini-3.5-flash"}, "gemini-3.8-flash"),
        "chatgpt":       ({"gpt-5.1", "gpt-5"}, "gpt-5.6-terra"),
        "openai":        ({"gpt-5.5", "gpt-5.4", "gpt-5.1", "gpt-5"}, "gpt-5.6-terra"),
        "claude-sonnet": ({"claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet-4-5-20250929"}, "claude-sonnet-5"),
    }),
    ("2026-09-19b", {  # whisper-1 retires 2027-02-26; gpt-transcribe measured better on Korean
        "whisper": ({"whisper-1"}, "gpt-transcribe"),
    }),
]
MODEL_DEFAULTS_VERSION = MODEL_UPGRADE_VERSIONS[-1][0]


def _apply_model_upgrades(config: AIConfig, applied_version: Optional[str]) -> list:
    upgraded = []
    for version, upgrades in MODEL_UPGRADE_VERSIONS:
        if applied_version and version <= applied_version:
            continue
        for pid, (old_ids, new) in upgrades.items():
            p = config.providers.get(pid)
            if p and p.model in old_ids:
                upgraded.append(f"{pid}:{p.model}->{new}")
                config.providers[pid] = p.model_copy(update={"model": new})
    return upgraded


def _migrate(config: AIConfig) -> bool:
    """Startup-time fixups. Returns True if the DB copy needs saving."""
    changed = False

    # Auto-merge new default providers not yet in DB. A new alias of an existing
    # vendor takes its key from that vendor's configured alias (the DB key can
    # differ from the env default).
    KEY_DONORS = {"gemini-lite": "gemini-flash"}
    added = []
    for pid, pconfig in _get_default_providers().items():
        if pid not in config.providers:
            donor = config.providers.get(KEY_DONORS.get(pid, ""))
            if donor and donor.api_key:
                pconfig = pconfig.model_copy(update={"api_key": donor.api_key})
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

    # Auto-heal known-retired model IDs (exact matches only; custom models untouched)
    healed = []
    for pid, (bad_ids, good) in RETIRED_MODELS.items():
        p = config.providers.get(pid)
        if p and p.model in bad_ids:
            healed.append(f"{pid}:{p.model}->{good}")
            update = {"model": good}
            old_name, new_name = RENAMED.get(pid, (None, None))
            if p.name == old_name:
                update["name"] = new_name
            config.providers[pid] = p.model_copy(update=update)
    for pid, p in list(config.providers.items()):
        if p.model in RETIRED_BY_ID_EXTRA:
            healed.append(f"{pid}:{p.model}->{RETIRED_BY_ID_EXTRA[p.model]}")
            config.providers[pid] = p.model_copy(update={"model": RETIRED_BY_ID_EXTRA[p.model]})
    if healed:
        changed = True
        print(f"[CONFIG] Auto-healed retired models: {healed}")

    return changed


# Aliases added to existing deployments (auto-added, possibly before their
# Railway key variable existed). Older aliases were seeded with their keys.
ENV_KEY_FILL = {"deepseek", "mistral"}


def _fill_env_keys(config: AIConfig) -> bool:
    """Startup only: an ENV_KEY_FILL alias stored without a key picks up its
    env key once one is set. Keys in the DB are never overwritten. To switch
    such a provider off, disable it (enabled=false) or remove the variable:
    a key cleared in the admin UI comes back at the next restart while the
    variable is set."""
    filled = []
    for pid, default in _get_default_providers().items():
        p = config.providers.get(pid)
        if pid in ENV_KEY_FILL and p and not p.api_key and default.api_key:
            config.providers[pid] = p.model_copy(update={"api_key": default.api_key})
            filled.append(pid)
    if filled:
        print(f"[CONFIG] API keys filled from environment: {filled}")
    return bool(filled)


def _bootstrap_from_db() -> AIConfig:
    """Load (and if needed seed / migrate) config from the DB. Raises on DB errors."""
    init_db()

    config = _load_from_db()
    if config:
        changed = _migrate(config)
        if _fill_env_keys(config) or changed:
            _save_to_db(config)
    else:
        # Genuinely empty DB: seed from legacy JSON, else from environment.
        config = _load_from_json() or AIConfig(
            providers=_get_default_providers(),
            default_provider=os.getenv("DEFAULT_AI_PROVIDER", "claude-sonnet")
        )
        _save_to_db(config)

    settings = _load_settings_from_db()
    if settings.get("model_defaults_version") != MODEL_DEFAULTS_VERSION:
        upgraded = _apply_model_upgrades(config, settings.get("model_defaults_version"))
        if upgraded:
            _save_to_db(config)
            print(f"[CONFIG] Applied recommended models ({MODEL_DEFAULTS_VERSION}): {upgraded}")
        conn = _connect()
        try:
            _upsert_setting(conn.cursor(), "model_defaults_version", MODEL_DEFAULTS_VERSION)
            conn.commit()
        finally:
            conn.close()
        settings["model_defaults_version"] = MODEL_DEFAULTS_VERSION
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
