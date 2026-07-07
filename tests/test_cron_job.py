from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage

from medium_ai_reader.cron import DigestConfig, DigestResult, run_digest, send_digest_email
from medium_ai_reader.models import Article


def make_config(**overrides) -> DigestConfig:
    values = {
        "intent": "Practical Python AI agents",
        "tag_text": "python, ai-agents",
        "source_text": "",
        "max_feeds": 4,
        "max_items_per_feed": 5,
        "top_k": 2,
        "include_metrics": False,
        "min_claps": 0,
        "min_responses": 0,
        "use_openrouter": False,
        "recipients": ("vishnucheppanam@gmail.com",),
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "digest@example.com",
        "smtp_password": "secret",
        "smtp_from": "digest@example.com",
    }
    values.update(overrides)
    return DigestConfig(**values)


def test_config_from_env_defaults_to_requested_recipient_and_metric_filtering():
    config = DigestConfig.from_env({"DIGEST_MIN_CLAPS": "25"})

    assert config.recipients == ("vishnucheppanam@gmail.com",)
    assert config.include_metrics is True
    assert config.min_claps == 25


def test_run_digest_finds_articles_and_sends_email():
    sent = {}

    def fake_fetcher(feed_urls, max_items_per_feed, include_metrics):
        assert feed_urls[0] == "https://medium.com/feed/tag/python"
        assert max_items_per_feed == 5
        assert include_metrics is False
        return [
            Article(
                title="Building Python AI agents that work",
                url="https://medium.com/example/agents-abc123def456",
                source_feed=feed_urls[0],
                author="A Writer",
                published="Tue, 07 Jul 2026 09:00:00 GMT",
                summary="A practical guide to AI agents with Python.",
                tags=["python", "ai-agents"],
            ),
            Article(
                title="A broad product essay",
                url="https://medium.com/example/product-abc123def457",
                source_feed=feed_urls[0],
                summary="Product thinking for teams.",
                tags=["product"],
            ),
        ], []

    def fake_sender(config, subject, text_body, html_body):
        sent["recipients"] = config.recipients
        sent["subject"] = subject
        sent["text_body"] = text_body
        sent["html_body"] = html_body

    result = run_digest(
        make_config(),
        fetcher=fake_fetcher,
        email_sender=fake_sender,
        now=datetime(2026, 7, 7, 13, 0, tzinfo=timezone.utc),
    )

    assert isinstance(result, DigestResult)
    assert sent["recipients"] == ("vishnucheppanam@gmail.com",)
    assert sent["subject"] == "Medium AI Daily Digest: 2 articles for 2026-07-07"
    assert "Building Python AI agents that work" in sent["text_body"]
    assert "https://medium.com/example/agents-abc123def456" in sent["html_body"]
    assert len(result.articles) == 2


def test_run_digest_sends_no_articles_email_when_fetch_is_empty():
    sent = {}

    def fake_fetcher(feed_urls, max_items_per_feed, include_metrics):
        return [], ["temporary feed failure"]

    def fake_sender(config, subject, text_body, html_body):
        sent["subject"] = subject
        sent["text_body"] = text_body
        sent["html_body"] = html_body

    result = run_digest(
        make_config(),
        fetcher=fake_fetcher,
        email_sender=fake_sender,
        now=datetime(2026, 7, 7, 13, 0, tzinfo=timezone.utc),
    )

    assert result.articles == ()
    assert sent["subject"] == "Medium AI Daily Digest: 0 articles for 2026-07-07"
    assert "No articles were found" in sent["text_body"]
    assert "temporary feed failure" in sent["html_body"]


def test_run_digest_filters_by_popularity_before_sending():
    sent = {}

    def fake_fetcher(feed_urls, max_items_per_feed, include_metrics):
        assert include_metrics is True
        return [
            Article(
                title="Quiet article",
                url="https://medium.com/example/quiet-abc123def456",
                source_feed=feed_urls[0],
                summary="Python AI agents",
                clap_count=3,
                response_count=0,
            ),
            Article(
                title="Popular Python AI agents article",
                url="https://medium.com/example/popular-abc123def457",
                source_feed=feed_urls[0],
                summary="Python AI agents",
                clap_count=150,
                response_count=8,
            ),
        ], []

    def fake_sender(config, subject, text_body, html_body):
        sent["text_body"] = text_body

    result = run_digest(
        make_config(include_metrics=True, min_claps=100, min_responses=1),
        fetcher=fake_fetcher,
        email_sender=fake_sender,
    )

    assert [article.title for article in result.articles] == ["Popular Python AI agents article"]
    assert "Popular Python AI agents article" in sent["text_body"]
    assert "Quiet article" not in sent["text_body"]


def test_send_digest_email_uses_configured_smtp(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message: EmailMessage):
            plain_body = message.get_body(("plain",)).get_content()
            calls.append(("send", message["To"], message["Subject"], plain_body))

    monkeypatch.setattr("medium_ai_reader.cron.smtplib.SMTP", FakeSMTP)

    send_digest_email(
        make_config(),
        "Digest subject",
        "Plain body",
        "<p>HTML body</p>",
    )

    assert calls[0] == ("connect", "smtp.example.com", 587, 30)
    assert ("starttls",) in calls
    assert ("login", "digest@example.com", "secret") in calls
    assert calls[-1] == ("send", "vishnucheppanam@gmail.com", "Digest subject", "Plain body\n")
