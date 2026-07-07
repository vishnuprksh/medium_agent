#!/usr/bin/env python3
"""Initialize the delivery history database schema.

Run this once after creating the PostgreSQL database on Render:
    python init_db.py

This will create the sent_articles table if it doesn't exist.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add src to path so we can import medium_ai_reader
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from medium_ai_reader.delivery_history import DeliveryHistory


def main() -> None:
    dsn = os.getenv("DIGEST_DB_DSN")
    if not dsn:
        print("ERROR: DIGEST_DB_DSN environment variable not set.")
        print("Set it to your PostgreSQL connection string, e.g.:")
        print("  postgresql://user:pass@host:5432/dbname")
        sys.exit(1)

    print(f"Initializing database schema...")
    try:
        history = DeliveryHistory(dsn)
        history.init_schema()
        print("✓ Database schema initialized successfully!")
        print("\nThe following table was created (if it didn't exist):")
        print("  - sent_articles (id, normalized_url, title, sent_at)")
    except Exception as e:
        print(f"ERROR: Failed to initialize database: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
