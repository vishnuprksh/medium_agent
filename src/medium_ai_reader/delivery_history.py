"""Persistent delivery history using PostgreSQL.

Stores sent article URLs to prevent re-sending across cron job runs.
Render cron jobs have no persistent disk, so we use a managed Postgres instance.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import List, Sequence

try:
    import psycopg
    from psycopg.rows import class_row, dict_row
except ImportError:
    psycopg = None


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


class DeliveryHistory:
    """Manages persistent delivery history in PostgreSQL."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv("DIGEST_DB_DSN")
        self._conn = None

    def _get_conn(self):
        """Get or create a database connection."""
        if not psycopg:
            raise RuntimeError("psycopg not installed. Add 'psycopg[binary]' to requirements.txt")
        if not self.dsn:
            raise RuntimeError("No database DSN. Set DIGEST_DB_DSN environment variable.")
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.dsn, row_factory=dict_row)
        return self._conn

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
        """
        if not urls:
            return set()

        normalized = [normalize_url(u) for u in urls]
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT normalized_url FROM sent_articles WHERE normalized_url = ANY(%s)",
                (normalized,)
            )
            return {row["normalized_url"] for row in cur.fetchall()}

    def filter_unsent(self, articles: Sequence) -> list:
        """Filter out articles that have already been sent.

        Returns a new list containing only unsent articles.
        """
        if not articles:
            return []

        urls = [a.url for a in articles]
        sent = self.get_sent_urls(urls)
        return [a for a in articles if normalize_url(a.url) not in sent]

    def record_sent(self, articles: Sequence) -> None:
        """Record that articles were sent successfully.

        Inserts normalized URLs into sent_articles table.
        Uses ON CONFLICT DO NOTHING to handle race conditions gracefully.
        """
        if not articles:
            return

        conn = self._get_conn()
        with conn.cursor() as cur:
            for article in articles:
                norm_url = normalize_url(article.url)
                cur.execute(
                    """INSERT INTO sent_articles (normalized_url, title)
                       VALUES (%s, %s)
                       ON CONFLICT (normalized_url) DO NOTHING""",
                    (norm_url, article.title[:500])  # Truncate title if too long
                )
            conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
