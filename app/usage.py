"""Usage logging module - tracks all AI API requests for analytics.

Rows are written by a dedicated background thread over one persistent DB
connection, so logging never delays a response or blocks the event loop.
Rules are unchanged: one row per provider attempt, timestamp taken when the
attempt ends, write failures are swallowed (counted, not raised).
"""
import queue
import threading
import time
import logging
from datetime import datetime, timezone
from typing import Optional

from .config import USE_POSTGRES, _get_pg_connection, _get_sqlite_connection

logger = logging.getLogger(__name__)

_QUEUE_MAX = 10000
_BATCH_MAX = 200


def _usage_table_sql() -> str:
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
        return sql.replace("INTEGER PRIMARY KEY {autoincrement}", "SERIAL PRIMARY KEY")
    return sql.replace("{autoincrement}", "AUTOINCREMENT")


def init_usage_table():
    """Create usage_logs table if not exists."""
    conn = _get_pg_connection() if USE_POSTGRES else _get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(_usage_table_sql())
        conn.commit()
    finally:
        conn.close()


_INSERT_SQL = '''
    INSERT INTO usage_logs (timestamp, provider, model, input_tokens, output_tokens, elapsed_ms, success, error_message, caller)
    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
'''.format(p="%s" if USE_POSTGRES else "?")

_STOP = object()


class _UsageWriter:
    """Single writer thread + bounded queue + persistent connection (its own,
    separate from config connections). Not the default executor: google-genai
    runs its blocking HTTP calls there."""

    def __init__(self):
        self._q: "queue.Queue" = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.dropped = 0   # queue full
        self.failed = 0    # DB write failed after reconnect
        self.written = 0
        self._index_checked = False

    def start(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="usage-writer", daemon=True)
                self._thread.start()

    def submit(self, row: tuple):
        self.start()
        try:
            self._q.put_nowait(row)
        except queue.Full:
            self.dropped += 1
            if self.dropped % 100 == 1:
                logger.warning(f"[USAGE LOG] queue full, dropped={self.dropped}")

    def stop(self, timeout: float = 5.0):
        """Flush what is queued (bounded by timeout) and stop the thread."""
        if self._thread is None or not self._thread.is_alive():
            return
        try:
            self._q.put(_STOP, timeout=timeout)
        except queue.Full:
            return
        self._thread.join(timeout)

    def stats(self) -> dict:
        return {"queued": self._q.qsize(), "written": self.written,
                "dropped": self.dropped, "failed": self.failed}

    def _run(self):
        conn = None
        stop = False
        while not stop:
            item = self._q.get()
            if item is _STOP:
                break
            batch = [item]
            while len(batch) < _BATCH_MAX:
                try:
                    nxt = self._q.get_nowait()
                except queue.Empty:
                    break
                if nxt is _STOP:
                    stop = True
                    break
                batch.append(nxt)
            conn = self._write(conn, batch)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _connect(self):
        conn = _get_pg_connection() if USE_POSTGRES else _get_sqlite_connection()
        cur = conn.cursor()
        cur.execute(_usage_table_sql())
        conn.commit()
        if not self._index_checked:
            self._ensure_index(conn)
        return conn

    def _ensure_index(self, conn):
        """Index for the dashboard's timestamp range scans. Built once, here in
        the writer thread (not in startup), CONCURRENTLY on PostgreSQL so inserts
        are not blocked while it builds."""
        self._index_checked = True
        sql = "CREATE INDEX {c} IF NOT EXISTS idx_usage_logs_timestamp ON usage_logs (timestamp)"
        try:
            if USE_POSTGRES:
                conn.autocommit = True
                try:
                    conn.cursor().execute(sql.format(c="CONCURRENTLY"))
                finally:
                    conn.autocommit = False
            else:
                conn.cursor().execute(sql.format(c=""))
                conn.commit()
        except Exception as e:
            logger.warning(f"[USAGE LOG] timestamp index not created: {type(e).__name__}: {e}")

    @staticmethod
    def _close(conn):
        try:
            conn.close()
        except Exception:
            pass
        return None

    def _write(self, conn, batch):
        # Whole batch in one transaction.
        try:
            if conn is None:
                conn = self._connect()
            conn.cursor().executemany(_INSERT_SQL, batch)
            conn.commit()
            self.written += len(batch)
            return conn
        except Exception as e:
            logger.error(f"[USAGE LOG ERROR] batch of {len(batch)}: {type(e).__name__}: {e}")
            conn = self._close(conn)

        # Retry row by row on a fresh connection, so a bad row loses only itself.
        # (If the batch commit failed after the server had committed, rows can
        # be written twice; that needs a connection drop at commit time.)
        for i, row in enumerate(batch):
            if conn is None:
                try:
                    conn = self._connect()
                except Exception as e:
                    logger.error(f"[USAGE LOG ERROR] reconnect failed: {type(e).__name__}: {e}")
                    self.failed += len(batch) - i
                    return None
            try:
                conn.cursor().execute(_INSERT_SQL, row)
                conn.commit()
                self.written += 1
            except Exception as e:
                logger.error(f"[USAGE LOG ERROR] row dropped: {type(e).__name__}: {e}")
                self.failed += 1
                try:
                    conn.rollback()
                except Exception:
                    conn = self._close(conn)
        return conn


usage_writer = _UsageWriter()


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
    """Queue a single API usage record (non-blocking)."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        # PostgreSQL rejects NUL bytes in text; provider error bodies can contain them
        clean = lambda v: v.replace("\x00", "") if isinstance(v, str) else v
        usage_writer.submit((now, clean(provider), clean(model), input_tokens, output_tokens, elapsed_ms,
                             1 if success else 0, clean(error_message), clean(caller)))
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
