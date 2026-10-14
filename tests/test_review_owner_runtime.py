from copy import deepcopy

import pytest

from reviewer.ci import prepare_snapshot
from reviewer.decision_records import DecisionEnvelope
from reviewer.github import GitHubError
from reviewer.owner_runtime import assert_owners,capture_owners,check_owners,stamp
from reviewer.reuse import reuse_result
from reviewer.publishing import StaleReview
from reviewer.git import Repository
from tests.test_review_ci import setup
from tests.test_review_owner_journal import Proofs


def captured(tmp_path,monkeypatch,*,fresh=False):
    api,old,env,pr,calls,statuses,root = setup(tmp_path)
    from tests.test_review_publishing import make_artifact
    from reviewer import review
    from tests.test_review_ci import Pass
    raw = make_artifact(old,review(root,old.base_sha,old.head_sha,model=Pass()))
    proofs = Proofs(old,raw)
    records = {'20-1':DecisionEnvelope.model_validate(proofs.envelope)}
    monkeypatch.setattr('reviewer.owner_runtime.enabled',lambda *args:True)
    monkeypatch.setattr('reviewer.owner_runtime.read_journal',lambda *args,**kwargs:deepcopy(records))
    monkeypatch.setattr('reviewer.reuse.Actions',lambda api:proofs)
    env.update(GITHUB_EVENT_NAME='workflow_dispatch',REVIEW_FRESH='true' if fresh else 'false')
    ctx = prepare_snapshot(api,root,4,env,fetcher=lambda *args:old.head_sha)
    ctx = capture_owners(api,Repository(root),ctx,env)
    return api,ctx,env,root,records,proofs


def test_owner_journal_selects_same_snapshot_source_and_analyzes_without_model(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs = captured(tmp_path,monkeypatch)
    assert ctx.analysis_origin=='owner_journal' and ctx.owner_sha256==stamp(records)
    check_owners(Repository(root),ctx,env)
    assert_owners(api,Repository(root),ctx)
    result = reuse_result(api,Repository(root),ctx)
    assert result.trace['model_calls']==0 and [f.model_dump() for f in result.findings]==proofs.review['result']['findings']


def test_explicit_fresh_request_skips_automatic_cache(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs = captured(tmp_path,monkeypatch,fresh=True)
    assert ctx.analysis_source is None and ctx.analysis_origin is None and ctx.owner_records


@pytest.mark.parametrize('change',['stamp','id','source','fresh'])
def test_captured_authority_or_source_tampering_is_rejected(tmp_path,monkeypatch,change):
    api,ctx,env,root,records,proofs = captured(tmp_path,monkeypatch)
    if change=='stamp': ctx=ctx.model_copy(update={'owner_sha256':'0'*64})
    elif change=='id': ctx=ctx.model_copy(update={'owner_records':{'21-1':records['20-1']},'owner_sha256':stamp({'21-1':records['20-1']})})
    elif change=='source': ctx=ctx.model_copy(update={'analysis_source':ctx.analysis_source.model_copy(update={'run_id':11})})
    else: env['REVIEW_FRESH']='true'
    with pytest.raises(ValueError): check_owners(Repository(root),ctx,env)


def test_changed_journal_or_missing_source_never_falls_back_to_model(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs = captured(tmp_path,monkeypatch)
    records.clear()
    with pytest.raises(StaleReview): assert_owners(api,Repository(root),ctx)
    records['20-1']=DecisionEnvelope.model_validate(proofs.envelope)
    proofs.jobs[(10,1,'review (4) / analyze')] = False
    with pytest.raises(GitHubError): reuse_result(api,Repository(root),ctx)


def test_other_snapshot_uses_fresh_analysis_and_manual_source_has_priority(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs = captured(tmp_path,monkeypatch)
    clean = ctx.model_copy(update={'analysis_source':None,'analysis_origin':None})
    records['20-1']=records['20-1'].model_copy(update={'decision':records['20-1'].decision.model_copy(update={'head_sha':'0'*40})})
    assert capture_owners(api,Repository(root),clean,env).analysis_source is None
    manual = ctx.model_copy(update={'analysis_origin':'manual'})
    assert capture_owners(api,Repository(root),manual,env).analysis_origin=='manual'


def test_absent_owner_workflow_cannot_accept_forged_authority(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs = captured(tmp_path,monkeypatch)
    monkeypatch.setattr('reviewer.owner_runtime.enabled',lambda *args:False)
    with pytest.raises(ValueError,match='unavailable'): check_owners(Repository(root),ctx,env)
