"""Usage logging module - tracks all AI API requests for analytics."""
import time
import logging
from datetime import datetime, timezone
from typing import Optional

from .config import USE_POSTGRES, _get_pg_connection, _get_sqlite_connection

logger = logging.getLogger(__name__)


def init_usage_table():
    """Create usage_logs table if not exists."""
    sql = '''
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY {autoincrement},
            timestamp TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            elapsed_ms INTEGER DEFAULT 0,
            success INTEGER DEFAULT 1,
            error_message TEXT,
            caller TEXT
        )
    '''

    if USE_POSTGRES:
        # PostgreSQL uses SERIAL for auto-increment
        sql = sql.replace("INTEGER PRIMARY KEY {autoincrement}", "SERIAL PRIMARY KEY")
        conn = _get_pg_connection()
    else:
        sql = sql.replace("{autoincrement}", "AUTOINCREMENT")
        conn = _get_sqlite_connection()

    cursor = conn.cursor()
    cursor.execute(sql)
    conn.commit()
    conn.close()


def log_usage(
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    elapsed_ms: int = 0,
    success: bool = True,
    error_message: Optional[str] = None,
    caller: Optional[str] = None
):
    """Log a single API usage record."""
    try:
        now = datetime.now(timezone.utc).isoformat()

        if USE_POSTGRES:
            conn = _get_pg_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO usage_logs (timestamp, provider, model, input_tokens, output_tokens, elapsed_ms, success, error_message, caller)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (now, provider, model, input_tokens, output_tokens, elapsed_ms,
                  1 if success else 0, error_message, caller))
        else:
            conn = _get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO usage_logs (timestamp, provider, model, input_tokens, output_tokens, elapsed_ms, success, error_message, caller)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (now, provider, model, input_tokens, output_tokens, elapsed_ms,
                  1 if success else 0, error_message, caller))

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[USAGE LOG ERROR] {e}")


def get_usage_stats(days: int = 7, provider: Optional[str] = None):
    """Get aggregated usage statistics."""
    if USE_POSTGRES:
        conn = _get_pg_connection()
        date_filter = f"timestamp >= (NOW() - INTERVAL '{days} days')::text"
        placeholder = "%s"
    else:
        conn = _get_sqlite_connection()
        date_filter = f"timestamp >= datetime('now', '-{days} days')"
        placeholder = "?"

    cursor = conn.cursor()

    # Per-provider summary
    query = f'''
        SELECT
            provider,
            COUNT(*) as request_count,
            SUM(input_tokens) as total_input_tokens,
            SUM(output_tokens) as total_output_tokens,
            ROUND(AVG(elapsed_ms)) as avg_elapsed_ms,
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as error_count
        FROM usage_logs
        WHERE {date_filter}
    '''
    params = []
    if provider:
        query += f" AND provider = {placeholder}"
        params.append(provider)
    query += " GROUP BY provider ORDER BY request_count DESC"

    cursor.execute(query, params)
    columns = ["provider", "request_count", "total_input_tokens", "total_output_tokens",
               "avg_elapsed_ms", "success_count", "error_count"]
    providers_stats = [dict(zip(columns, row)) for row in cursor.fetchall()]

    # Daily breakdown
    if USE_POSTGRES:
        date_extract = "LEFT(timestamp, 10)"
    else:
        date_extract = "DATE(timestamp)"

    daily_query = f'''
        SELECT
            {date_extract} as date,
            COUNT(*) as request_count,
            SUM(input_tokens) as total_input_tokens,
            SUM(output_tokens) as total_output_tokens
        FROM usage_logs
        WHERE {date_filter}
        GROUP BY {date_extract}
        ORDER BY date DESC
        LIMIT 30
    '''
    cursor.execute(daily_query)
    daily_columns = ["date", "request_count", "total_input_tokens", "total_output_tokens"]
    daily_stats = [dict(zip(daily_columns, row)) for row in cursor.fetchall()]

    # Total summary
    total_query = f'''
        SELECT
            COUNT(*) as total_requests,
            COALESCE(SUM(input_tokens), 0) as total_input_tokens,
            COALESCE(SUM(output_tokens), 0) as total_output_tokens,
            ROUND(COALESCE(AVG(elapsed_ms), 0)) as avg_elapsed_ms
        FROM usage_logs
        WHERE {date_filter}
    '''
    cursor.execute(total_query)
    row = cursor.fetchone()
    total = {
        "total_requests": row[0],
        "total_input_tokens": row[1],
        "total_output_tokens": row[2],
        "avg_elapsed_ms": row[3]
    }

    conn.close()

    return {
        "period_days": days,
        "total": total,
        "by_provider": providers_stats,
        "daily": daily_stats
    }


def get_recent_logs(limit: int = 50):
    """Get recent usage log entries."""
    if USE_POSTGRES:
        conn = _get_pg_connection()
    else:
        conn = _get_sqlite_connection()

    cursor = conn.cursor()
    cursor.execute('''
        SELECT timestamp, provider, model, input_tokens, output_tokens, elapsed_ms, success, error_message, caller
        FROM usage_logs
        ORDER BY id DESC
        LIMIT ?
    '''.replace('?', '%s' if USE_POSTGRES else '?'), (limit,))

    columns = ["timestamp", "provider", "model", "input_tokens", "output_tokens",
               "elapsed_ms", "success", "error_message", "caller"]
    logs = [dict(zip(columns, row)) for row in cursor.fetchall()]

    conn.close()
    return logs
