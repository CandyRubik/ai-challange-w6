from typing import Literal, Protocol

from pydantic import Field, model_validator

from .models import Record, Text


class JsonModel(Protocol):
    name: str

    def generate_json(self, *, messages: list[dict], max_tokens: int, schema: dict) -> str: ...


def generate_json(model, *, messages, max_tokens, schema, context_bytes):
    bounded = getattr(model, 'generate_bounded_json', None)
    if bounded is not None:
        return bounded(messages=messages, max_tokens=max_tokens, schema=schema, context_bytes=context_bytes)
    return model.generate_json(messages=messages, max_tokens=max_tokens, schema=schema)


class Limits(Record):
    context_bytes: int = Field(default=48_000, ge=1, le=256_000)
    changed_files: int = Field(default=30, ge=1, le=200)
    output_tokens: int = Field(default=4_000, ge=256, le=8_000)
    corpus_files: int = Field(default=500, ge=1, le=5_000)
    corpus_bytes: int = Field(default=2_000_000, ge=1, le=10_000_000)


class Fragment(Record):
    id: str
    path: str
    sha: str
    line: int
    text: str
    role: Literal["normative", "head", "before"]
    kind: Literal["text", "file_metadata"] = "text"


class Citation(Record):
    line: int | None = Field(default=None, ge=1)
    fragment_id: Text
    end_line: int | None = Field(default=None, ge=1)
    quote: Text | None = None

    @model_validator(mode='after')
    def complete_reference(self):
        if self.line is None:
            if self.quote is None or self.end_line is not None:
                raise ValueError('A quote or complete range is required')
        elif self.end_line is None or self.end_line < self.line:
            raise ValueError('Invalid evidence range')
        return self


class DraftCriterion(Record):
    path: Text
    rule_id: Text
    verdict: Literal["pass", "violation", "inconclusive"]
    reason: Text
    evidence: list[Citation] = Field(min_length=1, max_length=4)


class DraftFinding(Record):
    path: Text
    rule_id: Text
    severity: Literal["critical", "major", "minor"]
    summary: Text
    scenario: Text
    impact: Text
    recommendation: Text
    evidence: list[Citation] = Field(min_length=1, max_length=4)


class DraftReview(Record):
    criteria: list[DraftCriterion] = Field(max_length=100)
    findings: list[DraftFinding] = Field(max_length=10)
    recommendations: list[Text] = Field(max_length=5)
