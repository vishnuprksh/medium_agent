from __future__ import annotations

import argparse
import html
import logging
import os
import smtplib
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Callable, Mapping, Sequence

from .agents import CuratorAgent, DiscoveryPlan, PreferenceAgent, RankerAgent
from .delivery_history import DeliveryHistory, DeliveryHistoryError
from .medium_sources import fetch_articles, split_csv
from .models import Article

logger = logging.getLogger(__name__)

DEFAULT_RECIPIENT = "vishnucheppanam@gmail.com"
DEFAULT_INTENT = (
    "Advanced mathematics articles covering linear algebra, calculus, probability theory, "
    "statistics, optimization, information theory, and mathematical foundations "
    "for machine learning, artificial intelligence, and data science."
)
DEFAULT_TAGS = "mathematics, linear-algebra, calculus, probability, statistics, optimization, machine-learning, artificial-intelligence, data-science, information-theory"


class ConfigError(ValueError):
    """Raised when the cron job is missing required runtime configuration."""


@dataclass(frozen=True)
class DigestConfig:
    intent: str = DEFAULT_INTENT
    tag_text: str = DEFAULT_TAGS
    source_text: str = ""
    max_feeds: int = 10
    max_items_per_feed: int = 20
    top_k: int = 8
    include_metrics: bool = False
    min_claps: int = 0
    min_responses: int = 0
    use_openrouter: bool = True
    recipients: tuple[str, ...] = (DEFAULT_RECIPIENT,)
    subject_prefix: str = "Medium AI Daily Digest"
    dry_run: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    require_delivery_history: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DigestConfig":
        env = os.environ if env is None else env
        min_claps = _env_int(env, "DIGEST_MIN_CLAPS", 0, minimum=0)
        min_responses = _env_int(env, "DIGEST_MIN_RESPONSES", 0, minimum=0)
        include_metrics = _env_bool(env, "DIGEST_INCLUDE_METRICS", False)

        return cls(
            intent=env.get("DIGEST_INTENT", DEFAULT_INTENT).strip() or DEFAULT_INTENT,
            tag_text=env.get("DIGEST_TAGS", DEFAULT_TAGS),
            source_text=env.get("DIGEST_SOURCES", ""),
            max_feeds=_env_int(env, "DIGEST_MAX_FEEDS", 10, minimum=1),
            max_items_per_feed=_env_int(env, "DIGEST_MAX_ITEMS_PER_FEED", 20, minimum=1),
            top_k=_env_int(env, "DIGEST_TOP_K", 8, minimum=1),
            include_metrics=include_metrics or min_claps > 0 or min_responses > 0,
            min_claps=min_claps,
            min_responses=min_responses,
            use_openrouter=_env_bool(env, "DIGEST_USE_OPENROUTER", True),
            recipients=tuple(split_csv(env.get("DIGEST_RECIPIENTS", DEFAULT_RECIPIENT))),
            subject_prefix=env.get("DIGEST_SUBJECT_PREFIX", "Medium AI Daily Digest").strip()
            or "Medium AI Daily Digest",
            dry_run=_env_bool(env, "DIGEST_DRY_RUN", False),
            smtp_host=env.get("SMTP_HOST", "").strip(),
            smtp_port=_env_int(env, "SMTP_PORT", 587, minimum=1),
            smtp_username=env.get("SMTP_USERNAME", "").strip(),
            smtp_password=env.get("SMTP_PASSWORD", ""),
            smtp_from=env.get("SMTP_FROM", "").strip(),
            smtp_use_tls=_env_bool(env, "SMTP_USE_TLS", True),
            require_delivery_history=_env_bool(env, "DIGEST_REQUIRE_DELIVERY_HISTORY", True),
        )

    @property
    def sender(self) -> str:
        return self.smtp_from or self.smtp_username

    def validate(self) -> None:
        if not self.recipients:
            raise ConfigError("Set DIGEST_RECIPIENTS to at least one email address.")
        if self.dry_run:
            return
        if not self.smtp_host:
            raise ConfigError("Set SMTP_HOST, or run with DIGEST_DRY_RUN=true.")
        if not self.sender:
            raise ConfigError("Set SMTP_FROM or SMTP_USERNAME.")
        if self.smtp_username and not self.smtp_password:
            raise ConfigError("Set SMTP_PASSWORD for SMTP_USERNAME.")


@dataclass(frozen=True)
class DigestResult:
    generated_at: datetime
    plan: DiscoveryPlan
    articles: tuple[Article, ...]
    errors: tuple[str, ...]
    subject: str
    text_body: str
    html_body: str


FetchArticles = Callable[..., tuple[list[Article], list[str]]]
SendEmail = Callable[[DigestConfig, str, str, str], None]
ArticleFilter = Callable[[Sequence[Article]], list[Article]]


def run_digest(
    config: DigestConfig | None = None,
    *,
    fetcher: FetchArticles = fetch_articles,
    email_sender: SendEmail | None = None,
    now: datetime | None = None,
    delivery_history: DeliveryHistory | None = None,
) -> DigestResult:
    config = config or DigestConfig.from_env()
    config.validate()
    email_sender = email_sender or send_digest_email
    generated_at = now or datetime.now(timezone.utc)

    history = delivery_history or DeliveryHistory()
    try:
        logger.info(
            "Digest run starting: recipients=%s top_k=%s max_feeds=%s dry_run=%s",
            len(config.recipients),
            config.top_k,
            config.max_feeds,
            config.dry_run,
        )
        history_active = history.prepare(required=config.require_delivery_history and not config.dry_run)
        logger.info(
            "Delivery history status: active=%s required=%s dsn_configured=%s",
            history_active,
            config.require_delivery_history and not config.dry_run,
            bool(getattr(history, "dsn", None)),
        )

        filter_stats = {"candidates": 0, "already_sent": 0, "available_for_ranking": 0}

        def filter_unsent_candidates(candidates: Sequence[Article]) -> list[Article]:
            filtered = history.filter_unsent(candidates)
            filter_stats["candidates"] = len(candidates)
            filter_stats["already_sent"] = len(candidates) - len(filtered)
            filter_stats["available_for_ranking"] = len(filtered)
            return filtered

        plan, articles, errors = discover_articles(
            config,
            fetcher=fetcher,
            article_filter=filter_unsent_candidates,
        )
        if filter_stats["candidates"]:
            logger.info(
                "Delivery history filter complete: candidates=%s already_sent=%s available_for_ranking=%s",
                filter_stats["candidates"],
                filter_stats["already_sent"],
                filter_stats["available_for_ranking"],
            )
        else:
            logger.info("Delivery history filter skipped: no candidate articles")
        logger.info(
            "Article discovery complete: feeds=%s selected=%s warnings=%s",
            len(plan.feed_urls),
            len(articles),
            len(errors),
        )

        subject = build_subject(config, generated_at, len(articles))
        text_body = render_text_digest(config, generated_at, plan, articles, errors)
        html_body = render_html_digest(config, generated_at, plan, articles, errors)
        result = DigestResult(
            generated_at=generated_at,
            plan=plan,
            articles=tuple(articles),
            errors=tuple(errors),
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )

        if config.dry_run:
            print(result.text_body)
            # In dry-run mode, still show what would be recorded
            if articles:
                print(f"\n[DRY RUN] Would record {len(articles)} articles as sent.")
            logger.info("Dry run complete: selected_articles=%s warnings=%s", len(articles), len(errors))
        else:
            email_sender(config, result.subject, result.text_body, result.html_body)
            logger.info(
                "Email sent: recipients=%s selected_articles=%s subject=%s",
                len(config.recipients),
                len(articles),
                result.subject,
            )
            # Record successfully sent articles
            if articles:
                record_result = history.record_sent(articles)
                logger.info(
                    "Delivery history recorded: attempted=%s inserted=%s already_present=%s",
                    record_result.attempted,
                    record_result.inserted,
                    record_result.skipped_existing,
                )
            else:
                logger.info("Delivery history record skipped: no selected articles")

        logger.info("Digest run finished successfully: selected_articles=%s warnings=%s", len(articles), len(errors))

        return result
    finally:
        history.close()


def discover_articles(
    config: DigestConfig,
    *,
    fetcher: FetchArticles = fetch_articles,
    article_filter: ArticleFilter | None = None,
) -> tuple[DiscoveryPlan, list[Article], list[str]]:
    preference_agent = PreferenceAgent()
    plan = preference_agent.plan(
        intent=config.intent,
        tag_text=config.tag_text,
        source_text=config.source_text,
        max_feeds=config.max_feeds,
    )
    errors: list[str] = []

    if not plan.feed_urls:
        return plan, [], ["No Medium feeds could be built from DIGEST_TAGS or DIGEST_SOURCES."]

    articles, fetch_errors = fetcher(
        plan.feed_urls,
        max_items_per_feed=config.max_items_per_feed,
        include_metrics=config.include_metrics,
    )
    errors.extend(fetch_errors)

    if config.min_claps > 0 or config.min_responses > 0:
        before_count = len(articles)
        articles = filter_by_popularity(articles, config.min_claps, config.min_responses)
        if before_count and not articles:
            errors.append("Popularity filters removed all fetched articles.")

    if article_filter is not None and articles:
        before_count = len(articles)
        articles = article_filter(articles)
        if len(articles) < before_count:
            errors.append(f"Filtered out {before_count - len(articles)} already-sent articles.")

    if not articles:
        return plan, [], errors

    ranked = RankerAgent().rank(
        intent=config.intent,
        articles=articles,
        use_openai=config.use_openrouter,
        top_k=config.top_k,
    )
    curated = CuratorAgent().annotate(
        intent=config.intent,
        articles=ranked,
        use_openai=config.use_openrouter,
    )
    return plan, curated, errors


def send_digest_email(config: DigestConfig, subject: str, text_body: str, html_body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
        if config.smtp_use_tls:
            smtp.starttls()
        if config.smtp_username:
            smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)


def build_subject(config: DigestConfig, generated_at: datetime, article_count: int) -> str:
    date_text = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
    noun = "article" if article_count == 1 else "articles"
    return f"{config.subject_prefix}: {article_count} {noun} for {date_text}"


def render_text_digest(
    config: DigestConfig,
    generated_at: datetime,
    plan: DiscoveryPlan,
    articles: Sequence[Article],
    errors: Sequence[str],
) -> str:
    lines = [
        config.subject_prefix,
        f"Generated: {generated_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Intent: {config.intent}",
        "",
    ]

    if articles:
        lines.append(f"Top {len(articles)} Medium articles")
        lines.append("")
        for idx, article in enumerate(articles, start=1):
            lines.extend(_plain_article_lines(idx, article))
            lines.append("")
    else:
        lines.extend(
            [
                "No articles were found for today's digest.",
                "Try broadening DIGEST_TAGS or DIGEST_SOURCES if this keeps happening.",
                "",
            ]
        )

    lines.append("Feeds checked:")
    lines.extend(f"- {url}" for url in plan.feed_urls)

    if errors:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {error}" for error in errors[:10])
        if len(errors) > 10:
            lines.append(f"- ...and {len(errors) - 10} more")

    lines.extend(["", "Source: public Medium RSS feeds."])
    return "\n".join(lines).strip() + "\n"


def render_html_digest(
    config: DigestConfig,
    generated_at: datetime,
    plan: DiscoveryPlan,
    articles: Sequence[Article],
    errors: Sequence[str],
) -> str:
    article_html = "".join(_html_article(idx, article) for idx, article in enumerate(articles, start=1))
    if not article_html:
        article_html = (
            "<p>No articles were found for today's digest. Try broadening "
            "<code>DIGEST_TAGS</code> or <code>DIGEST_SOURCES</code> if this keeps happening.</p>"
        )

    feed_items = "".join(f"<li>{html.escape(url)}</li>" for url in plan.feed_urls)
    warning_block = ""
    if errors:
        warning_items = "".join(f"<li>{html.escape(error)}</li>" for error in errors[:10])
        if len(errors) > 10:
            warning_items += f"<li>...and {len(errors) - 10} more</li>"
        warning_block = f"<h2>Warnings</h2><ul>{warning_items}</ul>"

    return f"""<!doctype html>
<html>
  <body style="font-family: Arial, sans-serif; line-height: 1.55; color: #1f2937;">
    <h1 style="font-size: 24px; margin-bottom: 4px;">{html.escape(config.subject_prefix)}</h1>
    <p style="margin-top: 0; color: #4b5563;">
      Generated {html.escape(generated_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))}
    </p>
    <p><strong>Intent:</strong> {html.escape(config.intent)}</p>
    <h2 style="font-size: 18px;">Top Medium Articles</h2>
    {article_html}
    <h2 style="font-size: 18px;">Feeds Checked</h2>
    <ul>{feed_items}</ul>
    {warning_block}
    <p style="color: #6b7280; font-size: 13px;">Source: public Medium RSS feeds.</p>
  </body>
</html>
"""


def filter_by_popularity(articles: Sequence[Article], min_claps: int, min_responses: int) -> list[Article]:
    return [
        article
        for article in articles
        if _passes_minimum(article.clap_count, min_claps)
        and _passes_minimum(article.response_count, min_responses)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    _load_dotenv()
    _configure_logging()
    parser = argparse.ArgumentParser(description="Send the Medium AI Reader daily email digest.")
    parser.add_argument("--dry-run", action="store_true", help="Print the digest instead of sending email.")
    args = parser.parse_args(argv)

    try:
        config = DigestConfig.from_env()
        if args.dry_run:
            config = replace(config, dry_run=True)
        result = run_digest(config)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2
    except DeliveryHistoryError as exc:
        logger.error("Delivery history error: %s", exc)
        return 2
    except Exception:
        logger.exception("Digest run failed unexpectedly")
        return 1

    if not config.dry_run:
        logger.info("Sent %s articles to %s.", len(result.articles), ", ".join(config.recipients))
    return 0


def _plain_article_lines(idx: int, article: Article) -> list[str]:
    lines = [
        f"{idx}. {article.title}",
        f"   URL: {article.url}",
        f"   Score: {article.score:.2f}",
    ]
    if article.author:
        lines.append(f"   Author: {article.author}")
    if article.published:
        lines.append(f"   Published: {article.published}")
    if article.ai_note:
        lines.append(f"   Why it fits: {article.ai_note}")
    metrics = _metric_parts(article)
    if metrics:
        lines.append("   Metrics: " + ", ".join(metrics))
    if article.tags:
        lines.append("   Tags: " + ", ".join(article.tags[:6]))
    return lines


def _html_article(idx: int, article: Article) -> str:
    meta = []
    if article.author:
        meta.append(html.escape(article.author))
    if article.published:
        meta.append(html.escape(article.published))
    meta.extend(html.escape(metric) for metric in _metric_parts(article))
    meta_text = " | ".join(meta)
    tags = ", ".join(html.escape(tag) for tag in article.tags[:6])
    tag_html = f"<p><strong>Tags:</strong> {tags}</p>" if tags else ""
    note_html = f"<p>{html.escape(article.ai_note)}</p>" if article.ai_note else ""

    return f"""
    <div style="border-top: 1px solid #e5e7eb; padding: 16px 0;">
      <h3 style="font-size: 16px; margin: 0 0 4px;">
        {idx}. <a href="{html.escape(article.url)}">{html.escape(article.title)}</a>
      </h3>
      <p style="margin: 0 0 8px; color: #4b5563;">Score: {article.score:.2f}{' | ' + meta_text if meta_text else ''}</p>
      {note_html}
      {tag_html}
    </div>
"""


def _metric_parts(article: Article) -> list[str]:
    parts = []
    if article.clap_count is not None:
        parts.append(f"{article.clap_count:,} claps")
    if article.response_count is not None:
        parts.append(f"{article.response_count:,} responses")
    if article.reading_time_minutes is not None:
        parts.append(f"{article.reading_time_minutes:.1f} min read")
    return parts


def _passes_minimum(value: int | None, minimum: int) -> bool:
    return minimum <= 0 or (value is not None and value >= minimum)


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false.")


def _env_int(env: Mapping[str, str], name: str, default: int, *, minimum: int | None = None) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}.")
    return value


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


if __name__ == "__main__":
    raise SystemExit(main())
