"""Owner-confirmed project debt, separate from newly introduced PR findings."""
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
import re
import time
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .audit import boundaries
from .models import DebtAssessment, Evidence, Profile, Record, Text

REGISTRY_PATH = '.ai-review/debt.json'
MAX_SCOPE_FILES, MAX_SCAN_BYTES = 500, 2_000_000


def expiry(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError('Debt expiry must use UTC')
    return parsed


class DebtEntry(Record):
    id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')]
    rule_id: Text
    title: Text
    status: Literal['candidate', 'active', 'resolved', 'waived']
    blocking: bool = True
    severity: Literal['critical', 'major', 'minor'] = 'major'
    scope: list[Text] = Field(min_length=1, max_length=100)
    closure: Literal['python-import-boundary', 'owner-attestation']
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    confirmed_by: str = ''
    reason: str = ''
    expires_at: str | None = None

    @model_validator(mode='after')
    def confirmation(self):
        if self.status != 'candidate' and (not self.confirmed_by.strip() or not self.reason.strip()):
            raise ValueError('Confirmed debt requires owner and reason')
        if self.status == 'waived' and not self.expires_at:
            raise ValueError('Waived debt requires an expiry')
        if self.expires_at:
            if self.status != 'waived':
                raise ValueError('Only waived debt may have an expiry')
            expiry(self.expires_at)
        for ref in self.evidence:
            if (not re.fullmatch(r'[0-9a-f]{40}', ref.sha) or ref.kind != 'text' or ref.line < 1
                or len(ref.quote.encode()) > 4096 or not any(fnmatchcase(ref.path, p) for p in self.scope)):
                raise ValueError('Debt evidence must be bounded, pinned text in scope')
        return self


class DebtRegistry(Record):
    schema_version: Literal[1]
    entries: list[DebtEntry] = Field(default_factory=list, max_length=100)

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('Registry version must be an integer')
        return value

    @model_validator(mode='after')
    def unique_ids(self):
        if len({e.id for e in self.entries}) != len(self.entries):
            raise ValueError('Invalid registry version or duplicate debt IDs')
        return self


def validate_registry(registry, profile: Profile):
    rules = {r.id: r for r in profile.rules}
    owners = {o.casefold() for o in profile.owners}
    for entry in registry.entries:
        rule = rules.get(entry.rule_id)
        if entry.status != 'candidate' and entry.confirmed_by.casefold() not in owners:
            raise ValueError('Debt confirmation requires a current profile owner')
        if rule is None and entry.rule_id != 'BUGS':
            raise ValueError('Unknown debt rule')
        if rule and entry.scope != rule.scope:
            raise ValueError('Debt must preserve the full rule scope')
        if entry.blocking and rule and rule.level != 'must':
            raise ValueError('Advisory rule cannot create blocking debt')
        if entry.blocking and entry.rule_id == 'BUGS' and entry.severity != 'critical':
            raise ValueError('Only critical bug debt can block')
        if entry.closure == 'python-import-boundary' and (not rule or rule.check != entry.closure):
            raise ValueError('Static closure requires an import-boundary rule')
    return registry


def load_registry(repository, sha, profile):
    present = repository.run('ls-tree', '-z', sha, '--', REGISTRY_PATH)
    raw = repository.read(sha, REGISTRY_PATH) if present else ''
    registry = DebtRegistry.model_validate_json(raw) if present else DebtRegistry(schema_version=1)
    validate_registry(registry, profile)
    for entry in registry.entries:
        for ref in entry.evidence:
            repository.run('merge-base', '--is-ancestor', ref.sha, sha)
            lines = repository.read(ref.sha, ref.path).splitlines(keepends=True)
            if ''.join(lines[ref.line-1:]).startswith(ref.quote) is False:
                raise ValueError('Debt evidence does not match its historical source')
    return registry, hashlib.sha256(raw.encode()).hexdigest()


def scan(repository, sha, rule):
    paths = []
    for entry in repository.run('ls-tree', '-r', '-z', sha).split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode, kind, _ = metadata.split()
        path = name.decode('utf-8')
        if mode not in (b'100644', b'100755') or kind != b'blob':
            for pattern in rule.scope:
                prefix = re.split(r'[*?\[]', pattern, maxsplit=1)[0]
                if (fnmatchcase(path, pattern) or prefix.startswith(path+'/')
                    or prefix != pattern and path.startswith(prefix)):
                    raise ValueError('Debt scope may contain unsupported descendants')
        elif any(fnmatchcase(path, pattern) for pattern in rule.scope):
            paths.append(path)
    paths.sort()
    if len(paths) > MAX_SCOPE_FILES:
        raise ValueError('Debt scope exceeds the file budget')
    evidence, count, size, started = [], 0, 0, time.monotonic()
    for path in paths:
        if not path.endswith('.py'):
            raise ValueError('Import-boundary debt supports Python only')
        text = repository.read(sha, path)
        size += len(text.encode())
        if size > MAX_SCAN_BYTES or time.monotonic()-started > 30:
            raise ValueError('Debt scope exceeds the scan budget')
        found, _ = boundaries(text, path, rule.forbidden_imports)
        for line in sorted(set(found.values())):
            quote = text.splitlines()[line-1]
            if len(quote.encode()) > 4096:
                raise ValueError('Debt evidence exceeds the line budget')
            count += 1
            if len(evidence) < 20:
                evidence.append(Evidence(path=path, sha=sha, line=line, quote=quote))
    return evidence, paths, count


def assess(repository, head, registry, profile, *, now=None):
    now = now or datetime.now(timezone.utc)
    rules, scans, assessments = {r.id:r for r in profile.rules}, {}, []
    for entry in registry.entries:
        verdict, reason, refs, paths, count = entry.status, entry.reason, entry.evidence, [], 0
        if entry.status == 'candidate':
            reason = 'Candidate is not approved and does not affect the check'
        elif entry.status == 'resolved':
            reason = 'Owner-confirmed closure: '+entry.reason
        elif entry.status == 'waived' and expiry(entry.expires_at) > now:
            reason = 'Owner waiver until '+entry.expires_at+': '+entry.reason
        elif entry.closure == 'owner-attestation':
            verdict, reason = 'open', 'Owner-confirmed debt; explicit closure required: '+entry.reason
        else:
            try:
                if entry.rule_id not in scans:
                    scans[entry.rule_id] = scan(repository, head, rules[entry.rule_id])
                refs, paths, count = scans[entry.rule_id]
                verdict, reason = ('open', 'Forbidden dependency remains') if count else ('fixed', 'Full rule scope has no forbidden imports')
            except (ValueError, SyntaxError, RecursionError):
                verdict, reason, refs = 'inconclusive', 'Debt scope could not be checked completely', []
        assessments.append(DebtAssessment(debt_id=entry.id, rule_id=entry.rule_id, title=entry.title,
            blocking=entry.blocking, verdict=verdict, reason=reason, evidence=refs, checked_paths=paths, violation_count=count))
    return assessments


def include_debt(repository, result, profile):
    try:
        registry, registry_hash = load_registry(repository, result.snapshot.base_sha, profile)
        assessments = assess(repository, result.snapshot.head_sha, registry, profile)
        incomplete = any(a.verdict == 'inconclusive' for a in assessments)
        status = 'error' if incomplete or result.status == 'error' else ('failure' if
            any(a.blocking and a.verdict == 'open' for a in assessments) else result.status)
        return result.model_copy(update={'debt_sha256':registry_hash, 'project_debt':assessments, 'status':status,
            'execution':'partial' if incomplete and result.execution != 'failed' else result.execution,
            'limitations':[*result.limitations, *(['Project debt was not fully checked'] if incomplete else [])]})
    except (ValueError, SyntaxError, RecursionError):
        return result.model_copy(update={'status':'error', 'execution':'failed',
            'limitations':[*result.limitations, 'Trusted project debt registry is invalid or unavailable']})


def suggest(repository, sha, profile):
    entries = []
    for rule in profile.rules:
        if rule.source.quote not in repository.read(sha, rule.source.path):
            raise ValueError('Normative source is missing')
        if rule.check == 'python-import-boundary':
            refs, _, count = scan(repository, sha, rule)
            if count:
                entries.append(DebtEntry(id='debt-'+hashlib.sha256(rule.id.encode()).hexdigest()[:16],
                    rule_id=rule.id, title=rule.requirement, status='candidate', blocking=rule.level=='must',
                    scope=rule.scope, closure=rule.check, evidence=refs))
    return DebtRegistry(schema_version=1, entries=entries)
