from datetime import timedelta

import pytest

from reviewer.decision_records import DecisionEnvelope
from reviewer.decisions import OwnerProposal,propose
from reviewer.models import ReviewResult
from reviewer.owner_expiry import expire,select
from reviewer.owner_runtime import stamp
from reviewer.publishing import STATUS_CONTEXT,StaleReview,make_artifact
from tests.test_review_owner_journal import NOW,Proofs
from tests.test_review_publishing import fixture
from tests.test_reviewer import git


def setup(tmp_path,monkeypatch,*,plain=False,owner=False):
    api,ctx,raw,pr,calls,statuses,root = fixture(tmp_path,code='pass\n' if plain else 'import sqlite3\n')
    git(root,'checkout','--detach',ctx.base_sha)
    proposal = OwnerProposal(action='waive_rule',targets=['ARCH-STATE-PURE'],scope=['app/state/*.py'],reason='Explicit temporary rule exception',expires_at=(NOW+timedelta(days=1)).isoformat())
    decision = propose(ctx,ReviewResult.model_validate(raw['result']),proposal,'maintainer',now=NOW) if plain else None
    proofs = Proofs(ctx,raw,decision=decision)
    records = {'20-1':DecisionEnvelope.model_validate(proofs.envelope)}
    source = ctx.model_copy(update={'owner_records':{} if plain else records,'owner_sha256':stamp({} if plain else records)})
    if owner:
        source=source.model_copy(update={'run_id':20})
        proofs.owner_run.update(head_sha=ctx.base_sha)
    proofs.review = make_artifact(source,ReviewResult.model_validate(raw['result']))
    original_json = proofs.artifact_json
    def artifact_json(run,attempt,name,member,**kwargs):
        return proofs.review if member=='result.json' else original_json(run,attempt,name,member,**kwargs)
    proofs.artifact_json=artifact_json
    proofs.jobs[(10,1,'review (4) / publish')]=True
    proofs.jobs[(20,1,'Apply owner decision')]=True
    statuses.append(dict(state='success',context=STATUS_CONTEXT,target_url=source.run_url,created_at=(NOW+timedelta(hours=1)).isoformat()))
    monkeypatch.setattr('reviewer.owner_expiry.Actions',lambda api:proofs)
    monkeypatch.setattr('reviewer.owner_expiry.read_journal',lambda *args,**kwargs:records)
    env=dict(GITHUB_REPOSITORY=ctx.repository,GITHUB_RUN_ID='30',GITHUB_RUN_ATTEMPT='1',GITHUB_WORKFLOW_SHA=ctx.base_sha,
        GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='schedule')
    return api,ctx,env,root,records,proofs,statuses,pr


@pytest.mark.parametrize('owner',[False,True])
def test_expired_required_acceptance_invalidates_canonical_published_success(tmp_path,monkeypatch,owner):
    api,ctx,env,root,records,proofs,statuses,pr=setup(tmp_path,monkeypatch,owner=owner)
    assert expire(api,root,4,env,fetcher=lambda *args:ctx.head_sha,now=NOW+timedelta(days=2))=='invalidated'
    assert statuses[0]['state']=='error' and statuses[0]['target_url'].endswith('/runs/30/attempts/1')


@pytest.mark.parametrize('plain',[False,True])
def test_still_valid_acceptance_or_plain_success_is_unchanged(tmp_path,monkeypatch,plain):
    api,ctx,env,root,records,proofs,statuses,pr=setup(tmp_path,monkeypatch,plain=plain)
    assert expire(api,root,4,env,fetcher=lambda *args:ctx.head_sha,now=NOW+timedelta(hours=2))=='unchanged'
    assert len(statuses)==1


def test_changed_latest_generation_is_never_overwritten(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs,statuses,pr=setup(tmp_path,monkeypatch)
    def changed(*args,**kwargs):
        statuses.insert(0,dict(state='pending',context=STATUS_CONTEXT,target_url=ctx.run_url.replace('/10/','/40/')))
        return records
    monkeypatch.setattr('reviewer.owner_expiry.read_journal',changed)
    with pytest.raises(StaleReview): expire(api,root,4,env,fetcher=lambda *args:ctx.head_sha,now=NOW+timedelta(days=2))
    assert len(statuses)==2 and statuses[0]['state']=='pending'


def test_missing_publishing_proof_fails_closed_and_lost_authority_invalidates(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs,statuses,pr=setup(tmp_path,monkeypatch)
    proofs.jobs[(10,1,'review (4) / publish')]=False
    assert expire(api,root,4,env,fetcher=lambda *args:ctx.head_sha,now=NOW+timedelta(hours=2))=='invalidated'
    assert statuses[0]['state']=='error'


def test_lost_authority_before_deadline_invalidates_dependent_success(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs,statuses,pr=setup(tmp_path,monkeypatch)
    records.clear()
    assert expire(api,root,4,env,fetcher=lambda *args:ctx.head_sha,now=NOW+timedelta(hours=2))=='invalidated'
    assert statuses[0]['state']=='error'


def test_queue_overflow_is_error_without_model_or_worker_dispatch(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs,statuses,pr=setup(tmp_path,monkeypatch)
    api.open_pulls=lambda:[pr,{**pr,'number':5}]
    with pytest.raises(ValueError,match='capacity'): select(api,{},env,capacity=1)
    assert statuses[0]['state']=='error'


def test_new_generation_during_status_write_is_detected_without_overwriting_it(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs,statuses,pr=setup(tmp_path,monkeypatch)
    original=api.request
    def request(method,path,body=None,**kwargs):
        result=original(method,path,body,**kwargs)
        if method=='POST' and path.startswith('/statuses/'):
            statuses.insert(0,dict(state='pending',context=STATUS_CONTEXT,target_url=ctx.run_url.replace('/10/','/40/')))
        return result
    api.request=request
    with pytest.raises(StaleReview): expire(api,root,4,env,fetcher=lambda *args:ctx.head_sha,now=NOW+timedelta(days=2))
    assert statuses[0]['state']=='pending' and statuses[0]['target_url'].endswith('/runs/40/attempts/1')
