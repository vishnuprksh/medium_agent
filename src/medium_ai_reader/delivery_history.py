"""Persistent delivery history using Firebase Firestore.

Stores sent article URLs to prevent re-sending across scheduled digest runs.
Firebase Cloud Functions use Firestore through the Firebase Admin SDK.
"""

from __future__ import annotations

import hashlib
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Sequence

try:
    import firebase_admin
    from firebase_admin import firestore
except ImportError:
    firebase_admin = None
    firestore = None


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
        normalized = (parsed.netloc + parsed.path).lower()
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


def _history_document_id(history_key: str) -> str:
    """Return a Firestore-safe document ID for an arbitrary history key."""
    return hashlib.sha256(history_key.encode("utf-8")).hexdigest()


class DeliveryHistory:
    """Manages persistent delivery history in Firestore."""

    def __init__(self, collection_name: str | None = None) -> None:
        self.collection_name = (
            collection_name
            or os.getenv("DIGEST_HISTORY_COLLECTION", "").strip()
            or "sent_articles"
        )
        self._client = None

    def _get_client(self):
        """Get or create a Firestore client."""
        if firebase_admin is None or firestore is None:
            raise DeliveryHistoryError(
                "firebase-admin is not installed. Add 'firebase-admin' to requirements.txt."
            )
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        if self._client is None:
            self._client = firestore.client()
        return self._client

    def _collection(self):
        return self._get_client().collection(self.collection_name)

    @property
    def is_available(self) -> bool:
        """Check if Firestore is configured and accessible."""
        try:
            self._collection().limit(1).get()
            return True
        except Exception:
            return False

    def prepare(self, *, required: bool = False) -> bool:
        """Report whether delivery history is active."""
        try:
            self._collection().limit(1).get()
        except Exception as exc:
            if required:
                raise DeliveryHistoryError(f"Firestore delivery history is not available: {exc}") from exc
            return False

        return True

    def init_schema(self) -> None:
        """Firestore does not require schema initialization."""
        self.prepare(required=True)

    def get_sent_urls(self, urls: Sequence[str]) -> set[str]:
        """Check which URLs have already been sent.

        Returns a set of delivery-history keys that are already in Firestore.
        Returns empty set if Firestore is not configured.
        """
        if not urls:
            return set()

        lookup_keys = sorted({key for url in urls for key in article_lookup_keys(url)})
        collection = self._collection()
        refs = [collection.document(_history_document_id(key)) for key in lookup_keys]
        snapshots = self._get_client().get_all(refs)
        sent: set[str] = set()
        for snapshot in snapshots:
            data = snapshot.to_dict() if snapshot.exists else None
            if data and data.get("history_key"):
                sent.add(data["history_key"])
        return sent

    def filter_unsent(self, articles: Sequence) -> list:
        """Filter out articles that have already been sent.

        Returns a new list containing only unsent articles.
        If Firestore is not configured, returns all articles (no filtering).
        """
        if not articles:
            return []

        if not self.is_available:
            return list(articles)

        urls = [a.url for a in articles]
        sent = self.get_sent_urls(urls)
        return [a for a in articles if not any(key in sent for key in article_lookup_keys(a.url))]

    def record_sent(self, articles: Sequence) -> DeliveryRecordResult:
        """Record that articles were sent successfully.

        Inserts delivery-history keys into Firestore.
        Returns insert counts for logging.
        """
        if not articles:
            return DeliveryRecordResult(attempted=0, inserted=0, skipped_existing=0)

        if not self.is_available:
            return DeliveryRecordResult(attempted=len(articles), inserted=0, skipped_existing=len(articles))

        collection = self._collection()
        inserted = 0
        for article in articles:
            history_key = article_history_key(article.url)
            ref = collection.document(_history_document_id(history_key))
            if ref.get().exists:
                continue
            ref.set(
                {
                    "history_key": history_key,
                    "url": article.url,
                    "title": article.title[:500],
                    "sent_at": firestore.SERVER_TIMESTAMP,
                }
            )
            inserted += 1
        return DeliveryRecordResult(
            attempted=len(articles),
            inserted=inserted,
            skipped_existing=len(articles) - inserted,
        )

    def close(self) -> None:
        """Release the cached Firestore client reference."""
        self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
