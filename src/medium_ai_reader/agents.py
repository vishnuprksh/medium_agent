from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Sequence

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .medium_sources import build_feed_urls, infer_tags_from_prompt, keyword_terms, split_csv
from .models import Article


@dataclass
class DiscoveryPlan:
    intent: str
    tags: List[str]
    sources: List[str]
    feed_urls: List[str]


class PreferenceAgent:
    """Turns a fuzzy reading request into concrete Medium tags and feeds."""

    def plan(self, intent: str, tag_text: str, source_text: str, max_feeds: int = 12) -> DiscoveryPlan:
        explicit_tags = split_csv(tag_text)
        inferred_tags = infer_tags_from_prompt(intent)
        tags: List[str] = []
        for tag in [*explicit_tags, *inferred_tags]:
            clean = tag.strip()
            if clean and clean not in tags:
                tags.append(clean)

        sources = split_csv(source_text)
        feed_urls = build_feed_urls(tags=tags, sources=sources, max_feeds=max_feeds)
        return DiscoveryPlan(intent=intent, tags=tags, sources=sources, feed_urls=feed_urls)


class RankerAgent:
    """Ranks candidate articles against the user's intent."""

    def rank(self, intent: str, articles: Sequence[Article], use_openai: bool = True, top_k: int = 12) -> List[Article]:
        if not articles:
            return []

        docs = [self._article_doc(article) for article in articles]
        relevance = self._embedding_scores(intent, docs) if use_openai else None
        if relevance is None:
            relevance = self._tfidf_scores(intent, docs)

        query_terms = set(keyword_terms(intent, max_terms=18))
        for idx, article in enumerate(articles):
            article.relevance = float(relevance[idx])
            article.recency_score = self._recency_score(article.published)
            article.title_boost = self._title_boost(article, query_terms)
            article.score = (
                0.78 * article.relevance
                + 0.12 * article.recency_score
                + 0.10 * article.title_boost
            )
            article.reasons = self._local_reasons(article, query_terms)

        return sorted(articles, key=lambda a: a.score, reverse=True)[:top_k]

    def _article_doc(self, article: Article) -> str:
        text = article.search_text
        return text[:6500]

    def _tfidf_scores(self, intent: str, docs: Sequence[str]) -> np.ndarray:
        try:
            vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=10000)
            matrix = vectorizer.fit_transform([intent, *docs])
            scores = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
            return self._normalize(scores)
        except Exception:
            return np.zeros(len(docs), dtype=float)

    def _embedding_scores(self, intent: str, docs: Sequence[str]) -> np.ndarray | None:
        if not os.getenv("OPENROUTER_API_KEY"):
            return None
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=os.getenv("OPENROUTER_API_KEY"),
                base_url="https://openrouter.ai/api/v1"
            )
            model = os.getenv("OPENROUTER_EMBEDDING_MODEL", "tencent/hy3:free")
            # One embedding request keeps latency and cost down for the MVP.
            response = client.embeddings.create(model=model, input=[intent, *docs])
            vectors = np.array([item.embedding for item in response.data], dtype=float)
            query_vec = vectors[0]
            doc_vecs = vectors[1:]
            numerator = doc_vecs @ query_vec
            denominator = np.linalg.norm(doc_vecs, axis=1) * np.linalg.norm(query_vec)
            scores = numerator / np.maximum(denominator, 1e-12)
            return self._normalize(scores)
        except Exception:
            return None

    def _normalize(self, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=float)
        if len(values) == 0:
            return values
        min_value = float(np.min(values))
        max_value = float(np.max(values))
        if math.isclose(min_value, max_value):
            return np.ones_like(values) * 0.5
        return (values - min_value) / (max_value - min_value)

    def _recency_score(self, published: str) -> float:
        if not published:
            return 0.35
        try:
            dt = parsedate_to_datetime(published)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = max((datetime.now(timezone.utc) - dt).days, 0)
            return float(math.exp(-age_days / 180.0))
        except Exception:
            return 0.35

    def _title_boost(self, article: Article, query_terms: set[str]) -> float:
        if not query_terms:
            return 0.0
        haystack = f"{article.title} {' '.join(article.tags)}".lower()
        hits = sum(1 for term in query_terms if term in haystack)
        return min(hits / max(len(query_terms), 1), 1.0)

    def _local_reasons(self, article: Article, query_terms: set[str]) -> List[str]:
        haystack = article.search_text.lower()
        hits = [term for term in query_terms if term in haystack]
        reasons: List[str] = []
        if hits:
            reasons.append("Matches: " + ", ".join(hits[:6]))
        if article.tags:
            reasons.append("Medium tags: " + ", ".join(article.tags[:5]))
        if article.clap_count is not None:
            popularity = f"Claps: {article.clap_count:,}"
            if article.response_count is not None:
                popularity += f", responses: {article.response_count:,}"
            reasons.append(popularity)
        if article.published:
            reasons.append("Published: " + article.published)
        return reasons[:3]


class CuratorAgent:
    """Adds readable explanations for the top results."""

    def annotate(self, intent: str, articles: Sequence[Article], use_openai: bool = True) -> List[Article]:
        if not articles:
            return []

        if use_openai and os.getenv("OPENROUTER_API_KEY"):
            annotated = self._annotate_with_llm(intent, list(articles))
            if annotated:
                return annotated

        for article in articles:
            reason = article.reasons[0] if article.reasons else "Strong semantic match to your reading intent."
            article.ai_note = (
                f"Why it fits: {reason}. Score {article.score:.2f} combines topic relevance, title/tag match, and recency."
            )
        return list(articles)

    def _annotate_with_llm(self, intent: str, articles: List[Article]) -> List[Article] | None:
        try:
            from openai import OpenAI

            payload = [
                {
                    "index": idx,
                    "title": article.title,
                    "author": article.author,
                    "published": article.published,
                    "tags": article.tags[:8],
                    "clap_count": article.clap_count,
                    "response_count": article.response_count,
                    "reading_time_minutes": article.reading_time_minutes,
                    "score": round(article.score, 3),
                    "summary": article.summary[:900],
                }
                for idx, article in enumerate(articles)
            ]
            prompt = (
                "You are a precise reading curator. The user wants to read exactly this:\n"
                f"{intent}\n\n"
                "For each candidate article, write a short reason why it fits or what caveat it has. "
                "Return only JSON with this shape: "
                "[{\"index\":0,\"note\":\"...\"}]. Keep each note under 35 words.\n\n"
                f"Candidates:\n{json.dumps(payload, ensure_ascii=False)}"
            )

            client = OpenAI(
                api_key=os.getenv("OPENROUTER_API_KEY"),
                base_url="https://openrouter.ai/api/v1"
            )
            model = os.getenv("OPENROUTER_CHAT_MODEL", "tencent/hy3:free")
            response = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": "Return compact valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
            )
            text = getattr(response, "output_text", "") or ""
            notes = self._extract_json_array(text)
            if not notes:
                return None
            for item in notes:
                idx = int(item.get("index", -1))
                note = str(item.get("note", "")).strip()
                if 0 <= idx < len(articles) and note:
                    articles[idx].ai_note = note
            for article in articles:
                if not article.ai_note:
                    article.ai_note = "Good match based on semantic relevance and Medium tags."
            return articles
        except Exception:
            return None

    def _extract_json_array(self, text: str):
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\[[\s\S]*\]", text)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
