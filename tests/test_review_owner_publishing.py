from copy import deepcopy
from datetime import timedelta

import pytest

from reviewer.debt_publishing import ASSESSMENT_MARKER,DEBT_MARKER,OWNER_DEBT_MARKER
from reviewer.decisions import OwnerProposal,propose
from reviewer.models import ReviewResult
from reviewer.owner_publishing import publish_owned,record_digest
from reviewer.decision_records import DecisionEnvelope
from reviewer.owner_report import OWNER_MARKER
from reviewer.publishing import SUMMARY_MARKER,StaleReview,make_artifact,start
from tests.test_review_inline_publishing import setup as inline_setup
from tests.test_review_owner_journal import NOW,Proofs
from tests.review_debt_fixtures import setup as debt_setup


def setup(tmp_path,monkeypatch):
    api,ctx,result,repo,files,inline,reviews,writes,guard,pr = inline_setup(tmp_path)
    raw = make_artifact(ctx,result)
    proofs = Proofs(ctx,raw)
    monkeypatch.setattr('reviewer.owner_publishing.Actions',lambda api:proofs)
    issues = []
    original = api.request
    def request(method,path,body=None,**kwargs):
        if path=='/issues/4/comments':
            if method=='GET': return issues
            issue = dict(id=len(issues)+1,user=dict(login='github-actions[bot]',type='Bot'),**body)
            issues.append(issue);return issue
        if method=='PATCH' and path.startswith('/issues/comments/'):
            issue = next(c for c in issues if str(c['id'])==path.rsplit('/',1)[1])
            issue.update(body);return issue
        return original(method,path,body,**kwargs)
    api.request = request
    return api,ctx,raw,repo,proofs,issues,inline,pr


def test_authenticated_acceptance_changes_only_effective_check_and_preserves_inline(tmp_path,monkeypatch):
    api,ctx,raw,repo,proofs,issues,inline,pr = setup(tmp_path,monkeypatch)
    original = deepcopy(raw)
    assert publish_owned(api,ctx,raw,repo,clock=lambda:NOW)=='success'
    assert raw==original and raw['result']['status']=='failure' and raw['result']['findings'][0]['blocking']
    assert len(issues)==2 and len(inline)==1
    summary = next(c['body'] for c in issues if c['body'].startswith(SUMMARY_MARKER))
    assert 'Итог проверки: **success**; исходный анализ: **failure**' in summary
    assert '20-1' in inline[0]['body'] and '--- Original report ---' in inline[0]['body']
    assert raw['result']['findings'][0]['message'] in inline[0]['body']
    # A deleted display comment does not revoke the Actions artifact.
    issues[:] = [c for c in issues if not c['body'].startswith(OWNER_MARKER)]
    assert publish_owned(api,ctx,raw,repo,clock=lambda:NOW)=='success'
    assert len(issues)==2 and len(inline)==1


def test_expired_decision_restores_failure_and_removes_current_inline_acceptance(tmp_path,monkeypatch):
    api,ctx,raw,repo,proofs,issues,inline,pr = setup(tmp_path,monkeypatch)
    assert publish_owned(api,ctx,raw,repo,clock=lambda:NOW)=='success'
    assert publish_owned(api,ctx,raw,repo,clock=lambda:NOW+timedelta(days=1))=='failure'
    assert 'Принятие владельцем' not in inline[0]['body'] and raw['result']['findings'][0]['message'] in inline[0]['body']
    assert len(issues)==2 and len(inline)==1


def test_captured_journal_digest_cannot_grant_authority(tmp_path,monkeypatch):
    api,ctx,raw,repo,proofs,issues,inline,pr = setup(tmp_path,monkeypatch)
    with pytest.raises(StaleReview,match='journal changed'):
        publish_owned(api,ctx,raw,repo,expected='0'*64,clock=lambda:NOW)
    assert not issues and not inline
    proofs.jobs[(20,1,'Authorize owner decision')] = False
    assert publish_owned(api,ctx,raw,repo,clock=lambda:NOW)=='failure'
    assert not any(c['body'].startswith(OWNER_MARKER) for c in issues)


@pytest.mark.parametrize('change',['expiry','journal','head'])
def test_change_during_write_invalidates_own_status(tmp_path,monkeypatch,change):
    api,ctx,raw,repo,proofs,issues,inline,pr = setup(tmp_path,monkeypatch)
    moment = [NOW]
    original = api.request
    def request(method,path,body=None,**kwargs):
        response = original(method,path,body,**kwargs)
        if method=='POST' and path=='/issues/4/comments':
            if change=='expiry': moment[0]+=timedelta(days=1)
            elif change=='journal': proofs.jobs[(20,1,'Authorize owner decision')] = False
            else: pr['head']['sha']='0'*40
        return response
    api.request = request
    with pytest.raises(StaleReview): publish_owned(api,ctx,raw,repo,clock=lambda:moment[0])
    status = api.items(f'/commits/{ctx.head_sha}/statuses')[0]
    assert status['state']=='error' and not inline


def test_owner_cannot_turn_incomplete_original_into_success(tmp_path,monkeypatch):
    api,ctx,raw,repo,proofs,issues,inline,pr = setup(tmp_path,monkeypatch)
    result = ReviewResult.model_validate(raw['result']).model_copy(update={'status':'error','execution':'partial'})
    assert publish_owned(api,ctx,make_artifact(ctx,result),repo,clock=lambda:NOW)=='error'
    assert not any('Принятие владельцем' in c['body'] for c in inline)


def test_debt_acceptance_is_separate_from_retained_original_assessment(tmp_path,monkeypatch):
    api,ctx,raw,repo,issues,statuses,calls = debt_setup(tmp_path)
    p = OwnerProposal(action='waive_debt',targets=['state-storage'],scope=['app/state/*.py'],reason='Bounded migration',
        expires_at=(NOW+timedelta(days=1)).isoformat())
    decision = propose(ctx,ReviewResult.model_validate(raw['result']),p,'maintainer',now=NOW)
    proofs = Proofs(ctx,raw,decision=decision)
    monkeypatch.setattr('reviewer.owner_publishing.Actions',lambda api:proofs)
    start(api,ctx)
    assert publish_owned(api,ctx,raw,repo,clock=lambda:NOW)=='success'
    body = next(c['body'] for c in issues if c['body'].startswith(DEBT_MARKER))
    assert 'open' in body and 'Принятие владельцем' in body
    original = body.partition(ASSESSMENT_MARKER)[2].partition(OWNER_DEBT_MARKER)[0].strip()
    partial = ReviewResult.model_validate(raw['result']).model_copy(update={'status':'error','execution':'partial'})
    assert publish_owned(api,ctx,make_artifact(ctx,partial),repo,clock=lambda:NOW)=='error'
    body = next(c['body'] for c in issues if c['body'].startswith(DEBT_MARKER))
    assert body.partition(ASSESSMENT_MARKER)[2].strip()==original and 'Принятие владельцем' not in body


def test_oversized_inline_note_keeps_original_and_uses_summary_fallback(tmp_path,monkeypatch):
    from reviewer.inline_publishing import publish_inline
    api,ctx,result,repo,files,inline,reviews,writes,guard,pr = inline_setup(tmp_path)
    fallback = publish_inline(api,ctx,result,repo,guard=guard,owner_note=lambda findings:'Ж'*40_000)
    assert fallback and 'Примечание владельца' in fallback[0]
    assert '--- Original report ---' not in inline[0]['body'] and result.findings[0].message in inline[0]['body']


def test_early_authority_failure_invalidates_previous_own_success(tmp_path,monkeypatch):
    from reviewer.github import GitHubError
    api,ctx,raw,repo,proofs,issues,inline,pr = setup(tmp_path,monkeypatch)
    stamp = record_digest({'20-1':DecisionEnvelope.model_validate(proofs.envelope)})
    assert publish_owned(api,ctx,raw,repo,expected=stamp,clock=lambda:NOW)=='success'
    proofs.jobs[(20,1,'Authorize owner decision')] = False
    with pytest.raises(StaleReview): publish_owned(api,ctx,raw,repo,expected=stamp,clock=lambda:NOW)
    assert api.items(f'/commits/{ctx.head_sha}/statuses')[0]['state']=='error'

    def unavailable(*args,**kwargs): raise GitHubError('Journal temporarily unavailable')
    monkeypatch.setattr('reviewer.owner_publishing.read_journal',unavailable)
    with pytest.raises(GitHubError): publish_owned(api,ctx,raw,repo,clock=lambda:NOW)
    assert api.items(f'/commits/{ctx.head_sha}/statuses')[0]['state']=='error'


def test_new_authority_during_empty_journal_publication_requires_retry(tmp_path,monkeypatch):
    api,ctx,raw,repo,proofs,issues,inline,pr = setup(tmp_path,monkeypatch)
    rows = proofs.rows
    proofs.rows = []
    original = api.request
    def request(method,path,body=None,**kwargs):
        response = original(method,path,body,**kwargs)
        if method=='POST' and path=='/issues/4/comments': proofs.rows=rows
        return response
    api.request = request
    with pytest.raises(StaleReview): publish_owned(api,ctx,raw,repo,clock=lambda:NOW)
    assert api.items(f'/commits/{ctx.head_sha}/statuses')[0]['state']=='error'


def test_debt_lifecycle_value_error_inside_owner_guard_invalidates_success(tmp_path,monkeypatch):
    from reviewer.debt_publishing import validate_debt
    api,ctx,raw,repo,proofs,issues,inline,pr = setup(tmp_path,monkeypatch)
    assert publish_owned(api,ctx,raw,repo,clock=lambda:NOW)=='success'
    original = api.request
    expired = [False]
    def request(method,path,body=None,**kwargs):
        response = original(method,path,body,**kwargs)
        if method=='PATCH' and path.startswith('/issues/comments/'): expired[0]=True
        return response
    def lifecycle(context,result):
        if expired[0]: raise ValueError('Debt lifecycle expired')
        return validate_debt(context,result)
    api.request = request
    monkeypatch.setattr('reviewer.debt_publishing.validate_debt',lifecycle)
    with pytest.raises(StaleReview): publish_owned(api,ctx,raw,repo,clock=lambda:NOW)
    assert api.items(f'/commits/{ctx.head_sha}/statuses')[0]['state']=='error'


def test_empty_authority_updates_existing_journal_and_keeps_it_unique(tmp_path,monkeypatch):
    api,ctx,raw,repo,proofs,issues,inline,pr = setup(tmp_path,monkeypatch)
    assert publish_owned(api,ctx,raw,repo,clock=lambda:NOW)=='success'
    proofs.rows = []
    assert publish_owned(api,ctx,raw,repo,clock=lambda:NOW)=='failure'
    journals = [c for c in issues if c['body'].startswith(OWNER_MARKER)]
    assert len(journals)==1 and 'Итог проверки: **failure**' in journals[0]['body']
    assert 'Нет актуальных подтверждённых решений владельца.' in journals[0]['body']


def test_incomplete_original_is_error_even_with_empty_owner_journal(tmp_path,monkeypatch):
    from reviewer.git import Repository
    from tests.test_review_publishing import fixture
    from tests.test_reviewer import PROFILE
    profile = deepcopy(PROFILE);profile['rules'][0]['level']='should'
    api,ctx,raw,pr,calls,statuses,root = fixture(tmp_path,profile=profile)
    result = ReviewResult.model_validate(raw['result'])
    criteria = [c.model_copy(update={'verdict':'inconclusive','reason':'Advisory context unavailable'})
                if c.rule_id=='ARCH-STATE-PURE' else c for c in result.criteria]
    partial = result.model_copy(update={'findings':[],'criteria':criteria,'status':'success','execution':'partial'})
    monkeypatch.setattr('reviewer.owner_publishing.read_journal',lambda *args,**kwargs:{})
    start(api,ctx)
    assert publish_owned(api,ctx,make_artifact(ctx,partial),Repository(root),clock=lambda:NOW)=='error'
    assert statuses[0]['state']=='error'
