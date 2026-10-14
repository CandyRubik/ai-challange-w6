"""Scoped owner policy. These records alone are not authenticated CI authority."""
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
import re
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .debt import expiry
from .models import Record, Text


class OwnerProposal(Record):
    action: Literal['dismiss','waive_rule','waive_debt','resolve_debt','revoke']
    targets: list[Annotated[str,Field(min_length=1,max_length=128)]] = Field(min_length=1,max_length=10)
    reason: Annotated[str,Field(min_length=1,max_length=2000,pattern=r'\S')]
    scope: list[Annotated[str,Field(min_length=1,max_length=300)]] = Field(default_factory=list,max_length=20)
    expires_at: str | None = None

    @model_validator(mode='after')
    def bounded_scope(self):
        if len(set(self.targets)) != len(self.targets) or len(set(self.scope)) != len(self.scope):
            raise ValueError('Decision targets and scope must be unique')
        for path in self.scope:
            if path.startswith('/') or '\\' in path or any(p in ('..','.') for p in path.split('/')) or any(ord(c)<32 or ord(c)==127 for c in path):
                raise ValueError('Decision scope must contain repository-relative patterns')
        if self.action == 'waive_rule' and not self.scope:
            raise ValueError('Rule waiver requires an explicit path scope')
        if self.action == 'revoke' and (self.scope or self.expires_at):
            raise ValueError('Revocation has no path scope or expiry')
        if self.action != 'revoke' and not self.expires_at:
            raise ValueError('Temporary owner decisions require a UTC expiry')
        if self.expires_at:
            expiry(self.expires_at)
        return self


class OwnerDecision(Record):
    proposal: OwnerProposal
    actor: Text
    repository_id: int = Field(gt=0)
    pr_number: int = Field(gt=0)
    base_sha: Annotated[str,Field(pattern=r'^[0-9a-f]{40}$')]
    head_sha: Annotated[str,Field(pattern=r'^[0-9a-f]{40}$')]
    profile_sha256: Annotated[str,Field(pattern=r'^[0-9a-f]{64}$')]
    created_at: str
    bindings: dict[str,Annotated[str,Field(pattern=r'^[0-9a-f]{64}$')]]

    @model_validator(mode='after')
    def bound_targets(self):
        if set(self.bindings) != set(self.proposal.targets):
            raise ValueError('Decision must bind every declared target exactly once')
        created = expiry(self.created_at)
        if self.proposal.expires_at and not created < expiry(self.proposal.expires_at) <= created+timedelta(days=7):
            raise ValueError('Decision lifetime exceeds seven days')
        return self


class DecisionEffects(Record):
    state: Literal['success','failure','error']
    accepted_findings: dict[str,str] = Field(default_factory=dict)
    accepted_debt: dict[str,str] = Field(default_factory=dict)
    remaining_findings: list[str] = Field(default_factory=list)
    remaining_debt: list[str] = Field(default_factory=list)
    expires_at: str | None = None


def fingerprint(value):
    from .contracts import digest
    return digest(value.model_dump())


def finding_path(context,finding):
    if finding.evidence.sha == context.head_sha:
        return finding.evidence.path
    return next((path for path,before in context.before_paths.items() if before == finding.evidence.path),finding.evidence.path)


def propose(context,result,proposal,actor,*,now=None,proposed_registry=None,known_decisions=None):
    """Validate a proposed record; callers authenticate GitHub actor and provenance."""
    now = now or datetime.now(timezone.utc)
    if actor.casefold() not in {o.casefold() for o in context.profile.owners}:
        raise ValueError('Decision requires a current profile owner')
    if proposal.expires_at and not now < expiry(proposal.expires_at) <= now+timedelta(days=7):
        raise ValueError('Owner decision must expire within seven days')
    findings = {fingerprint(f):f for f in result.findings} if result else {}
    entries = {e.id:e for e in context.debt_registry.entries}
    rules = {r.id:r for r in context.profile.rules}
    bindings = {}
    if proposal.action != 'revoke' and (not result or result.status == 'error' or result.execution != 'complete'):
        raise ValueError('Incomplete review cannot be overridden')
    for target in proposal.targets:
        if proposal.action == 'dismiss':
            finding = findings.get(target)
            if finding is None or proposal.scope and not any(fnmatchcase(finding_path(context,finding),p) for p in proposal.scope):
                raise ValueError('Dismiss target or scope differs from the original finding')
            bindings[target] = target
        elif proposal.action == 'waive_rule':
            if target == 'BUGS' or target not in rules:
                raise ValueError('Rule waiver requires a known rule; broad BUGS waiver is forbidden')
            bindings[target] = fingerprint(rules[target])
        elif proposal.action in ('waive_debt','resolve_debt'):
            entry = entries.get(target)
            if entry is None or not any(a.debt_id==target and a.verdict=='open' for a in result.project_debt):
                raise ValueError('Debt decision requires an open confirmed entry')
            if proposal.scope and proposal.scope != entry.scope:
                raise ValueError('Debt decision must preserve the full entry scope')
            if proposal.action == 'resolve_debt':
                proposed = next((e for e in proposed_registry.entries if e.id==target),None) if proposed_registry else None
                expected = entry.model_copy(update={'status':'resolved','confirmed_by':actor,'reason':proposal.reason,'expires_at':None})
                if entry.closure != 'owner-attestation' or proposed != expected:
                    raise ValueError('Manual closure requires matching resolved entry in proposed head')
            bindings[target] = fingerprint(entry)
        else:
            previous = (known_decisions or {}).get(target)
            if previous is None or previous.proposal.action == 'revoke' or (
                previous.repository_id,previous.pr_number)!=(context.repository_id,context.pr_number):
                raise ValueError('Revocation requires an existing authenticated decision ID')
            bindings[target] = fingerprint(previous)
    return OwnerDecision(proposal=proposal,actor=actor,repository_id=context.repository_id,pr_number=context.pr_number,
        base_sha=context.base_sha,head_sha=context.head_sha,profile_sha256=context.profile_sha256,
        created_at=now.isoformat(),bindings=bindings)


def effects(context,result,decisions,*,now=None):
    """Compute effects for authenticated ID→record input without altering findings."""
    now = now or datetime.now(timezone.utc)
    accepted,debts = {},{}
    owners = {o.casefold() for o in context.profile.owners}
    eligible = {key:d for key,d in decisions.items() if d.repository_id==context.repository_id
                and d.pr_number==context.pr_number and d.actor.casefold() in owners and expiry(d.created_at)<=now}
    revoked = {target for d in eligible.values() if d.proposal.action=='revoke'
               for target,binding in d.bindings.items() if target in eligible and fingerprint(eligible[target])==binding}
    findings = {fingerprint(f):f for f in result.findings}
    entries = {e.id:e for e in context.debt_registry.entries}
    rules = {r.id:r for r in context.profile.rules}
    if result.status != 'error' and result.execution == 'complete':
        order = lambda item: (*map(int,item[0].split('-')),item[0]) if re.fullmatch(r'[1-9]\d*-[1-9]\d*',item[0]) else (0,0,item[0])
        for key,d in sorted(eligible.items(),key=order):
            p = d.proposal
            if key in revoked or p.action=='revoke' or d.profile_sha256!=context.profile_sha256 or expiry(p.expires_at)<=now:
                continue
            exact = (d.base_sha,d.head_sha)==(context.base_sha,context.head_sha)
            for target,binding in d.bindings.items():
                if p.action=='dismiss' and exact and target in findings and binding==target:
                    accepted[target] = key
                elif p.action=='waive_rule' and target!='BUGS' and target in rules and fingerprint(rules[target])==binding:
                    for fid,f in findings.items():
                        if f.rule_id==target and any(fnmatchcase(finding_path(context,f),path) for path in p.scope):
                            accepted[fid] = key
                elif target in entries and fingerprint(entries[target])==binding and (
                    p.action=='waive_debt' or p.action=='resolve_debt' and exact):
                    debts[target] = key
    deadlines = [eligible[key].proposal.expires_at for key in set(accepted.values())|set(debts.values())]
    remaining = [fid for fid,f in findings.items() if f.blocking and fid not in accepted]
    remaining_debt = [a.debt_id for a in result.project_debt if a.blocking and a.verdict=='open' and a.debt_id not in debts]
    unexplained_failure = result.status=='failure' and not any(f.blocking for f in findings.values()) and not any(
        a.blocking and a.verdict=='open' for a in result.project_debt)
    state = ('error' if result.status=='error' or result.execution!='complete' else
             'failure' if remaining or remaining_debt or unexplained_failure else 'success')
    return DecisionEffects(state=state,accepted_findings=accepted,accepted_debt=debts,
        remaining_findings=remaining,remaining_debt=remaining_debt,
        expires_at=min(deadlines,key=expiry) if deadlines else None)
