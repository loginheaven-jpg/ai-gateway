import os
import json
import sqlite3
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

# Use data directory for SQLite (can be mounted as Railway Volume)
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "config.db"
CONFIG_FILE = DATA_DIR / "ai-config.json"


class ProviderConfig(BaseModel):
    name: str
    api_key: str
    model: str
    base_url: str
    enabled: bool = True


class AIConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    default_provider: str = "claude"


def init_db():
    """Initialize SQLite database for config storage"""
    conn = sqlite3.connect(str(DB_FILE))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            api_key TEXT NOT NULL,
            model TEXT NOT NULL,
            base_url TEXT NOT NULL,
            enabled INTEGER DEFAULT 1
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    conn.close()


def _get_default_providers():
    """Get default provider configurations from environment or defaults"""
    return {
        "claude": ProviderConfig(
            name="Claude (Anthropic)",
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
            base_url="https://api.anthropic.com/v1",
            enabled=True
        ),
        "openai": ProviderConfig(
            name="GPT (OpenAI)",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            base_url="https://api.openai.com/v1",
            enabled=True
        ),
        "gemini-pro": ProviderConfig(
            name="Gemini (Pro)",
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            model=os.getenv("GEMINI_PRO_MODEL", "gemini-1.5-pro"),
            base_url="https://generativelanguage.googleapis.com/v1beta",
            enabled=True
        ),
        "gemini-flash": ProviderConfig(
            name="Gemini (Flash)",
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            model=os.getenv("GEMINI_FLASH_MODEL", "gemini-2.0-flash-exp"),
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
            model=os.getenv("PERPLEXITY_MODEL", "llama-3.1-sonar-large-128k-online"),
            base_url="https://api.perplexity.ai",
            enabled=True
        )
    }


def _load_from_db() -> Optional[AIConfig]:
    """Load configuration from SQLite database"""
    if not DB_FILE.exists():
        return None

    conn = sqlite3.connect(str(DB_FILE))
    cursor = conn.cursor()

    # Check if providers table has data
    cursor.execute("SELECT COUNT(*) FROM providers")
    count = cursor.fetchone()[0]

    if count == 0:
        conn.close()
        return None

    # Load providers
    cursor.execute("SELECT id, name, api_key, model, base_url, enabled FROM providers")
    providers = {}
    for row in cursor.fetchall():
        providers[row[0]] = ProviderConfig(
            name=row[1],
            api_key=row[2],
            model=row[3],
            base_url=row[4],
            enabled=bool(row[5])
        )

    # Load default provider
    cursor.execute("SELECT value FROM settings WHERE key = 'default_provider'")
    result = cursor.fetchone()
    default_provider = result[0] if result else "claude"

    conn.close()
    return AIConfig(providers=providers, default_provider=default_provider)


def _save_to_db(config: AIConfig):
    """Save configuration to SQLite database"""
    init_db()
    conn = sqlite3.connect(str(DB_FILE))
    cursor = conn.cursor()

    # Clear and insert providers
    cursor.execute("DELETE FROM providers")
    for provider_id, provider in config.providers.items():
        cursor.execute('''
            INSERT INTO providers (id, name, api_key, model, base_url, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (provider_id, provider.name, provider.api_key, provider.model,
              provider.base_url, 1 if provider.enabled else 0))

    # Save default provider
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
    1. SQLite database (primary for production)
    2. JSON file (legacy/development)
    3. Environment variables (initial setup)
    """
    init_db()

    # Try loading from database first
    config = _load_from_db()
    if config:
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
        default_provider=os.getenv("DEFAULT_AI_PROVIDER", "claude")
    )
    _save_to_db(default_config)
    return default_config


def save_config(config: AIConfig) -> None:
    """Save AI configuration to SQLite database"""
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
        default_provider="claude"
    )
    _save_to_db(default_config)
    return default_config
