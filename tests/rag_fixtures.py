"""Reusable document retrieval and answer models for grounded-answer tests."""

import json

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


class Reranker:
    def score(self, question: str, hits: list[SearchHit]) -> list[float]:
        return [0.9 for _ in hits]


class InterpretationModel:
    def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
        return json.dumps({
            "kind": "question",
            "question_scope": "document",
            "search_question": "What does ChatSessionService do?",
            "goal_action": "keep",
            "goal": "",
            "changes": [],
            "clarification": "",
        })


def decision(
    *, kind: str = "question", scope: str = "document",
    goal_action: str = "keep", goal: str = "", changes: list[dict] | None = None,
    clarification: str = "",
) -> dict:
    return {
        "kind": kind, "question_scope": scope,
        "search_question": "What does ChatSessionService do?" if kind == "question" else "",
        "goal_action": goal_action, "goal": goal,
        "changes": changes or [], "clarification": clarification,
    }


class AnswerModel:
    def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
        return json.dumps({"status": "answered", "claims": [{
            "text": "ChatSessionService выполняет отправленные задачи.",
            "evidence": [{
                "chunk_id": "structure-0001",
                "quote": "ChatSessionService coordinates chat sessions.",
            }],
        }]})
