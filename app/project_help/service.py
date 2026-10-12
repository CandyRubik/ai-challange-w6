"""Explicit /help routing, grounded documentation, and live repository context."""

from dataclasses import dataclass
import json
import re

from ..agents.agent import Agent
from ..indexing.store import SearchHit
from ..indexing.rag import DocumentRag
from ..orchestration.context import OrchestrationContext
from ..indexing.generation import generate_grounded_answer
from .mcp_client import ProjectMcpClient

DEFAULT_QUESTION = "Как устроен проект?"


def help_question(content: str) -> str | None:
    match = re.fullmatch(r"/help(?:\s+(.*))?", content.strip(), flags=re.DOTALL)
    return (match[1] or DEFAULT_QUESTION).strip() if match else None


@dataclass(frozen=True)
class HelpResult:
    answer: str
    details: dict


class ProjectHelpService:
    def __init__(self, rag: DocumentRag, mcp: ProjectMcpClient):
        self.rag = rag
        self.mcp = mcp

    @staticmethod
    def _branch_question(question: str) -> bool:
        question = re.sub(r"git[- ]ветка", "ветка", question, flags=re.IGNORECASE)
        return bool(re.fullmatch(
            r"(?:какая\s+(?:(?:сейчас|текущая|git)\s+)*ветка|(?:текущая|git)\s+ветка|"
            r"(?:what(?:'s| is)\s+(?:the\s+)?(?:current\s+)?(?:git\s+)?branch))\s*[?.!]*",
            question, flags=re.IGNORECASE,
        ))

    def answer(self, question: str, agent: Agent, orchestration: OrchestrationContext) -> HelpResult:
        context = self.mcp.branch()
        if self._branch_question(question):
            if context["status"] != "ok":
                text = "Текущая ветка недоступна: MCP не смог прочитать git-checkout проекта."
            elif context["detached"]:
                text = f"Checkout находится в detached HEAD: {context['commit']}."
            else:
                text = f"Текущая ветка проекта: {context['branch']}."
            return HelpResult(text, {"sources": [], "citations": [], "mcp": context,
                                     "grounding_status": "tool_answer"})
        hits = self.rag.retrieve(question, [], "structure", force=True)
        # Repository values are evidence just like document excerpts, with their own source.
        if context["status"] == "ok":
            hits = [*hits, SearchHit({
                "chunk_id": "mcp-git-branch", "kind": "tool", "source": "MCP:git_branch",
                "title": "Текущий git-контекст", "section": "git_branch", "text": json.dumps(context, ensure_ascii=False),
                "line_start": 1, "line_end": 1,
            }, 1.0)] if hits else hits
        metrics = {}
        grounded = generate_grounded_answer(
            agent, question, question, hits, metrics=metrics, prompt_version="complete-v3",
            orchestration=orchestration,
        )
        active = self.rag.index.status().get("index") or {}
        return HelpResult(grounded.content, {
            "sources": grounded.sources, "citations": grounded.citations,
            "grounding_status": grounded.status, "mcp": context,
            "index_version": active.get("version"), "metrics": metrics,
        })
