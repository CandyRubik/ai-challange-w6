"""Shared generation path for the chat and paired model evaluation."""

import json
import re
from dataclasses import replace
from time import monotonic

from ..agents.agent import Agent, AgentOutputError
from .grounding import GroundedAnswer, GroundingError, validate_grounded_answer
from .store import SearchHit
from ..invariants import InvariantPolicy, InvariantViolation
from ..memory.context import MemoryContext
from ..orchestration.context import OrchestrationContext
from .citation_options import prepare_citations, resolve_citations


GROUNDING_SYSTEM_PROMPT = (
    "You format answers to questions about retrieved document excerpts. "
    "Return only one valid JSON object matching the requested schema. "
    "Never emit prose outside the JSON object."
)
MAX_GROUNDING_ATTEMPTS = 4


def generate_grounded_answer(
    agent: Agent, question: str, search_question: str, hits: list[SearchHit], *,
    memory: MemoryContext | None = None, metrics: dict | None = None,
    prompt_version: str = "baseline", orchestration: OrchestrationContext | None = None,
) -> GroundedAnswer:
    metrics = metrics if metrics is not None else {}
    if not hits:
        return GroundedAnswer.unknown()
    payload = json.dumps({
        "user_question": question, "standalone_search_question": search_question,
    }, ensure_ascii=False)
    feedback = ""
    prompt, schema, lookup = prepare_citations(hits, prompt_version=prompt_version)
    if not lookup:
        return GroundedAnswer.unknown()
    policy = InvariantPolicy(orchestration.invariants if orchestration is not None else None)
    if policy.settings is not None and policy.settings.sentence_limit_enabled:
        limit = min(5, policy.settings.max_sentences)
        schema["properties"]["claims"]["maxItems"] = limit
        prompt = (
            f"Use at most {limit} sentences in the entire visible answer. "
            "Avoid dotted filenames in prose under this limit; the UI shows exact source paths separately.\n"
            + prompt
        )
    for attempt in range(MAX_GROUNDING_ATTEMPTS):
        metrics["generation_attempts"] = metrics.get("generation_attempts", 0) + 1
        started = monotonic()
        try:
            raw = agent.respond_json(
                [], payload, memory=memory, schema=schema, orchestration=orchestration,
                document_context=prompt + (
                    "\nPrevious output failed validation: " + feedback[:200] if feedback else ""
                ),
            )
            answer = validate_grounded_answer(resolve_citations(raw, lookup), hits)
            content = (re.sub(r"\s*\[DOC:[^]]+\]", "", answer.content)
                       if orchestration is not None else answer.content)
            return replace(answer, content=policy.apply(content))
        except (AgentOutputError, GroundingError, InvariantViolation) as error:
            feedback = str(error)
            metrics.setdefault("validation_errors", []).append(feedback)
        finally:
            metrics["generation_seconds"] = metrics.get("generation_seconds", 0) + monotonic() - started
    return GroundedAnswer.failed()
