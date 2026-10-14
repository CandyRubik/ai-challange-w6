"""Pinned review context and artifact data contract, without network effects."""
import hashlib
import json
from typing import Annotated
from pydantic import Field,model_validator
from .debt import DebtRegistry
from .decision_records import AnalysisSource
from .models import Profile, Record, ReviewResult

SHA = Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
DIGEST = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
STATUS_CONTEXT = 'AI Review / project'
SUMMARY_MARKER = '<!-- ai-review:summary:v1 -->'



class RunContext(Record):
    repository: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    repository_id: int = Field(gt=0)
    pr_number: int = Field(gt=0)
    base_sha: SHA
    head_sha: SHA
    code_sha: SHA
    merge_base_sha: SHA
    diff_sha256: DIGEST
    profile_sha256: DIGEST
    profile: Profile
    debt_sha256: DIGEST = hashlib.sha256(b'').hexdigest()
    debt_registry: DebtRegistry = Field(default_factory=lambda: DebtRegistry(schema_version=1))
    checks: dict[str, list[str]]
    before_paths: dict[str, str]
    change_status: dict[str, str]
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    external: bool
    analysis_source: AnalysisSource | None = None
    analysis_origin: Annotated[str,Field(pattern=r'^manual$')] | None = None

    @model_validator(mode='after')
    def source_origin(self):
        if (self.analysis_source is None)!=(self.analysis_origin is None):
            raise ValueError('Analysis source and origin must be captured together')
        return self

    @property
    def run_url(self):
        return workflow_url(self.repository, self.run_id, self.run_attempt)



def workflow_url(repository, run_id, run_attempt):
    return f'https://github.com/{repository}/actions/runs/{run_id}/attempts/{run_attempt}'



class ReviewArtifact(Record):
    context: RunContext
    result: ReviewResult
    result_sha256: DIGEST



def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

