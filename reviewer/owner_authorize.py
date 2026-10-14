"""Authorize one explicit owner proposal from a trusted main dispatch."""
from datetime import datetime,timedelta,timezone
import json
import re

from .actions import Actions
from .ci import prepare_snapshot
from .decision_records import AnalysisSource,DecisionEnvelope
from .decisions import OwnerProposal,propose
from .debt import expiry,load_registry
from .git import Repository
from .owner_journal import OWNER_WORKFLOW,owner_actors,read_journal,read_review,run_identity,workflow_id
from .publishing import start


def number(value,*,attempt=False):
    if not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]{0,19}',value) or attempt and int(value)>20:
        raise ValueError('Owner PR/source identifiers must be canonical positive numbers')
    return int(value)


def authorize(api,root,env,inputs,*,fetcher,now=None):
    now = now or datetime.now(timezone.utc)
    pr = number(inputs.get('pr'))
    context = prepare_snapshot(api,root,pr,env,fetcher=fetcher,start_status=False,owner_policy=False)
    repository,actions = Repository(root),Actions(api)
    run = actions.run_attempt(context.run_id,context.run_attempt)
    if (not run_identity(run,context.repository_id,OWNER_WORKFLOW,workflow_id(actions,OWNER_WORKFLOW),context.run_id,context.run_attempt)
        or run['head_sha']!=context.base_sha or run['head_branch']!='main' or run['event']!='workflow_dispatch'
        or run['head_repository']['id']!=context.repository_id or not owner_actors(run,context.profile)
        or run['actor']['login'].casefold()!=env['GITHUB_ACTOR'].casefold()
        or run['triggering_actor']['login'].casefold()!=env['GITHUB_TRIGGERING_ACTOR'].casefold()):
        raise ValueError('Owner dispatch origin is invalid')
    created = expiry(run['created_at'])
    if not created<=now<created+timedelta(days=7):
        raise ValueError('Owner dispatch expired; start a new dispatch')
    action = inputs.get('action')
    until = inputs.get('expires_at') or (None if action=='revoke' else (now+timedelta(days=1)).isoformat())
    proposal = OwnerProposal(action=action,targets=json.loads(inputs.get('targets','')),scope=json.loads(inputs.get('scope') or '[]'),
        reason=inputs.get('reason'),expires_at=until)
    if until and expiry(until)>created+timedelta(days=7):
        raise ValueError('Decision expiry exceeds the official dispatch lifetime')
    records = read_journal(actions,repository,context,now=now)
    source,result = None,None
    if action!='revoke':
        source_run = number(inputs.get('review_run'))
        attempt = number(inputs.get('review_attempt') or '1',attempt=True)
        artifact = read_review(actions,repository,source_run,attempt,pr,context=context)
        started = expiry(actions.run_attempt(source_run,attempt)['run_started_at'])
        if not now-timedelta(days=7)<=started<=now:
            raise ValueError('New owner decisions require an analysis attempt from the last seven days')
        source = AnalysisSource(run_id=source_run,run_attempt=attempt,result_sha256=artifact.result_sha256)
        result = artifact.result
    elif inputs.get('review_run'):
        raise ValueError('Revocation must omit the analysis source')
    proposed = load_registry(repository,context.head_sha,context.profile)[0] if action=='resolve_debt' else None
    decision = propose(context,result,proposal,env['GITHUB_ACTOR'],now=now,proposed_registry=proposed,
        known_decisions={key:record.decision for key,record in records.items()})
    envelope = DecisionEnvelope(schema_version=1,run_id=context.run_id,run_attempt=context.run_attempt,
        code_sha=context.code_sha,triggering_actor=env['GITHUB_TRIGGERING_ACTOR'],decision=decision,source=source)
    start(api,context)  # Only a fully validated request may claim the status.
    return envelope
