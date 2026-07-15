from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
from types import SimpleNamespace

from medium_ai_reader.cron import DigestConfig, DigestResult, run_digest, send_digest_email
from medium_ai_reader import delivery_history as delivery_history_module
from medium_ai_reader.delivery_history import DeliveryHistory, DeliveryRecordResult, article_history_key, article_lookup_keys
from medium_ai_reader.models import Article


class FakeDeliveryHistory:
    def __init__(self, sent_urls=()):
        self.dsn = "postgresql://example"
        self.sent_urls = set(sent_urls)
        self.recorded = []
        self.closed = False

    def prepare(self, *, required=False):
        return True

    def filter_unsent(self, articles):
        return [article for article in articles if article.url not in self.sent_urls]

    def record_sent(self, articles):
        self.recorded.extend(articles)
        return DeliveryRecordResult(attempted=len(articles), inserted=len(articles), skipped_existing=0)

    def close(self):
        self.closed = True


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
        "require_delivery_history": True,
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
        delivery_history=FakeDeliveryHistory(),
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
        delivery_history=FakeDeliveryHistory(),
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
        delivery_history=FakeDeliveryHistory(),
    )

    assert [article.title for article in result.articles] == ["Popular Python AI agents article"]
    assert "Popular Python AI agents article" in sent["text_body"]
    assert "Quiet article" not in sent["text_body"]


def test_run_digest_filters_already_sent_articles_before_sending():
    sent = {}
    history = FakeDeliveryHistory(sent_urls={"https://medium.com/example/old-abc123def456"})

    def fake_fetcher(feed_urls, max_items_per_feed, include_metrics):
        return [
            Article(
                title="Already sent",
                url="https://medium.com/example/old-abc123def456",
                source_feed=feed_urls[0],
                summary="Python AI agents",
            ),
            Article(
                title="Fresh article",
                url="https://medium.com/example/fresh-abc123def457",
                source_feed=feed_urls[0],
                summary="Python AI agents",
            ),
        ], []

    def fake_sender(config, subject, text_body, html_body):
        sent["subject"] = subject
        sent["text_body"] = text_body

    result = run_digest(
        make_config(top_k=1),
        fetcher=fake_fetcher,
        email_sender=fake_sender,
        now=datetime(2026, 7, 7, 13, 0, tzinfo=timezone.utc),
        delivery_history=history,
    )

    assert [article.title for article in result.articles] == ["Fresh article"]
    assert sent["subject"] == "Medium AI Daily Digest: 1 article for 2026-07-07"
    assert "Fresh article" in sent["text_body"]
    assert "Already sent" not in sent["text_body"]
    assert [article.title for article in history.recorded] == ["Fresh article"]


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


def test_article_history_key_uses_medium_post_id_across_url_variants():
    first = "https://medium.com/@writer/same-story-abc123def456?source=rss"
    second = "https://publication.example.com/same-story-abc123def456"

    assert article_history_key(first) == "medium-post:abc123def456"
    assert article_history_key(second) == "medium-post:abc123def456"
    assert article_lookup_keys(first)[1] == "medium.com/@writer/same-story-abc123def456"


def test_delivery_history_records_and_filters_with_firestore(monkeypatch):
    store = {}

    class FakeSnapshot:
        def __init__(self, data):
            self._data = data

        @property
        def exists(self):
            return self._data is not None

        def to_dict(self):
            return dict(self._data) if self._data is not None else None

    class FakeRef:
        def __init__(self, doc_id):
            self.doc_id = doc_id

        def get(self):
            return FakeSnapshot(store.get(self.doc_id))

        def set(self, data):
            store[self.doc_id] = dict(data)

    class FakeCollection:
        def document(self, doc_id):
            return FakeRef(doc_id)

        def limit(self, count):
            return self

        def get(self):
            return []

    class FakeClient:
        def collection(self, name):
            return FakeCollection()

        def get_all(self, refs):
            return [ref.get() for ref in refs]

    class FakeFirebaseAdmin:
        def __init__(self):
            self._apps = []

        def initialize_app(self):
            self._apps.append(object())

    fake_admin = FakeFirebaseAdmin()
    fake_firestore = SimpleNamespace(client=lambda: FakeClient(), SERVER_TIMESTAMP="SERVER_TIMESTAMP")
    monkeypatch.setattr(delivery_history_module, "firebase_admin", fake_admin)
    monkeypatch.setattr(delivery_history_module, "firestore", fake_firestore)

    history = DeliveryHistory()
    old_article = Article(
        title="Already sent",
        url="https://medium.com/example/old-abc123def456",
        source_feed="https://medium.com/feed/tag/python",
    )
    new_article = Article(
        title="Fresh article",
        url="https://medium.com/example/fresh-abc123def457",
        source_feed="https://medium.com/feed/tag/python",
    )

    result = history.record_sent([old_article])

    assert result == DeliveryRecordResult(attempted=1, inserted=1, skipped_existing=0)
    assert [article.title for article in history.filter_unsent([old_article, new_article])] == ["Fresh article"]
    assert history.record_sent([old_article]) == DeliveryRecordResult(
        attempted=1,
        inserted=0,
        skipped_existing=1,
    )
