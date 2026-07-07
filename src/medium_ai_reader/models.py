from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Article:
    title: str
    url: str
    source_feed: str
    author: str = ""
    published: str = ""
    summary: str = ""
    content: str = ""
    tags: List[str] = field(default_factory=list)
    score: float = 0.0
    relevance: float = 0.0
    recency_score: float = 0.0
    title_boost: float = 0.0
    reasons: List[str] = field(default_factory=list)
    ai_note: str = ""

    @property
    def search_text(self) -> str:
        tag_text = ", ".join(self.tags)
        return f"{self.title}\nAuthor: {self.author}\nTags: {tag_text}\n{self.summary}\n{self.content}".strip()
