"""Read owner authority from canonical Actions jobs/artifacts, never PR comments."""
from datetime import datetime, timedelta, timezone
import hashlib
import re

from .actions import positive
from .artifacts import preview
from .contracts import ReviewArtifact
from .decision_records import DecisionEnvelope
from .debt import expiry
from .github import GitHubError
from .models import Profile

OWNER_WORKFLOW = '.github/workflows/ai-review-owner.yml'
REVIEW_WORKFLOW = '.github/workflows/ai-review.yml'
AUTHORIZE_JOB = 'Authorize owner decision'
SNAPSHOT_FIELDS = ('repository','repository_id','pr_number','base_sha','head_sha','code_sha','merge_base_sha',
    'diff_sha256','profile_sha256','profile','debt_sha256','debt_registry','checks','before_paths','change_status','external')


def same_snapshot(first,second):
    return all(getattr(first,key)==getattr(second,key) for key in SNAPSHOT_FIELDS)


def workflow_id(actions,path):
    workflow = actions.api.request('GET',f'/actions/workflows/{path.rsplit("/",1)[1]}')
    if workflow.get('path')!=path:
        raise GitHubError('Canonical review workflow path mismatch')
    return positive(workflow.get('id'))


def run_identity(run,repository_id,path,workflow,run_id,attempt):
    return (positive(run['id'])==run_id and positive(run['run_attempt'])==attempt
        and positive(run['repository']['id'])==repository_id and positive(run['workflow_id'])==workflow
        and run['path']==path and isinstance(run['head_sha'],str) and bool(re.fullmatch(r'[0-9a-f]{40}',run['head_sha'])))


def owner_actors(run,profile):
    owners = {owner.casefold() for owner in profile.owners}
    return (run['actor']['login'].casefold() in owners and run['triggering_actor']['login'].casefold() in owners)


def pinned_profile(repository,sha,expected_hash):
    raw = repository.read(sha,'.ai-review/profile.json')
    if hashlib.sha256(raw.encode()).hexdigest()!=expected_hash:
        raise GitHubError('Owner/source profile differs from its trusted Git revision')
    return Profile.model_validate_json(raw)


def read_review(actions,repository,run_id,attempt,number,*,context=None):
    """Verify a complete original AI artifact; optionally require current snapshot."""
    try:
        run_id,attempt,number = positive(run_id),positive(attempt),positive(number)
        if attempt>20:
            raise GitHubError('Review attempt budget exceeded')
        run = actions.run_attempt(run_id,attempt)
        canonical = workflow_id(actions,REVIEW_WORKFLOW)
        root_id = positive(actions.api.request('GET','')['id'])
        if not run_identity(run,root_id,REVIEW_WORKFLOW,canonical,run_id,attempt):
            raise GitHubError('Original review run identity is invalid')
        if not actions.successful_job(run_id,attempt,f'review ({number}) / analyze'):
            raise GitHubError('Original review analyze job did not succeed')
        raw = actions.artifact_json(run,attempt,f'result-{number}-{attempt}','result.json',member_limit=2_000_000)
        artifact = ReviewArtifact.model_validate(raw)
        source = artifact.context
        if (source.repository!=actions.api.repository or source.repository_id!=root_id
            or (source.run_id,source.run_attempt,source.pr_number)!=(run_id,attempt,number)):
            raise GitHubError('Original review artifact identity differs from Actions')
        if run['event']=='pull_request_target':
            if source.external or run['head_sha']!=source.head_sha or positive(run['head_repository']['id'])!=root_id:
                raise GitHubError('Original PR target run head/repository is invalid')
        elif run['event'] in ('workflow_dispatch','push'):
            if (run['head_branch']!='main' or run['head_sha']!=source.base_sha
                or positive(run['head_repository']['id'])!=root_id
                or run['event']=='workflow_dispatch' and not owner_actors(run,source.profile)):
                raise GitHubError('Original main review run is invalid')
        else:
            raise GitHubError('Original review event is unsupported')
        trusted = context.base_sha if context else repository.run('rev-parse','HEAD^{commit}').decode().strip()
        repository.run('merge-base','--is-ancestor',source.code_sha,trusted)
        if pinned_profile(repository,source.base_sha,source.profile_sha256)!=source.profile:
            raise GitHubError('Original review policy differs from trusted Git')
        preview(source,raw)
        if artifact.result.status=='error' or artifact.result.execution!='complete':
            raise GitHubError('Original analysis is incomplete and cannot authorize exceptions')
        if context and not same_snapshot(source,context):
            raise GitHubError('Original analysis snapshot/profile differs from current PR')
        return artifact
    except GitHubError:
        raise
    except (ValueError,KeyError,TypeError,AttributeError,OSError):
        raise GitHubError('Original review provenance or artifact is invalid') from None


def read_journal(actions,repository,context,*,now=None):
    """All successful authorize attempts survive later failed reruns/apply jobs."""
    try:
        now = now or datetime.now(timezone.utc)
        canonical = workflow_id(actions,OWNER_WORKFLOW)
        cutoff = (now-timedelta(days=7)).isoformat()
        runs = actions.collection(f'/actions/workflows/{canonical}/runs','workflow_runs',params={
            'event':'workflow_dispatch','branch':'main','created':'>='+cutoff})
        records,checked = {},0
        for row in runs:
            if row.get('display_title')!=f'Review decision PR #{context.pr_number}':
                continue
            count = positive(row['run_attempt'])
            if count>20 or checked+count>100:
                raise GitHubError('Owner decision attempt budget exceeded')
            checked += count
            for attempt in range(1,count+1):
                run = actions.run_attempt(positive(row['id']),attempt)
                if not run_identity(run,context.repository_id,OWNER_WORKFLOW,canonical,row['id'],attempt):
                    raise GitHubError('Owner workflow run identity is invalid')
                if (run['event']!='workflow_dispatch' or run['head_branch']!='main'
                    or positive(run['head_repository']['id'])!=context.repository_id or not owner_actors(run,context.profile)):
                    continue
                if not actions.successful_job(run['id'],attempt,AUTHORIZE_JOB):
                    continue
                repository.run('merge-base','--is-ancestor',run['head_sha'],context.base_sha)
                raw = actions.artifact_json(run,attempt,f'decision-{run["id"]}-{attempt}','decision.json')
                envelope = DecisionEnvelope.model_validate(raw)
                decision = envelope.decision
                historical = pinned_profile(repository,envelope.code_sha,decision.profile_sha256)
                if ((envelope.run_id,envelope.run_attempt,envelope.code_sha)!=(run['id'],attempt,run['head_sha'])
                    or envelope.triggering_actor.casefold()!=run['triggering_actor']['login'].casefold()
                    or decision.actor.casefold()!=run['actor']['login'].casefold()
                    or (decision.repository_id,decision.pr_number)!=(context.repository_id,context.pr_number)
                    or expiry(decision.created_at)>now or not owner_actors(run,historical)):
                    raise GitHubError('Owner decision differs from its authenticated workflow origin')
                records[envelope.id] = envelope
                if len(records)>100:
                    raise GitHubError('Owner decision journal budget exceeded')
        return records
    except GitHubError:
        raise
    except (ValueError,KeyError,TypeError,AttributeError,OSError):
        raise GitHubError('Owner decision journal proof is invalid') from None
