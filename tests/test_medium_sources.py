from medium_ai_reader.medium_sources import (
    apply_article_metrics,
    build_feed_urls,
    extract_article_metrics,
    medium_post_id,
    source_to_feed,
)
from medium_ai_reader.models import Article


def test_profile_to_feed():
    assert source_to_feed("@StartupGrind") == "https://medium.com/feed/@startupgrind"


def test_publication_to_feed():
    assert source_to_feed("towards-data-science") == "https://medium.com/feed/towards-data-science"


def test_medium_url_to_feed():
    assert source_to_feed("https://medium.com/better-programming") == "https://medium.com/feed/better-programming"


def test_custom_domain_to_feed():
    assert source_to_feed("https://example-publication.com") == "https://example-publication.com/feed"


def test_build_feed_urls_dedupes():
    urls = build_feed_urls(["AI", "ai"], ["@Someone"], max_feeds=5)
    assert urls[0] == "https://medium.com/feed/tag/ai"
    assert urls.count("https://medium.com/feed/tag/ai") == 1
    assert urls[-1] == "https://medium.com/feed/@someone"


def test_medium_post_id_from_article_url():
    assert (
        medium_post_id("https://medium.com/@writer/example-title-abc123def456?source=rss")
        == "abc123def456"
    )


def test_extract_article_metrics_from_apollo_state():
    html = """
    <script>
    window.__APOLLO_STATE__ = {
      "Post:abc123def456": {
        "__typename": "Post",
        "clapCount": 321,
        "readingTime": 4.25,
        "postResponses": {"__typename": "PostResponses", "count": 7}
      }
    };
    </script>
    """

    metrics = extract_article_metrics(
        html,
        article_url="https://medium.com/@writer/example-title-abc123def456",
    )

    assert metrics == {
        "clap_count": 321,
        "response_count": 7,
        "reading_time_minutes": 4.25,
    }


def test_extract_article_metrics_falls_back_to_embedded_fields():
    html = """
    <script>
    {"postResponses":{"__typename":"PostResponses","count":3},"clapCount":19,"readingTime":2.5}
    </script>
    """

    metrics = extract_article_metrics(html)

    assert metrics["clap_count"] == 19
    assert metrics["response_count"] == 3
    assert metrics["reading_time_minutes"] == 2.5


def test_apply_article_metrics_sets_nullable_article_fields():
    article = Article(title="Title", url="https://example.com", source_feed="feed")

    apply_article_metrics(
        article,
        {"clap_count": 12, "response_count": 2, "reading_time_minutes": 6.75},
    )

    assert article.clap_count == 12
    assert article.response_count == 2
    assert article.reading_time_minutes == 6.75
