"""SQLite persistence: every evaluated candidate, mention-count history, and backtest results."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,               -- ISO8601 UTC
    ticker TEXT NOT NULL,

    social_mentions_now REAL,
    social_mentions_baseline REAL,
    social_ratio REAL,
    social_score REAL,

    price REAL,
    price_change_pct REAL,
    volume REAL,
    avg_volume_20d REAL,
    volume_ratio REAL,
    intraday_volatility_pct REAL,
    price_volume_score REAL,

    options_available INTEGER,
    put_call_volume_ratio REAL,
    options_score REAL,

    news_count_recent REAL,
    news_count_baseline REAL,
    news_score REAL,

    total_score REAL NOT NULL,
    crossed_threshold INTEGER NOT NULL,
    alerted INTEGER NOT NULL DEFAULT 0,

    raw_json TEXT NOT NULL                 -- full raw signal payload, for fields not modeled above
);

CREATE INDEX IF NOT EXISTS idx_signals_ticker_time ON signals(ticker, timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_crossed ON signals(crossed_threshold, timestamp);

CREATE TABLE IF NOT EXISTS mention_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,                  -- 'stocktwits' | 'reddit'
    ticker TEXT NOT NULL,
    count REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mentions_ticker_source_time
    ON mention_history(ticker, source, timestamp);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    ticker TEXT NOT NULL,
    score REAL NOT NULL,
    signal_id INTEGER NOT NULL REFERENCES signals(id)
);

CREATE INDEX IF NOT EXISTS idx_alerts_ticker_time ON alerts(ticker, timestamp);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_timestamp TEXT NOT NULL,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    ticker TEXT NOT NULL,
    signal_timestamp TEXT NOT NULL,
    entry_price REAL,
    return_1d_pct REAL,
    return_3d_pct REAL,
    return_7d_pct REAL,
    crossed_threshold INTEGER NOT NULL
);
"""


@dataclass
class SignalRecord:
    timestamp: str
    ticker: str
    social_mentions_now: Optional[float]
    social_mentions_baseline: Optional[float]
    social_ratio: Optional[float]
    social_score: Optional[float]
    price: Optional[float]
    price_change_pct: Optional[float]
    volume: Optional[float]
    avg_volume_20d: Optional[float]
    volume_ratio: Optional[float]
    intraday_volatility_pct: Optional[float]
    price_volume_score: Optional[float]
    options_available: bool
    put_call_volume_ratio: Optional[float]
    options_score: Optional[float]
    news_count_recent: Optional[float]
    news_count_baseline: Optional[float]
    news_score: Optional[float]
    total_score: float
    crossed_threshold: bool
    raw: dict = field(default_factory=dict)


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- signals -----------------------------------------------------

    def insert_signal(self, record: SignalRecord) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO signals (
                    timestamp, ticker,
                    social_mentions_now, social_mentions_baseline, social_ratio, social_score,
                    price, price_change_pct, volume, avg_volume_20d, volume_ratio,
                    intraday_volatility_pct, price_volume_score,
                    options_available, put_call_volume_ratio, options_score,
                    news_count_recent, news_count_baseline, news_score,
                    total_score, crossed_threshold, alerted, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    record.timestamp, record.ticker,
                    record.social_mentions_now, record.social_mentions_baseline,
                    record.social_ratio, record.social_score,
                    record.price, record.price_change_pct, record.volume,
                    record.avg_volume_20d, record.volume_ratio,
                    record.intraday_volatility_pct, record.price_volume_score,
                    int(record.options_available), record.put_call_volume_ratio, record.options_score,
                    record.news_count_recent, record.news_count_baseline, record.news_score,
                    record.total_score, int(record.crossed_threshold),
                    json.dumps(record.raw),
                ),
            )
            return cur.lastrowid

    def mark_alerted(self, signal_id: int, ticker: str, score: float, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE signals SET alerted = 1 WHERE id = ?", (signal_id,))
            conn.execute(
                "INSERT INTO alerts (timestamp, ticker, score, signal_id) VALUES (?, ?, ?, ?)",
                (timestamp, ticker, score, signal_id),
            )

    def last_alert_time(self, ticker: str) -> Optional[datetime]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT timestamp FROM alerts WHERE ticker = ? ORDER BY timestamp DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row["timestamp"])

    def is_in_cooldown(self, ticker: str, cooldown_hours: float, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        last = self.last_alert_time(ticker)
        if last is None:
            return False
        return now - last < timedelta(hours=cooldown_hours)

    def fetch_signals(
        self,
        crossed_threshold_only: bool = False,
        since: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM signals WHERE 1=1"
        params: list = []
        if crossed_threshold_only:
            query += " AND crossed_threshold = 1"
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " ORDER BY timestamp ASC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

    # ---- mention history / baselines ----------------------------------

    def record_mentions(self, source: str, ticker: str, count: float, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mention_history (timestamp, source, ticker, count) VALUES (?, ?, ?, ?)",
                (timestamp, source, ticker, count),
            )

    def mention_baseline(
        self, source: str, ticker: str, lookback_days: float, before: datetime, cold_start_value: float
    ) -> float:
        window_start = before - timedelta(days=lookback_days)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT AVG(count) AS avg_count FROM mention_history
                WHERE source = ? AND ticker = ? AND timestamp >= ? AND timestamp < ?
                """,
                (source, ticker, window_start.isoformat(), before.isoformat()),
            ).fetchone()
        if row is None or row["avg_count"] is None:
            return cold_start_value
        return max(float(row["avg_count"]), cold_start_value)

    # ---- backtest -------------------------------------------------------

    def insert_backtest_result(
        self,
        run_timestamp: str,
        signal_id: int,
        ticker: str,
        signal_timestamp: str,
        entry_price: Optional[float],
        return_1d_pct: Optional[float],
        return_3d_pct: Optional[float],
        return_7d_pct: Optional[float],
        crossed_threshold: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_results (
                    run_timestamp, signal_id, ticker, signal_timestamp, entry_price,
                    return_1d_pct, return_3d_pct, return_7d_pct, crossed_threshold
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_timestamp, signal_id, ticker, signal_timestamp, entry_price,
                    return_1d_pct, return_3d_pct, return_7d_pct, int(crossed_threshold),
                ),
            )
