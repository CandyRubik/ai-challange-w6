"""Authenticated workflow envelope; policy records alone carry no CI authority."""
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .decisions import OwnerDecision
from .models import Record, Text


class AnalysisSource(Record):
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0,le=20)
    result_sha256: Annotated[str,Field(pattern=r'^[0-9a-f]{64}$')]


class DecisionEnvelope(Record):
    schema_version: Literal[1]
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0,le=20)
    code_sha: Annotated[str,Field(pattern=r'^[0-9a-f]{40}$')]
    triggering_actor: Text
    decision: OwnerDecision
    source: AnalysisSource | None = None

    @field_validator('schema_version',mode='before')
    @classmethod
    def integer_version(cls,value):
        if type(value) is not int:
            raise ValueError('Decision envelope version must be an integer')
        return value

    @model_validator(mode='after')
    def source_binding(self):
        if self.code_sha!=self.decision.base_sha:
            raise ValueError('Owner workflow code must match its captured main')
        if (self.decision.proposal.action=='revoke')!=(self.source is None):
            raise ValueError('Only revocation may omit the original analysis source')
        return self

    @property
    def id(self):
        return f'{self.run_id}-{self.run_attempt}'
