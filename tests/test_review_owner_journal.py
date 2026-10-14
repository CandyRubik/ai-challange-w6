from copy import deepcopy
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest

from reviewer.decision_records import AnalysisSource,DecisionEnvelope
from reviewer.decisions import OwnerProposal,fingerprint,propose
from reviewer.github import GitHubError
from reviewer.git import Repository
from reviewer.models import ReviewResult
from reviewer.owner_journal import AUTHORIZE_JOB,OWNER_WORKFLOW,REVIEW_WORKFLOW,read_journal,read_review
from reviewer.contracts import digest
from tests.test_review_publishing import fixture

NOW = datetime(2030,1,1,tzinfo=timezone.utc)


class Proofs:
    def __init__(self,ctx,raw):
        self.context,self.review = ctx,raw
        self.owner_run = dict(id=20,run_attempt=1,repository=dict(id=1),workflow_id=101,path=OWNER_WORKFLOW,
            head_sha=ctx.base_sha,head_branch='main',head_repository=dict(id=1),event='workflow_dispatch',
            actor=dict(login='maintainer'),triggering_actor=dict(login='maintainer'),conclusion='failure')
        self.review_run = {**self.owner_run,'id':10,'workflow_id':100,'path':REVIEW_WORKFLOW,
                           'head_sha':ctx.head_sha,'head_branch':'feature','event':'pull_request_target'}
        self.rows = [dict(id=20,run_attempt=1,display_title='Review decision PR #4')]
        proposal = OwnerProposal(action='dismiss',targets=[fingerprint(ReviewResult.model_validate(raw['result']).findings[0])],
            reason='Confirmed false positive',expires_at=(NOW+timedelta(days=1)).isoformat())
        decision = propose(ctx,ReviewResult.model_validate(raw['result']),proposal,'maintainer',now=NOW)
        self.envelope = DecisionEnvelope(schema_version=1,run_id=20,run_attempt=1,code_sha=ctx.base_sha,
            triggering_actor='maintainer',decision=decision,source=AnalysisSource(run_id=10,run_attempt=1,result_sha256=raw['result_sha256'])).model_dump()
        self.jobs = {(10,1,f'review (4) / analyze'):True,(20,1,AUTHORIZE_JOB):True}
        self.api = SimpleNamespace(repository='owner/repo',request=self.request)
        self.calls = []

    def request(self,method,path,**kwargs):
        if path=='': return dict(id=1)
        return dict(id=101 if 'ai-review-owner' in path else 100,path=OWNER_WORKFLOW if 'ai-review-owner' in path else REVIEW_WORKFLOW)

    def run_attempt(self,run,attempt):
        self.calls.append(('attempt',run,attempt))
        return {**deepcopy(self.owner_run if run==20 else self.review_run),'run_attempt':attempt}

    def successful_job(self,run,attempt,name):
        return self.jobs.get((run,attempt,name),False)

    def artifact_json(self,run,attempt,name,member,**kwargs):
        self.calls.append(('artifact',run['id'],attempt))
        return deepcopy(self.envelope if member=='decision.json' else self.review)

    def collection(self,path,key,**kwargs):
        self.calls.append(('collection',kwargs))
        return self.rows


def setup(tmp_path):
    _,ctx,raw,pr,calls,statuses,root = fixture(tmp_path)
    return Proofs(ctx,raw),Repository(root),ctx,raw


def incomplete(actions):
    actions.review['result'].update(status='error',execution='partial')
    actions.review['result_sha256'] = digest(actions.review['result'])


def test_original_pr_target_review_uses_pr_head_not_code_sha(tmp_path):
    actions,repo,ctx,raw = setup(tmp_path)
    assert ctx.head_sha!=ctx.code_sha
    artifact = read_review(actions,repo,10,1,4,context=ctx)
    assert artifact.result_sha256 == raw['result_sha256'] and artifact.result.status=='failure'


@pytest.mark.parametrize('mutate',[
    lambda a: a.review_run.update(path='.github/workflows/foreign.yml'),
    lambda a: a.review_run.update(event='pull_request'),
    lambda a: a.review_run.update(head_sha='0'*40),
    lambda a: a.review_run.update(repository=dict(id=2)),
    lambda a: a.jobs.update({(10,1,'review (4) / analyze'):False}),
    incomplete,
    lambda a: a.review['context'].update(head_sha='0'*40),
])
def test_source_without_canonical_complete_proof_is_rejected(tmp_path,mutate):
    actions,repo,ctx,raw = setup(tmp_path);mutate(actions)
    with pytest.raises(GitHubError): read_review(actions,repo,10,1,4,context=ctx)


def test_manual_original_review_requires_main_and_both_owners(tmp_path):
    actions,repo,ctx,raw = setup(tmp_path)
    actions.review_run.update(event='workflow_dispatch',head_branch='main',head_sha=ctx.base_sha)
    assert read_review(actions,repo,10,1,4,context=ctx).result.status=='failure'
    actions.review_run['triggering_actor']['login'] = 'other-user'
    with pytest.raises(GitHubError): read_review(actions,repo,10,1,4,context=ctx)


def test_authorize_job_proves_decision_even_when_workflow_apply_failed(tmp_path):
    actions,repo,ctx,raw = setup(tmp_path)
    records = read_journal(actions,repo,ctx,now=NOW)
    assert list(records)==['20-1'] and actions.owner_run['conclusion']=='failure'
    assert records['20-1'].decision.proposal.reason=='Confirmed false positive'
    assert actions.calls[0][1]['params']['event']=='workflow_dispatch'


def test_prior_authorization_survives_failed_later_rerun(tmp_path):
    actions,repo,ctx,raw = setup(tmp_path)
    actions.rows[0]['run_attempt'] = 2
    actions.jobs[(20,2,AUTHORIZE_JOB)] = False
    assert list(read_journal(actions,repo,ctx,now=NOW)) == ['20-1']
    assert ('attempt',20,2) in actions.calls and ('artifact',20,2) not in actions.calls


@pytest.mark.parametrize('mutation',[
    dict(event='push'),dict(head_branch='feature'),dict(head_repository=dict(id=2)),
    dict(actor=dict(login='other-user')),dict(triggering_actor=dict(login='other-user')),
])
def test_non_owner_or_non_main_run_never_grants_decision(tmp_path,mutation):
    actions,repo,ctx,raw = setup(tmp_path);actions.owner_run.update(mutation)
    assert read_journal(actions,repo,ctx,now=NOW)=={}


@pytest.mark.parametrize('mutation',[
    lambda a: a.envelope.update(run_id=30),
    lambda a: a.envelope.update(triggering_actor='other-user'),
    lambda a: a.envelope['decision'].update(actor='other-user'),
    lambda a: a.envelope['decision'].update(pr_number=5),
    lambda a: a.envelope['decision'].update(created_at=(NOW+timedelta(hours=1)).isoformat()),
    lambda a: a.envelope['decision'].update(profile_sha256='0'*64),
])
def test_artifact_copy_or_forged_metadata_is_rejected(tmp_path,mutation):
    actions,repo,ctx,raw = setup(tmp_path);mutation(actions)
    with pytest.raises(GitHubError): read_journal(actions,repo,ctx,now=NOW)


def test_unauthorized_job_and_other_pr_title_are_ignored_before_download(tmp_path):
    actions,repo,ctx,raw = setup(tmp_path)
    actions.jobs[(20,1,AUTHORIZE_JOB)] = False
    assert read_journal(actions,repo,ctx,now=NOW)=={}
    assert not any(c[0]=='artifact' for c in actions.calls)
    actions.rows[0]['display_title'] = 'Review decision PR #5'
    actions.calls.clear()
    assert read_journal(actions,repo,ctx,now=NOW)=={} and len(actions.calls)==1


def test_attempt_overflow_fails_closed(tmp_path):
    actions,repo,ctx,raw = setup(tmp_path);actions.rows[0]['run_attempt'] = 21
    with pytest.raises(GitHubError): read_journal(actions,repo,ctx,now=NOW)
