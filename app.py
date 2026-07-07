from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from medium_ai_reader.agents import CuratorAgent, PreferenceAgent, RankerAgent  # noqa: E402
from medium_ai_reader.medium_sources import fetch_articles  # noqa: E402

load_dotenv()

st.set_page_config(page_title="Medium AI Reader Finder", page_icon="🧭", layout="wide")


@st.cache_data(show_spinner=False, ttl=30 * 60)
def cached_fetch(feed_urls: tuple[str, ...], max_items_per_feed: int, include_metrics: bool):
    return fetch_articles(
        feed_urls,
        max_items_per_feed=max_items_per_feed,
        include_metrics=include_metrics,
    )


def passes_minimum(value, minimum):
    return minimum <= 0 or (value is not None and value >= minimum)


def filter_by_popularity(articles, min_claps: int, min_responses: int):
    return [
        article
        for article in articles
        if passes_minimum(article.clap_count, min_claps)
        and passes_minimum(article.response_count, min_responses)
    ]


def metric_text(label: str, value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        return f"{label}: {value:.1f} min"
    return f"{label}: {value:,}"


def article_table(articles):
    return pd.DataFrame(
        [
            {
                "rank": idx + 1,
                "title": article.title,
                "author": article.author,
                "published": article.published,
                "score": round(article.score, 3),
                "relevance": round(article.relevance, 3),
                "recency": round(article.recency_score, 3),
                "claps": article.clap_count,
                "responses": article.response_count,
                "reading_mins": (
                    round(article.reading_time_minutes, 1)
                    if article.reading_time_minutes is not None
                    else None
                ),
                "url": article.url,
            }
            for idx, article in enumerate(articles)
        ]
    )


def render_article(idx, article):
    st.markdown(f"### {idx}. [{article.title}]({article.url})")
    meta = []
    if article.author:
        meta.append(f"By {article.author}")
    if article.published:
        meta.append(article.published)
    if article.tags:
        meta.append("Tags: " + ", ".join(article.tags[:6]))
    for metric in [
        metric_text("Claps", article.clap_count),
        metric_text("Responses", article.response_count),
        metric_text("Read", article.reading_time_minutes),
    ]:
        if metric:
            meta.append(metric)
    if meta:
        st.caption(" | ".join(meta))

    st.write(article.ai_note)
    st.progress(min(max(float(article.score), 0.0), 1.0), text=f"Fit score: {article.score:.2f}")

    with st.expander("Preview and ranking details"):
        if article.summary:
            st.write(article.summary[:1400] + ("..." if len(article.summary) > 1400 else ""))
        st.json(
            {
                "score": round(article.score, 4),
                "semantic_relevance": round(article.relevance, 4),
                "recency_score": round(article.recency_score, 4),
                "title_tag_boost": round(article.title_boost, 4),
                "clap_count": article.clap_count,
                "response_count": article.response_count,
                "reading_time_minutes": article.reading_time_minutes,
                "source_feed": article.source_feed,
                "reasons": article.reasons,
            }
        )
    st.divider()


st.title("🧭 Medium AI Reader Finder")
st.caption("Agent-style Medium discovery: infer reading intent → explore public Medium RSS feeds → rank → curate.")

with st.sidebar:
    st.header("Reading intent")
    intent = st.text_area(
        "Describe exactly what you want to read",
        value="Practical, non-hype articles about building useful AI agents with Python and product thinking.",
        height=120,
    )

    tag_text = st.text_input(
        "Seed Medium tags/topics",
        value="artificial-intelligence, ai-agents, python, software-development",
        help="Comma-separated. The app will also infer tags from your reading intent.",
    )

    source_text = st.text_area(
        "Optional Medium profiles/publications/custom domains",
        value="",
        placeholder="@username\ntowards-data-science\nhttps://medium.com/better-programming\nhttps://publication-domain.com",
        height=100,
    )

    max_feeds = st.slider("Max feeds to explore", 3, 20, 10)
    max_items_per_feed = st.slider("Articles per feed", 5, 40, 20)
    top_k = st.slider("Final recommendations", 3, 20, 8)

    fetch_popularity = st.checkbox(
        "Fetch claps and responses from article pages",
        value=False,
        help="Slower: adds one public article-page request per unique result so you can filter by popularity.",
    )
    min_claps = st.number_input(
        "Minimum claps",
        min_value=0,
        value=0,
        step=10,
        disabled=not fetch_popularity,
    )
    min_responses = st.number_input(
        "Minimum responses",
        min_value=0,
        value=0,
        step=1,
        disabled=not fetch_popularity,
    )

    use_openrouter = st.checkbox("Use OpenRouter embeddings/curation when key is available", value=True)
    has_key = bool(os.getenv("OPENROUTER_API_KEY"))
    if use_openrouter and not has_key:
        st.info("No OPENROUTER_API_KEY found. The app will use the local TF-IDF fallback.")

    st.caption(
        "This MVP reads public RSS feeds and does not bypass Medium membership, login, robots rules, or paywalls."
    )

    run = st.button("Find my articles", type="primary")

if not intent.strip():
    st.warning("Enter a reading intent to start.")
    st.stop()

if run:
    preference_agent = PreferenceAgent()
    ranker_agent = RankerAgent()
    curator_agent = CuratorAgent()

    with st.status("Agents are exploring Medium...", expanded=True) as status:
        plan = preference_agent.plan(intent=intent, tag_text=tag_text, source_text=source_text, max_feeds=max_feeds)
        st.write(f"PreferenceAgent inferred {len(plan.tags)} tags.")
        st.write(f"ExplorerAgent built {len(plan.feed_urls)} feed URLs.")

        if not plan.feed_urls:
            st.error("No feed URLs could be built. Add at least one tag, profile, publication, or custom-domain source.")
            st.stop()

        with st.expander("Feeds being explored", expanded=False):
            for url in plan.feed_urls:
                st.code(url, language="text")

        articles, errors = cached_fetch(
            tuple(plan.feed_urls),
            max_items_per_feed=max_items_per_feed,
            include_metrics=fetch_popularity,
        )
        st.write(f"FetcherAgent collected {len(articles)} unique candidate articles.")
        if errors:
            with st.expander("Fetch errors"):
                for error in errors:
                    st.warning(error)

        if fetch_popularity:
            metric_count = sum(1 for article in articles if article.clap_count is not None)
            st.write(f"PopularityAgent found clap counts for {metric_count} articles.")
            if min_claps > 0 or min_responses > 0:
                before_filter = len(articles)
                articles = filter_by_popularity(articles, min_claps, min_responses)
                st.write(f"PopularityFilter kept {len(articles)} of {before_filter} articles.")

        if not articles:
            st.error("No articles found after the selected feeds and filters. Try broader tags or lower the popularity thresholds.")
            st.stop()

        ranked = ranker_agent.rank(intent=intent, articles=articles, use_openai=use_openrouter, top_k=top_k)
        st.write("RankerAgent scored candidates against your intent.")

        curated = curator_agent.annotate(intent=intent, articles=ranked, use_openai=use_openrouter)
        st.write("CuratorAgent wrote reading-fit notes.")
        status.update(label="Done", state="complete")

    st.subheader("Best matches")
    st.dataframe(article_table(curated), use_container_width=True, hide_index=True)

    for idx, article in enumerate(curated, start=1):
        render_article(idx, article)
else:
    st.info("Set your reading intent and click **Find my articles**.")

    st.markdown(
        """
        **How it works**

        1. **PreferenceAgent** extracts tags from your reading intent and seed sources.
        2. **ExplorerAgent** converts Medium tags, profiles, publications, and custom-domain publications into RSS feeds.
        3. **FetcherAgent** reads public RSS entries.
        4. **RankerAgent** scores each article. With an API key it uses embeddings; without one it uses a local TF-IDF fallback.
        5. **CuratorAgent** explains why each result fits your intent.
        """
    )
