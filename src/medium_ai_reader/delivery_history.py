"""Persistent delivery history using PostgreSQL.

Stores sent article URLs to prevent re-sending across cron job runs.
Render cron jobs have no persistent disk, so we use a managed Postgres instance.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Sequence

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None


class DeliveryHistoryError(RuntimeError):
    """Raised when delivery history is required but cannot be used."""


@dataclass(frozen=True)
class DeliveryRecordResult:
    attempted: int
    inserted: int
    skipped_existing: int


def normalize_url(url: str) -> str:
    """Normalize a Medium URL for deduplication.

    Strips protocol, query params, fragments, and trailing slashes.
    Converts to lowercase for case-insensitive comparison.
    """
    try:
        parsed = urllib.parse.urlparse(url.strip())
        # Keep only netloc + path, lowercase
        normalized = (parsed.netloc + parsed.path).lower()
        # Remove trailing slash
        normalized = re.sub(r"/+$", "", normalized)
        return normalized
    except Exception:
        return url.strip().lower()


def article_history_key(url: str) -> str:
    """Return a stable delivery-history key for an article URL."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        path = urllib.parse.unquote(parsed.path)
        match = re.search(r"(?:^|[-/])([0-9a-f]{12})(?:/)?$", path, flags=re.IGNORECASE)
        if match:
            return f"medium-post:{match.group(1).lower()}"
    except Exception:
        pass
    return f"url:{normalize_url(url)}"


def article_lookup_keys(url: str) -> tuple[str, str]:
    """Return primary and legacy keys used to find previously sent articles."""
    primary = article_history_key(url)
    legacy = normalize_url(url)
    return primary, legacy


class DeliveryHistory:
    """Manages persistent delivery history in PostgreSQL."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv("DIGEST_DB_DSN")
        self._conn = None

    def _get_conn(self):
        """Get or create a database connection."""
        if not psycopg:
            raise DeliveryHistoryError("psycopg not installed. Add 'psycopg[binary]' to requirements.txt")
        if not self.dsn:
            raise DeliveryHistoryError("No database DSN. Set DIGEST_DB_DSN environment variable.")
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.dsn, row_factory=dict_row)
        return self._conn

    @property
    def is_available(self) -> bool:
        """Check if the database is configured and accessible."""
        if not self.dsn:
            return False
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    def prepare(self, *, required: bool = False) -> bool:
        """Initialize schema and report whether delivery history is active."""
        if not self.dsn:
            if required:
                raise DeliveryHistoryError(
                    "Delivery history is required, but DIGEST_DB_DSN is not set. "
                    "Set DIGEST_DB_DSN to a PostgreSQL connection string or set "
                    "DIGEST_REQUIRE_DELIVERY_HISTORY=false to allow duplicate-prone sends."
                )
            return False

        try:
            self.init_schema()
        except Exception as exc:
            self.close()
            if required:
                raise DeliveryHistoryError(f"Delivery history database is not available: {exc}") from exc
            return False

        return True

    def init_schema(self) -> None:
        """Create the sent_articles table if it doesn't exist."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sent_articles (
                    id SERIAL PRIMARY KEY,
                    normalized_url TEXT UNIQUE NOT NULL,
                    title TEXT,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_sent_articles_url 
                ON sent_articles(normalized_url);
                CREATE INDEX IF NOT EXISTS idx_sent_articles_sent_at 
                ON sent_articles(sent_at DESC);
            """)
            conn.commit()

    def get_sent_urls(self, urls: Sequence[str]) -> set[str]:
        """Check which URLs have already been sent.

        Returns a set of normalized URLs that are already in the database.
        Returns empty set if database is not configured.
        """
        if not urls:
            return set()
        
        if not self.dsn:
            return set()

        lookup_keys = sorted({key for url in urls for key in article_lookup_keys(url)})
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT normalized_url FROM sent_articles WHERE normalized_url = ANY(%s)",
                (lookup_keys,)
            )
            return {row["normalized_url"] for row in cur.fetchall()}

    def filter_unsent(self, articles: Sequence) -> list:
        """Filter out articles that have already been sent.

        Returns a new list containing only unsent articles.
        If database is not configured, returns all articles (no filtering).
        """
        if not articles:
            return []
        
        if not self.dsn:
            return list(articles)

        urls = [a.url for a in articles]
        sent = self.get_sent_urls(urls)
        return [a for a in articles if not any(key in sent for key in article_lookup_keys(a.url))]

    def record_sent(self, articles: Sequence) -> DeliveryRecordResult:
        """Record that articles were sent successfully.

        Inserts normalized URLs into sent_articles table.
        Uses ON CONFLICT DO NOTHING to handle race conditions gracefully.
        Returns insert counts for logging.
        """
        if not articles:
            return DeliveryRecordResult(attempted=0, inserted=0, skipped_existing=0)
        
        if not self.dsn:
            return DeliveryRecordResult(attempted=len(articles), inserted=0, skipped_existing=len(articles))

        conn = self._get_conn()
        inserted = 0
        with conn.cursor() as cur:
            for article in articles:
                history_key = article_history_key(article.url)
                cur.execute(
                    """INSERT INTO sent_articles (normalized_url, title)
                       VALUES (%s, %s)
                       ON CONFLICT (normalized_url) DO NOTHING
                       RETURNING id""",
                    (history_key, article.title[:500])  # Truncate title if too long
                )
                if cur.fetchone() is not None:
                    inserted += 1
            conn.commit()
        return DeliveryRecordResult(
            attempted=len(articles),
            inserted=inserted,
            skipped_existing=len(articles) - inserted,
        )

    def close(self) -> None:
        """Close the database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
