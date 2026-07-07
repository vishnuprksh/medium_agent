from medium_ai_reader.medium_sources import build_feed_urls, source_to_feed


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
