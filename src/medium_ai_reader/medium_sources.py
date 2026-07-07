from __future__ import annotations

import html
import json
import os
import re
import time
from typing import Any, Iterable, List, Sequence
from urllib.parse import urlparse, urlunparse


from .models import Article

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "how",
    "i", "in", "into", "is", "it", "me", "my", "not", "of", "on", "or", "read",
    "should", "that", "the", "this", "to", "want", "what", "when", "where", "which",
    "with", "you", "your", "about", "have", "has", "using", "use", "learn", "guide",
    "article", "articles", "medium", "website", "find", "best", "good", "exactly",
}

TAG_HINTS = {
    "ai": ["artificial-intelligence", "ai"],
    "agent": ["ai-agents", "artificial-intelligence"],
    "agents": ["ai-agents", "artificial-intelligence"],
    "llm": ["llm", "large-language-models", "artificial-intelligence"],
    "machine": ["machine-learning"],
    "learning": ["machine-learning"],
    "python": ["python", "programming"],
    "startup": ["startup", "startups"],
    "product": ["product-management"],
    "data": ["data-science"],
    "software": ["software-development", "programming"],
    "programming": ["programming", "software-development"],
    "engineering": ["software-engineering", "software-development"],
    "design": ["ux-design", "design"],
    "career": ["career-advice"],
}


def slugify_tag(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("_", "-")
    value = re.sub(r"[\s/]+", "-", value)
    value = re.sub(r"[^a-z0-9@.-]+", "", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def split_csv(value: str) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]


def keyword_terms(text: str, max_terms: int = 12) -> List[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+-]{2,}", text.lower())
    result: List[str] = []
    for word in words:
        if word in STOPWORDS:
            continue
        if word not in result:
            result.append(word)
        if len(result) >= max_terms:
            break
    return result


def infer_tags_from_prompt(prompt: str, max_tags: int = 8) -> List[str]:
    tags: List[str] = []
    terms = keyword_terms(prompt, max_terms=20)
    joined = " ".join(terms)

    if "machine learning" in prompt.lower():
        tags.append("machine-learning")
    if "artificial intelligence" in prompt.lower():
        tags.append("artificial-intelligence")
    if "software development" in prompt.lower():
        tags.append("software-development")
    if "data science" in prompt.lower():
        tags.append("data-science")

    for term in terms:
        for hinted in TAG_HINTS.get(term, []):
            if hinted not in tags:
                tags.append(hinted)
        slug = slugify_tag(term)
        if slug and slug not in tags:
            tags.append(slug)

    # Add one high-signal phrase tag when the prompt contains obvious adjacent terms.
    phrase_candidates = re.findall(r"[a-zA-Z][a-zA-Z0-9+-]+\s+[a-zA-Z][a-zA-Z0-9+-]+", joined)
    for phrase in phrase_candidates[:3]:
        slug = slugify_tag(phrase)
        if slug and slug not in tags:
            tags.append(slug)

    return tags[:max_tags]


def source_to_feed(source: str) -> str | None:
    source = source.strip()
    if not source:
        return None

    if source.startswith("@"):
        return f"https://medium.com/feed/{slugify_tag(source)}"

    parsed = urlparse(source if "://" in source else f"https://medium.com/{source.strip('/')}")
    if not parsed.netloc:
        return None

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if netloc in {"medium.com", "www.medium.com"}:
        if path.startswith("feed/"):
            feed_path = path
        elif path:
            feed_path = f"feed/{path}"
        else:
            return None
        return urlunparse(("https", "medium.com", f"/{feed_path}", "", "", ""))

    # Custom-domain Medium publications expose /feed on the same domain.
    feed_path = path.rstrip("/")
    if feed_path.endswith("feed"):
        final_path = f"/{feed_path}"
    else:
        final_path = f"/{feed_path}/feed" if feed_path else "/feed"
    return urlunparse((scheme, netloc, final_path, "", "", ""))


def build_feed_urls(tags: Sequence[str], sources: Sequence[str], max_feeds: int = 12) -> List[str]:
    urls: List[str] = []

    for raw_tag in tags:
        tag = slugify_tag(raw_tag)
        if not tag:
            continue
        urls.append(f"https://medium.com/feed/tag/{tag}")

    for source in sources:
        feed = source_to_feed(source)
        if feed:
            urls.append(feed)

    deduped: List[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
        if len(deduped) >= max_feeds:
            break
    return deduped


def html_to_text(value: str) -> str:
    from bs4 import BeautifulSoup

    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def entry_text(entry) -> str:
    pieces: List[str] = []
    if getattr(entry, "summary", None):
        pieces.append(html_to_text(entry.summary))
    for content_part in getattr(entry, "content", []) or []:
        value = content_part.get("value", "") if isinstance(content_part, dict) else ""
        if value:
            pieces.append(html_to_text(value))
    return "\n".join(piece for piece in pieces if piece).strip()


def medium_post_id(url: str) -> str | None:
    path = urlparse(url).path
    match = re.search(r"([0-9a-f]{12})(?:/)?$", path, flags=re.IGNORECASE)
    return match.group(1).lower() if match else None


def _extract_apollo_state(page_html: str) -> dict[str, Any] | None:
    marker = "window.__APOLLO_STATE__ = "
    start = page_html.find(marker)
    if start < 0:
        return None

    decoder = json.JSONDecoder()
    try:
        state, _ = decoder.raw_decode(page_html[start + len(marker) :].lstrip())
    except json.JSONDecodeError:
        return None

    return state if isinstance(state, dict) else None


def _ref_value(state: dict[str, Any], value: Any) -> Any:
    if isinstance(value, dict) and isinstance(value.get("__ref"), str):
        return state.get(value["__ref"])
    return value


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_article_metrics(page_html: str, article_url: str = "") -> dict[str, int | float | None]:
    """Extract public Medium metrics from an article page when they are embedded."""
    page_html = html.unescape(page_html)
    metrics: dict[str, int | float | None] = {
        "clap_count": None,
        "response_count": None,
        "reading_time_minutes": None,
    }

    state = _extract_apollo_state(page_html)
    if state:
        post = None
        post_id = medium_post_id(article_url)
        if post_id:
            post = state.get(f"Post:{post_id}")
        if not isinstance(post, dict):
            for value in state.values():
                if isinstance(value, dict) and value.get("__typename") == "Post" and "clapCount" in value:
                    post = value
                    break

        if isinstance(post, dict):
            metrics["clap_count"] = _int_or_none(post.get("clapCount"))
            metrics["reading_time_minutes"] = _float_or_none(post.get("readingTime"))
            responses = _ref_value(state, post.get("postResponses"))
            if isinstance(responses, dict):
                metrics["response_count"] = _int_or_none(responses.get("count"))

    if metrics["clap_count"] is None:
        match = re.search(r'"clapCount"\s*:\s*(\d+)', page_html)
        if match:
            metrics["clap_count"] = int(match.group(1))
    if metrics["response_count"] is None:
        match = re.search(
            r'"postResponses"\s*:\s*\{[^{}]*"count"\s*:\s*(\d+)',
            page_html,
        )
        if match:
            metrics["response_count"] = int(match.group(1))
    if metrics["reading_time_minutes"] is None:
        match = re.search(r'"readingTime"\s*:\s*([0-9]+(?:\.[0-9]+)?)', page_html)
        if match:
            metrics["reading_time_minutes"] = float(match.group(1))

    return metrics


def apply_article_metrics(article: Article, metrics: dict[str, int | float | None]) -> Article:
    article.clap_count = _int_or_none(metrics.get("clap_count"))
    article.response_count = _int_or_none(metrics.get("response_count"))
    article.reading_time_minutes = _float_or_none(metrics.get("reading_time_minutes"))
    return article


def fetch_article_metrics(article_url: str, timeout: int = 10) -> dict[str, int | float | None]:
    import requests

    user_agent = os.getenv(
        "APP_USER_AGENT",
        "MediumAIReader/0.1 (+https://example.com; respectful RSS discovery)",
    )
    response = requests.get(article_url, headers={"User-Agent": user_agent}, timeout=timeout)
    response.raise_for_status()
    return extract_article_metrics(response.text, article_url=article_url)


def enrich_articles_with_metrics(
    articles: Sequence[Article],
    timeout: int = 10,
    pause_seconds: float = 0.15,
) -> List[str]:
    errors: List[str] = []
    for article in articles:
        try:
            apply_article_metrics(article, fetch_article_metrics(article.url, timeout=timeout))
        except Exception as exc:  # noqa: BLE001 - article pages may block or omit metadata.
            errors.append(f"{article.url}: {exc}")
        time.sleep(pause_seconds)
    return errors


def fetch_feed(feed_url: str, max_items: int = 25, timeout: int = 15) -> List[Article]:
    import feedparser
    import requests

    user_agent = os.getenv(
        "APP_USER_AGENT",
        "MediumAIReader/0.1 (+https://example.com; respectful RSS discovery)",
    )
    response = requests.get(feed_url, headers={"User-Agent": user_agent}, timeout=timeout)
    response.raise_for_status()

    parsed = feedparser.parse(response.content)
    articles: List[Article] = []

    for entry in parsed.entries[:max_items]:
        title = html.unescape(getattr(entry, "title", "")).strip()
        link = getattr(entry, "link", "").strip()
        if not title or not link:
            continue

        tags = []
        for tag in getattr(entry, "tags", []) or []:
            term = tag.get("term") if isinstance(tag, dict) else getattr(tag, "term", "")
            if term:
                tags.append(str(term))

        author = getattr(entry, "author", "") or getattr(entry, "dc_creator", "")
        published = getattr(entry, "published", "") or getattr(entry, "updated", "")
        body = entry_text(entry)
        articles.append(
            Article(
                title=title,
                url=link,
                source_feed=feed_url,
                author=html_to_text(author),
                published=published,
                summary=body[:2200],
                content=body,
                tags=tags,
            )
        )

    return articles


def fetch_articles(
    feed_urls: Iterable[str],
    max_items_per_feed: int = 20,
    pause_seconds: float = 0.35,
    include_metrics: bool = False,
) -> tuple[List[Article], List[str]]:
    seen_urls = set()
    articles: List[Article] = []
    errors: List[str] = []

    for feed_url in feed_urls:
        try:
            feed_articles = fetch_feed(feed_url, max_items=max_items_per_feed)
            for article in feed_articles:
                normalized = article.url.split("?")[0].rstrip("/")
                if normalized in seen_urls:
                    continue
                seen_urls.add(normalized)
                articles.append(article)
        except Exception as exc:  # noqa: BLE001 - surface feed-level failures in the app UI.
            errors.append(f"{feed_url}: {exc}")
        time.sleep(pause_seconds)

    if include_metrics:
        metric_errors = enrich_articles_with_metrics(articles)
        errors.extend(f"Popularity metadata {error}" for error in metric_errors)

    return articles, errors
