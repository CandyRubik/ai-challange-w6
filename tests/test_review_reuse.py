from copy import deepcopy

import pytest

from reviewer.ci import analyze_snapshot,check_context,prepare_snapshot
from reviewer.contracts import ReviewArtifact
from reviewer.github import GitHubError
from reviewer.reuse import reuse_result,source_inputs
from tests.test_review_owner_journal import Proofs
from tests.test_review_publishing import fixture


def reused(tmp_path,monkeypatch):
    api,old,raw,pr,calls,statuses,root = fixture(tmp_path)
    from tests.test_reviewer import git
    git(root,'checkout','--detach',old.base_sha)
    env = dict(GITHUB_REPOSITORY='owner/repo',GITHUB_RUN_ID='20',GITHUB_RUN_ATTEMPT='1',
        GITHUB_WORKFLOW_SHA=old.base_sha,GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='workflow_dispatch',
        GITHUB_ACTOR='maintainer',GITHUB_TRIGGERING_ACTOR='maintainer',SOURCE_REVIEW_RUN='10',SOURCE_REVIEW_ATTEMPT='1')
    proofs = Proofs(old,raw)
    monkeypatch.setattr('reviewer.reuse.Actions',lambda api:proofs)
    context = prepare_snapshot(api,root,4,env,fetcher=lambda *args:old.head_sha)
    return api,context,env,raw,proofs,root


def test_manual_reuse_preserves_original_with_zero_new_model_calls(tmp_path,monkeypatch):
    api,ctx,env,raw,proofs,root = reused(tmp_path,monkeypatch)
    original = deepcopy(raw)
    def forbidden(): raise AssertionError('No model construction')
    artifact = analyze_snapshot(api,root,ctx,env,model_factory=forbidden,fetcher=lambda *args:ctx.head_sha)
    result = artifact['result']
    assert result['status']=='failure' and result['execution']=='complete'
    for key in ('findings','criteria','policy'):
        assert result[key]==raw['result'][key]
    assert result['trace']['model_calls']==result['trace']['api_calls']==0
    assert result['trace']['wire_inputs']==[] and result['trace']['original_analysis']==raw['result']['trace']
    assert raw==original and ctx.analysis_source.result_sha256==raw['result_sha256']
    assert ReviewArtifact.model_validate(artifact).context==ctx


@pytest.mark.parametrize('changed',['run','attempt','removed'])
def test_context_source_must_match_manual_request(tmp_path,monkeypatch,changed):
    api,ctx,env,raw,proofs,root = reused(tmp_path,monkeypatch)
    if changed=='run': env['SOURCE_REVIEW_RUN']='11'
    elif changed=='attempt': env['SOURCE_REVIEW_ATTEMPT']='2'
    else: ctx=ctx.model_copy(update={'analysis_source':None,'analysis_origin':None})
    with pytest.raises(ValueError): check_context(root,ctx,env)


def test_source_digest_change_after_prepare_fails_closed(tmp_path,monkeypatch):
    api,ctx,env,raw,proofs,root = reused(tmp_path,monkeypatch)
    from reviewer.contracts import digest
    proofs.review['result']['recommendations']=['Changed source result']
    proofs.review['result_sha256']=digest(proofs.review['result'])
    def forbidden(): raise AssertionError('No fallback model')
    with pytest.raises(ValueError,match='digest changed'):
        analyze_snapshot(api,root,ctx,env,model_factory=forbidden,fetcher=lambda *args:ctx.head_sha)


def test_unavailable_source_after_prepare_does_not_fallback_to_paid_model(tmp_path,monkeypatch):
    api,ctx,env,raw,proofs,root = reused(tmp_path,monkeypatch)
    proofs.jobs[(10,1,'review (4) / analyze')] = False
    with pytest.raises(GitHubError): analyze_snapshot(api,root,ctx,env,model_factory=lambda:None,fetcher=lambda *args:ctx.head_sha)


def test_repeated_reuse_does_not_nest_original_trace(tmp_path,monkeypatch):
    api,ctx,env,raw,proofs,root = reused(tmp_path,monkeypatch)
    from reviewer.git import Repository
    first = reuse_result(api,Repository(root),ctx)
    from reviewer.artifacts import make_artifact
    proofs.review = make_artifact(ctx,first)
    proofs.owner_run.update(id=20,workflow_id=100,path=proofs.review_run['path'],head_sha=ctx.base_sha)
    proofs.jobs[(20,1,'review (4) / analyze')] = True
    second_ctx = ctx.model_copy(update={'run_id':30,'analysis_source':ctx.analysis_source.model_copy(
        update={'run_id':20,'result_sha256':proofs.review['result_sha256']})})
    second = reuse_result(api,Repository(root),second_ctx)
    assert second.trace['original_analysis']==first.trace['original_analysis']
    assert 'original_analysis' not in second.trace['original_analysis'] and second.trace['model_calls']==0


@pytest.mark.parametrize('env',[
    dict(GITHUB_EVENT_NAME='pull_request_target',SOURCE_REVIEW_RUN='10'),
    dict(GITHUB_EVENT_NAME='workflow_dispatch',SOURCE_REVIEW_RUN='PRIVATE_VALUE'),
    dict(GITHUB_EVENT_NAME='workflow_dispatch',SOURCE_REVIEW_RUN='10',SOURCE_REVIEW_ATTEMPT='21'),
])
def test_invalid_or_non_manual_source_is_rejected_before_api(env):
    with pytest.raises(ValueError) as error: source_inputs(env)
    assert 'PRIVATE_VALUE' not in str(error.value)
