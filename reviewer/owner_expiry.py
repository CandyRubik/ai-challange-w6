"""Invalidate owner-dependent green statuses without a model or head execution."""
from datetime import datetime,timezone
import re

from .actions import Actions
from .artifacts import preview
from .ci import check_context,identity,write_state
from .contracts import ReviewArtifact,STATUS_CONTEXT
from .debt import expiry
from .decisions import effects
from .github import GitHubError
from .git import Repository
from .owner_journal import (AUTHORIZE_JOB,OWNER_WORKFLOW,REVIEW_WORKFLOW,owner_actors,pinned_profile,
    read_journal,read_review,run_identity,same_snapshot,workflow_id)
from .owner_runtime import stamp
from .publishing import StaleReview,assert_current,capture,set_status


def latest(api,head):
    return next((s for s in api.items(f'/commits/{head}/statuses') if s.get('context')==STATUS_CONTEXT),{})


def published(actions,repository,run_id,attempt,number,current):
    run = actions.run_attempt(run_id,attempt)
    if run.get('path')==REVIEW_WORKFLOW:
        artifact = read_review(actions,repository,run_id,attempt,number,context=current)
        job = f'review ({number}) / publish'
    else:
        canonical = workflow_id(actions,OWNER_WORKFLOW)
        if (not run_identity(run,current.repository_id,OWNER_WORKFLOW,canonical,run_id,attempt)
            or run['event']!='workflow_dispatch' or run['head_branch']!='main'
            or run['head_repository']['id']!=current.repository_id or not owner_actors(run,current.profile)
            or not actions.successful_job(run_id,attempt,AUTHORIZE_JOB)):
            raise GitHubError('Published owner workflow proof is invalid')
        raw = actions.artifact_json(run,attempt,f'result-{number}-{attempt}','result.json',member_limit=2_000_000)
        artifact = ReviewArtifact.model_validate(raw)
        source = artifact.context
        if ((source.run_id,source.run_attempt,source.pr_number)!=(run_id,attempt,number)
            or run['head_sha']!=source.code_sha or not same_snapshot(source,current)
            or pinned_profile(repository,source.base_sha,source.profile_sha256)!=source.profile):
            raise GitHubError('Published owner result differs from its run/current snapshot')
        preview(source,raw)
        job = 'Apply owner decision'
    if not actions.successful_job(run_id,attempt,job):
        raise GitHubError('Published review job did not complete successfully')
    return artifact


def expire(api,root,number,env,*,fetcher,now=None):
    now = now or datetime.now(timezone.utc)
    repository = Repository(root)
    _,run_id,attempt = identity(env)
    fetched = fetcher(root,number,api.repository,env.get('GITHUB_TOKEN',''))
    current = capture(api,root,number,run_id=run_id,run_attempt=attempt)
    check_context(root,current,env,captured_source=False,captured_owner=False)
    if fetched!=current.head_sha: raise StaleReview('PR changed during expiry fetch')
    prior = latest(api,current.head_sha)
    if prior.get('state')!='success': return 'unchanged'
    def guard():
        assert_current(api,current,started=False)
        if latest(api,current.head_sha)!=prior:
            raise StaleReview('Review generation changed during expiry check')
    guard()
    reason = None
    try:
        match = re.fullmatch(re.escape(f'https://github.com/{api.repository}/actions/runs/')+r'([1-9][0-9]*)/attempts/([1-9][0-9]*)',prior.get('target_url') or '')
        if not match or int(match[2])>20: raise ValueError('Published review origin is invalid')
        actions = Actions(api)
        artifact = published(actions,repository,int(match[1]),int(match[2]),number,current)
        if artifact.context.owner_records and artifact.context.owner_sha256!=stamp(artifact.context.owner_records):
            raise ValueError('Published owner context digest is invalid')
        bodies = {key:record.decision for key,record in artifact.context.owner_records.items()}
        before = effects(artifact.context,artifact.result,bodies,now=expiry(prior['created_at']))
        if before.state!='success': raise ValueError('Published green verdict is inconsistent')
        if artifact.result.status=='success' or not before.expires_at: return 'unchanged'
        records = read_journal(actions,repository,current,now=now)
        after = effects(current,artifact.result,{key:record.decision for key,record in records.items()},now=now)
        if after.state=='success': return 'unchanged'
        reason = 'Owner exception expired or no longer applies; rerun AI Review'
    except (ValueError,KeyError,TypeError,AttributeError,GitHubError):
        reason = 'Accepted review could not be validated; rerun AI Review'
    guard()
    set_status(api,current,'error',reason)
    assert_current(api,current)
    return 'invalidated'


def select(api,event,env,*,capacity=20):
    identity(env)
    raw = event.get('inputs',{}).get('pr')
    if raw:
        from .owner_authorize import number
        rows = [api.pull(number(raw))]
    else:
        rows = api.open_pulls()
    rows = [pr for pr in rows if pr['state']=='open' and pr['base']['ref']=='main']
    if len(rows)>capacity:
        for pr in rows: write_state(api,pr['head']['sha'],env,'error','Expiry queue capacity exceeded; manual review required')
        raise ValueError('Expiry queue capacity exceeded; no analysis started')
    return [pr['number'] for pr in rows]
