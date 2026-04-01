import os
import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from pydantic import BaseModel

# Database configuration
# Priority: DATABASE_URL (PostgreSQL) > SQLite file
DATABASE_URL = os.getenv("DATABASE_URL")

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


def _get_pg_connection():
    """Get PostgreSQL connection"""
    import psycopg2
    try:
        print(f"[CONFIG] Connecting to PostgreSQL...")
        conn = psycopg2.connect(DATABASE_URL)
        print(f"[CONFIG] PostgreSQL connection successful")
        return conn
    except Exception as e:
        print(f"[CONFIG] PostgreSQL connection failed: {type(e).__name__}: {e}")
        raise


def _get_sqlite_connection():
    """Get SQLite connection"""
    import sqlite3
    return sqlite3.connect(str(DB_FILE))


def init_db():
    """Initialize database (PostgreSQL or SQLite)"""
    if USE_POSTGRES:
        conn = _get_pg_connection()
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
        # Add service_type column if not exists (migration)
        try:
            cursor.execute("ALTER TABLE providers ADD COLUMN service_type TEXT DEFAULT 'chat'")
        except Exception:
            pass  # Column already exists
        conn.commit()
        conn.close()
    else:
        import sqlite3
        conn = sqlite3.connect(str(DB_FILE))
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
        # Add service_type column if not exists (migration)
        try:
            cursor.execute("ALTER TABLE providers ADD COLUMN service_type TEXT DEFAULT 'chat'")
        except Exception:
            pass  # Column already exists
        conn.commit()
        conn.close()


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
            model=os.getenv("GEMINI_PRO_MODEL", "gemini-3-pro-preview"),
            base_url="https://generativelanguage.googleapis.com/v1beta",
            enabled=True
        ),
        "gemini-flash": ProviderConfig(
            name="Gemini (Flash)",
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            model=os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash"),
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
    """Load configuration from database (PostgreSQL or SQLite)"""
    try:
        if USE_POSTGRES:
            conn = _get_pg_connection()
        else:
            if not DB_FILE.exists():
                return None
            conn = _get_sqlite_connection()

        cursor = conn.cursor()

        # Check if providers table has data
        cursor.execute("SELECT COUNT(*) FROM providers")
        count = cursor.fetchone()[0]

        if count == 0:
            conn.close()
            return None

        # Load providers
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

        # Load default provider
        cursor.execute("SELECT value FROM settings WHERE key = 'default_provider'")
        result = cursor.fetchone()
        default_provider = result[0] if result else "claude"

        conn.close()
        return AIConfig(providers=providers, default_provider=default_provider)
    except Exception as e:
        print(f"[CONFIG] Error loading from DB: {e}")
        return None


def _save_to_db(config: AIConfig):
    """Save configuration to database (PostgreSQL or SQLite)"""
    init_db()

    if USE_POSTGRES:
        conn = _get_pg_connection()
    else:
        conn = _get_sqlite_connection()

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

    # Save default provider
    if USE_POSTGRES:
        cursor.execute('''
            INSERT INTO settings (key, value) VALUES ('default_provider', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        ''', (config.default_provider,))
    else:
        cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value) VALUES ('default_provider', ?)
        ''', (config.default_provider,))

    conn.commit()
    conn.close()


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


def load_config() -> AIConfig:
    """Load AI configuration with fallback chain:
    1. Database (PostgreSQL or SQLite)
    2. JSON file (legacy/development)
    3. Environment variables (initial setup)
    """
    init_db()

    # Try loading from database first
    config = _load_from_db()
    if config:
        # Auto-merge new default providers not yet in DB
        defaults = _get_default_providers()
        added = []
        for pid, pconfig in defaults.items():
            if pid not in config.providers:
                config.providers[pid] = pconfig
                added.append(pid)
        if added:
            _save_to_db(config)
            print(f"[CONFIG] Auto-added new providers: {added}")

        # Auto-migrate legacy aliases (openai → chatgpt)
        if "openai" in config.providers and "chatgpt" not in config.providers:
            config.providers["chatgpt"] = config.providers["openai"]
            config.providers["chatgpt"].name = "ChatGPT (OpenAI)"

        return config

    # Try loading from JSON file (for migration)
    config = _load_from_json()
    if config:
        # Migrate to database
        _save_to_db(config)
        return config

    # Use defaults from environment variables
    default_config = AIConfig(
        providers=_get_default_providers(),
        default_provider=os.getenv("DEFAULT_AI_PROVIDER", "claude-sonnet")
    )
    _save_to_db(default_config)
    return default_config


def save_config(config: AIConfig) -> None:
    """Save AI configuration to database"""
    _save_to_db(config)


def get_provider(provider_id: str) -> Optional[ProviderConfig]:
    """Get a specific provider configuration"""
    config = load_config()
    return config.providers.get(provider_id)


def update_provider(provider_id: str, updates: dict) -> ProviderConfig:
    """Update a specific provider configuration"""
    config = load_config()

    if provider_id not in config.providers:
        raise ValueError(f"Provider not found: {provider_id}")

    provider = config.providers[provider_id]
    updated_data = provider.model_dump()
    updated_data.update(updates)
    config.providers[provider_id] = ProviderConfig(**updated_data)

    save_config(config)
    return config.providers[provider_id]


def add_provider(provider_id: str, provider_config: ProviderConfig) -> ProviderConfig:
    """Add a new provider configuration"""
    config = load_config()

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
    _save_to_db(default_config)
    return default_config
