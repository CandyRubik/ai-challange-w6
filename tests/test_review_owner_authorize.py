from datetime import timedelta

import pytest

from reviewer.decision_records import DecisionEnvelope
from reviewer.decisions import fingerprint
from reviewer.models import ReviewResult
from reviewer.owner_authorize import authorize,number
from tests.test_review_ci import setup
from tests.test_review_owner_journal import NOW,Proofs
from reviewer import review
from reviewer.publishing import make_artifact
from tests.test_review_ci import Pass


def request(tmp_path,monkeypatch):
    api,old,env,pr,calls,statuses,root = setup(tmp_path)
    raw = make_artifact(old,review(root,old.base_sha,old.head_sha,model=Pass()))
    proofs = Proofs(old,raw)
    proofs.review_run['run_started_at']=(NOW-timedelta(hours=1)).isoformat()
    monkeypatch.setattr('reviewer.owner_authorize.Actions',lambda api:proofs)
    monkeypatch.setattr('reviewer.owner_authorize.read_journal',lambda *args,**kwargs:{})
    env.update(GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_RUN_ID='20')
    finding = fingerprint(ReviewResult.model_validate(raw['result']).findings[0])
    inputs = dict(pr='4',action='dismiss',targets='["'+finding+'"]',scope='[]',reason='Confirmed false positive',review_run='10')
    return api,env,inputs,root,old,proofs,statuses


def test_authorized_dispatch_binds_source_and_marks_pending_after_validation(tmp_path,monkeypatch):
    api,env,inputs,root,old,proofs,statuses = request(tmp_path,monkeypatch)
    record = authorize(api,root,env,inputs,fetcher=lambda *args:old.head_sha,now=NOW)
    assert record.id=='20-1' and record.source.run_id==10 and record.source.result_sha256==proofs.review['result_sha256']
    assert record.decision.proposal.expires_at==(NOW+timedelta(days=1)).isoformat()
    assert statuses[0]['state']=='pending' and statuses[0]['target_url'].endswith('/runs/20/attempts/1')


@pytest.mark.parametrize('mutate',[
    lambda e,i,p:i.update(pr='04'),lambda e,i,p:e.update(GITHUB_ACTOR='outsider'),
    lambda e,i,p:e.update(GITHUB_TRIGGERING_ACTOR='outsider'),lambda e,i,p:i.update(targets='["unknown"]'),
    lambda e,i,p:i.update(expires_at=(NOW+timedelta(days=8)).isoformat()),
    lambda e,i,p:p.review_run.update(run_started_at=(NOW-timedelta(days=8)).isoformat()),
    lambda e,i,p:p.owner_run.update(created_at=(NOW-timedelta(days=8)).isoformat()),
    lambda e,i,p:p.owner_run.update(head_branch='feature'),
])
def test_invalid_authorization_never_claims_status(tmp_path,monkeypatch,mutate):
    api,env,inputs,root,old,proofs,statuses = request(tmp_path,monkeypatch)
    mutate(env,inputs,proofs)
    with pytest.raises((ValueError,RuntimeError)):
        authorize(api,root,env,inputs,fetcher=lambda *args:old.head_sha,now=NOW)
    assert not statuses


def test_revoke_binds_existing_decision_and_omits_source(tmp_path,monkeypatch):
    api,env,inputs,root,old,proofs,statuses = request(tmp_path,monkeypatch)
    previous = DecisionEnvelope.model_validate(proofs.envelope)
    monkeypatch.setattr('reviewer.owner_authorize.read_journal',lambda *args,**kwargs:{'20-1':previous})
    original = proofs.run_attempt
    proofs.run_attempt=lambda run,attempt:{**proofs.owner_run,'id':30} if run==30 else original(run,attempt)
    env['GITHUB_RUN_ID']='30'
    inputs.update(action='revoke',targets='["20-1"]',review_run='')
    def no_head_registry(*args): raise AssertionError('Revocation does not depend on proposed registry validity')
    monkeypatch.setattr('reviewer.owner_authorize.load_registry',no_head_registry)
    record = authorize(api,root,env,inputs,fetcher=lambda *args:old.head_sha,now=NOW)
    assert record.source is None and record.decision.bindings['20-1']==fingerprint(previous.decision)
    assert record.decision.proposal.expires_at is None


@pytest.mark.parametrize('raw',['0','04','-1','4/5','４',None])
def test_noncanonical_numbers_are_rejected(raw):
    with pytest.raises(ValueError): number(raw)
