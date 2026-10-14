from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys

import pytest

from reviewer.decisions import OwnerProposal, effects, fingerprint, propose
from reviewer.models import ReviewResult
from tests.review_debt_fixtures import setup
from tests.test_review_publishing import fixture

NOW = datetime(2030,1,1,tzinfo=timezone.utc)
DEADLINE = (NOW+timedelta(days=1)).isoformat()


def proposal(action,targets,**kwargs):
    return OwnerProposal(action=action,targets=targets,reason='Accepted deliberately for this scope',
                         expires_at=DEADLINE,**kwargs)


def finding_review(tmp_path,**kwargs):
    _,ctx,raw,*_ = fixture(tmp_path,**kwargs)
    return ctx,ReviewResult.model_validate(raw['result']),raw


def test_exact_dismiss_preserves_original_and_reports_independent_blockers(tmp_path):
    ctx,result,_ = finding_review(tmp_path,extra={'app/state/other.py':'import sqlite3\n'})
    original = result.model_dump_json()
    fid = fingerprint(result.findings[0])
    decision = propose(ctx,result,proposal('dismiss',[fid]),'MAINTAINER',now=NOW)
    plan = effects(ctx,result,{'10-1':decision},now=NOW)
    assert plan.state == 'failure' and plan.accepted_findings == {fid:'10-1'}
    assert len(plan.remaining_findings) == len(result.findings)-1
    assert result.model_dump_json() == original and all(f.blocking for f in result.findings)
    all_ids = [fingerprint(f) for f in result.findings]
    all_decision = propose(ctx,result,proposal('dismiss',all_ids),'maintainer',now=NOW)
    assert effects(ctx,result,{'10-2':all_decision},now=NOW).state == 'success'


@pytest.mark.parametrize('field,size',[('head_sha',40),('base_sha',40),('profile_sha256',64)])
def test_dismiss_never_crosses_snapshot_or_profile(tmp_path,field,size):
    ctx,result,_ = finding_review(tmp_path)
    decision = propose(ctx,result,proposal('dismiss',[fingerprint(result.findings[0])]),'maintainer',now=NOW)
    changed = ctx.model_copy(update={field:'0'*size})
    assert effects(changed,result,{'10-1':decision},now=NOW).state == 'failure'


def test_diagnosis_change_same_anchor_is_a_different_finding(tmp_path):
    ctx,result,_ = finding_review(tmp_path)
    decision = propose(ctx,result,proposal('dismiss',[fingerprint(result.findings[0])]),'maintainer',now=NOW)
    changed = result.model_copy(update={'findings':[result.findings[0].model_copy(update={'message':'Different defect at same anchor'})]})
    assert effects(ctx,changed,{'10-1':decision},now=NOW).state == 'failure'


@pytest.mark.parametrize('updates',[{'status':'error'},{'execution':'partial'},{'execution':'failed'}])
def test_incomplete_review_cannot_be_overridden(tmp_path,updates):
    ctx,result,_ = finding_review(tmp_path)
    p = proposal('dismiss',[fingerprint(result.findings[0])])
    decision = propose(ctx,result,p,'maintainer',now=NOW)
    incomplete = result.model_copy(update=updates)
    with pytest.raises(ValueError,match='Incomplete'): propose(ctx,incomplete,p,'maintainer',now=NOW)
    plan = effects(ctx,incomplete,{'10-1':decision},now=NOW)
    assert plan.state == 'error' and not plan.accepted_findings


def test_rule_waiver_is_scoped_time_bounded_and_does_not_waive_debt(tmp_path):
    ctx,result,_ = finding_review(tmp_path)
    rule = result.findings[0].rule_id
    decision = propose(ctx,result,proposal('waive_rule',[rule],scope=['app/state/*.py']),'maintainer',now=NOW)
    assert effects(ctx,result,{'10-1':decision},now=NOW).state == 'success'
    assert effects(ctx,result,{'10-1':decision},now=NOW+timedelta(days=1)).state == 'failure'
    other = result.model_copy(update={'findings':[result.findings[0].model_copy(update={
        'evidence':result.findings[0].evidence.model_copy(update={'path':'app/other.py'})})]})
    assert effects(ctx,other,{'10-1':decision},now=NOW).state == 'failure'
    with pytest.raises(ValueError): propose(ctx,result,proposal('waive_rule',['BUGS'],scope=['**']),'maintainer',now=NOW)
    debt_root = tmp_path/'debt';debt_root.mkdir()
    _,debt_ctx,raw,*_ = setup(debt_root)
    debt = ReviewResult.model_validate(raw['result'])
    waiver = propose(debt_ctx,debt,proposal('waive_rule',['ARCH-STATE-PURE'],scope=['app/state/*.py']),'maintainer',now=NOW)
    assert effects(debt_ctx,debt,{'10-1':waiver},now=NOW).state == 'failure'


def test_debt_waiver_binds_exact_entry(tmp_path):
    _,ctx,raw,*_ = setup(tmp_path)
    result = ReviewResult.model_validate(raw['result'])
    decision = propose(ctx,result,proposal('waive_debt',['state-storage']),'maintainer',now=NOW)
    assert effects(ctx,result,{'10-1':decision},now=NOW).state == 'success'
    entry = ctx.debt_registry.entries[0].model_copy(update={'title':'Changed debt'})
    changed = ctx.model_copy(update={'debt_registry':ctx.debt_registry.model_copy(update={'entries':[entry]})})
    assert effects(changed,result,{'10-1':decision},now=NOW).state == 'failure'


def test_manual_closure_requires_matching_proposed_owner_attestation(tmp_path):
    _,ctx,raw,*_ = setup(tmp_path,closure='owner-attestation')
    result = ReviewResult.model_validate(raw['result'])
    p = proposal('resolve_debt',['state-storage'])
    with pytest.raises(ValueError,match='matching resolved'): propose(ctx,result,p,'maintainer',now=NOW)
    entry = ctx.debt_registry.entries[0].model_copy(update={'status':'resolved','confirmed_by':'maintainer','reason':p.reason})
    proposed = ctx.debt_registry.model_copy(update={'entries':[entry]})
    decision = propose(ctx,result,p,'maintainer',now=NOW,proposed_registry=proposed)
    assert effects(ctx,result,{},now=NOW).state == 'failure'
    assert effects(ctx,result,{'10-1':decision},now=NOW).state == 'success'
    assert effects(ctx.model_copy(update={'head_sha':'0'*40}),result,{'10-1':decision},now=NOW).state == 'failure'


def test_authenticated_revoke_restores_original_failure(tmp_path):
    ctx,result,_ = finding_review(tmp_path)
    decision = propose(ctx,result,proposal('dismiss',[fingerprint(result.findings[0])]),'maintainer',now=NOW)
    revoke = OwnerProposal(action='revoke',targets=['10-1'],reason='Reconsidered')
    revoked = propose(ctx,None,revoke,'maintainer',now=NOW,known_decisions={'10-1':decision})
    assert effects(ctx,result,{'10-1':decision,'11-1':revoked},now=NOW).state == 'failure'
    with pytest.raises(ValueError): propose(ctx,None,revoke,'other-user',now=NOW,known_decisions={'10-1':decision})


@pytest.mark.parametrize('scope',[['../app/*'],['/app/*'],['app/\n*'],['app/../state/*'],[]])
def test_invalid_rule_scope_rejected(scope):
    with pytest.raises(ValueError): proposal('waive_rule',['ARCH-STATE-PURE'],scope=scope)


def test_invalid_owner_expiry_or_unknown_target_rejected(tmp_path):
    ctx,result,_ = finding_review(tmp_path)
    p = proposal('dismiss',[fingerprint(result.findings[0])])
    with pytest.raises(ValueError): propose(ctx,result,p,'other-user',now=NOW)
    for date in (NOW.isoformat(),(NOW+timedelta(days=8)).isoformat()):
        with pytest.raises(ValueError): propose(ctx,result,p.model_copy(update={'expires_at':date}),'maintainer',now=NOW)
    with pytest.raises(ValueError): propose(ctx,result,proposal('dismiss',['0'*64]),'maintainer',now=NOW)
    with pytest.raises(ValueError):
        OwnerProposal(action='waive_rule',targets=['ARCH-STATE-PURE'],scope=['app/*'],reason='reason',
                      expires_at='2030-01-02T00:00:00+03:00')


def test_cli_lists_exact_ids_and_has_no_ci_authority(tmp_path):
    ctx,result,raw = finding_review(tmp_path)
    path = tmp_path/'artifact.json';path.write_text(json.dumps(raw))
    process = subprocess.run([sys.executable,'scripts/preview_review_decision.py','--artifact',str(path)],capture_output=True,text=True)
    assert process.returncode == 0
    output = json.loads(process.stdout)
    assert output['authority'] == 'offline-preview-only'
    assert output['finding_ids'][0]['id'] == fingerprint(result.findings[0])


def test_unknown_failure_and_future_decision_do_not_turn_green(tmp_path):
    ctx,result,_ = finding_review(tmp_path)
    decision = propose(ctx,result,proposal('dismiss',[fingerprint(result.findings[0])]),'maintainer',now=NOW)
    future = decision.model_copy(update={'created_at':(NOW+timedelta(hours=1)).isoformat()})
    assert effects(ctx,result,{'10-1':future},now=NOW).state == 'failure'
    unknown = result.model_copy(update={'findings':[],'criteria':[]})
    assert effects(ctx,unknown,{},now=NOW).state == 'failure'


def test_latest_covering_decision_controls_reason_and_expiry(tmp_path):
    ctx,result,_ = finding_review(tmp_path)
    p = proposal('dismiss',[fingerprint(result.findings[0])])
    early = propose(ctx,result,p.model_copy(update={'expires_at':(NOW+timedelta(hours=1)).isoformat()}),'maintainer',now=NOW)
    later = propose(ctx,result,p,'maintainer',now=NOW)
    plan = effects(ctx,result,{'20-1':early,'100-1':later},now=NOW)
    assert set(plan.accepted_findings.values()) == {'100-1'} and plan.expires_at == DEADLINE


def test_invalid_cli_input_never_echoes_unknown_fields_or_values(tmp_path):
    _,_,raw = finding_review(tmp_path)
    raw['result']['PRIVATE_UNKNOWN_FIELD'] = {'value':'PRIVATE_SENTINEL'}
    path = tmp_path/'artifact.json';path.write_text(json.dumps(raw))
    process = subprocess.run([sys.executable,'scripts/preview_review_decision.py','--artifact',str(path)],capture_output=True,text=True)
    assert process.returncode == 2 and not process.stdout
    assert 'PRIVATE_UNKNOWN_FIELD' not in process.stderr and 'PRIVATE_SENTINEL' not in process.stderr


def test_cli_new_proposal_supersedes_known_decision_for_same_finding(tmp_path):
    ctx,result,raw = finding_review(tmp_path)
    now = datetime.now(timezone.utc)
    fid = fingerprint(result.findings[0])
    old_proposal = OwnerProposal(action='dismiss',targets=[fid],reason='Older',expires_at=(now+timedelta(hours=1)).isoformat())
    known = propose(ctx,result,old_proposal,'maintainer',now=now)
    new = old_proposal.model_copy(update={'reason':'Newer','expires_at':(now+timedelta(days=1)).isoformat()})
    paths = [tmp_path/name for name in ('artifact.json','known.json','proposal.json')]
    for path,value in zip(paths,(raw,{'10-1':known.model_dump()},new.model_dump())):
        path.write_text(json.dumps(value))
    process = subprocess.run([sys.executable,'scripts/preview_review_decision.py','--artifact',str(paths[0]),
        '--known-decisions',str(paths[1]),'--proposal',str(paths[2]),'--actor','maintainer'],capture_output=True,text=True)
    assert process.returncode == 0
    output = json.loads(process.stdout)
    assert output['effects']['accepted_findings'][fid] == output['preview_decision_id'] == '11-1'
    assert output['effects']['expires_at'] == new.expires_at and output['decision']['proposal']['reason'] == 'Newer'
