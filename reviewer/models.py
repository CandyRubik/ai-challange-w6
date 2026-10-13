from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Source(Record):
    path: Text
    quote: Text


class Examples(Record):
    allowed: Text
    forbidden: Text


class Rule(Record):
    id: Text
    category: Literal["architecture", "forbidden", "best_practice", "invariant"]
    level: Literal["must", "should"]
    scope: list[Text] = Field(min_length=1)
    requirement: Text
    rationale: Text
    check: Literal["python-import-boundary", "semantic"]
    forbidden_imports: list[Text] = Field(default_factory=list)
    source: Source
    examples: Examples | None = None

    @model_validator(mode="after")
    def require_imports(self):
        if self.check == "python-import-boundary" and not self.forbidden_imports:
            raise ValueError("Import-boundary rule needs forbidden_imports")
        return self


class Profile(Record):
    schema_version: Literal[1]
    project: Text
    owners: list[Text] = Field(min_length=1)
    rules: list[Rule] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def unique_rules(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("Duplicate rule IDs")
        return self


class Snapshot(Record):
    base_sha: str
    head_sha: str
    merge_base_sha: str
    diff_sha256: str


class Evidence(Source):
    sha: str
    line: int
    kind: Literal["text", "file_metadata"] = "text"


class Finding(Record):
    rule_id: str
    blocking: bool
    message: str
    evidence: Evidence
    source: Evidence | None = None
    category: Literal["bug", "architecture", "forbidden", "best_practice", "invariant"] = "forbidden"
    severity: Literal["critical", "major", "minor"] = "major"
    scenario: str = ""
    impact: str = ""
    recommendation: str = ""
    supporting_evidence: list[Evidence] = Field(default_factory=list)


class Criterion(Record):
    path: str
    rule_id: str
    verdict: Literal["pass", "violation", "inconclusive"]
    reason: str
    evidence: list[Evidence] = Field(default_factory=list)


class ReviewResult(Record):
    snapshot: Snapshot | None = None
    profile_sha256: str = ""
    profile_origin: Literal["base", "explicit_local"] = "base"
    execution: Literal["complete", "partial", "failed"] = "complete"
    status: Literal["success", "failure", "error"] = "success"
    findings: list[Finding] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)
    not_applicable: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    policy: list[Rule] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    trace: dict = Field(default_factory=dict)
