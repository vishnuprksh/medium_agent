from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from firebase_functions import options, scheduler_fn

from src.medium_ai_reader.cron import run_digest

logger = logging.getLogger(__name__)

SECRET_ENV_VARS = [
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "OPENROUTER_API_KEY",
]


@scheduler_fn.on_schedule(
    schedule="0 13 * * *",
    timezone=ZoneInfo("Etc/UTC"),
    region=options.SupportedRegion.US_CENTRAL1,
    memory=options.MemoryOption.GB_1,
    timeout_sec=540,
    max_instances=1,
    retry_count=0,
    secrets=SECRET_ENV_VARS,
)
def daily_digest(event: scheduler_fn.ScheduledEvent) -> None:
    """Run the Medium digest from Firebase Cloud Scheduler."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = run_digest()
    logger.info(
        "Firebase scheduled digest finished: job=%s schedule_time=%s selected_articles=%s",
        event.job_name,
        event.schedule_time,
        len(result.articles),
    )
