"""Reusable document retrieval and answer models for grounded-answer tests."""

from app.indexing.store import SearchHit


def hit(text: str = "ChatSessionService coordinates chat sessions.") -> SearchHit:
    return SearchHit({
        "chunk_id": "structure-0001",
        "source": "README.md",
        "title": "Test project docs",
        "section": "Architecture → ChatSessionService",
        "line_start": 6,
        "line_end": 6,
        "text": text,
    }, 0.9)


class Index:
    def __init__(self) -> None:
        self.searches: list[str] = []

    def ensure_built(self) -> dict:
        return {"version": "test"}

    def search(self, query: str, strategy: str, k: int) -> list[SearchHit]:
        self.searches.append(query)
        return [hit()]
