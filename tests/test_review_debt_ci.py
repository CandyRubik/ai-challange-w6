from copy import deepcopy
import pytest
from reviewer.ci import analyze_snapshot, prepare_snapshot, validate_proposed_profile
from reviewer.publishing import StaleReview, digest, preview, publish, start
from tests.review_debt_fixtures import setup
from tests.test_review_ci import Pass
from tests.test_reviewer import commit, git


@pytest.mark.parametrize('tamper', ['missing', 'duplicate', 'hash', 'blocking', 'head', 'success'])
def test_forged_debt_coverage_or_success_is_rejected_before_writes(tmp_path, tamper):
    api, context, artifact, repo, comments, statuses, calls = setup(tmp_path)
    raw = deepcopy(artifact)
    if tamper == 'missing': raw['result']['project_debt'] = []
    elif tamper == 'duplicate': raw['result']['project_debt'] *= 2
    elif tamper == 'hash': raw['result']['debt_sha256'] = '0'*64
    elif tamper == 'blocking': raw['result']['project_debt'][0]['blocking'] = False
    elif tamper == 'head': raw['result']['project_debt'][0]['evidence'][0]['sha'] = context.base_sha
    else: raw['result']['status'] = 'success'
    raw['result_sha256'] = digest(raw['result'])
    with pytest.raises(ValueError): publish(api,context,raw,repository=repo)
    assert not comments and not statuses


def test_claimed_fix_is_recomputed_from_actual_git_objects(tmp_path):
    api, context, artifact, repo, comments, statuses, calls = setup(tmp_path)
    raw = deepcopy(artifact)
    item = raw['result']['project_debt'][0]
    item.update(verdict='fixed', evidence=[], violation_count=0, checked_paths=[])
    raw['result']['status'] = 'success'
    raw['result_sha256'] = digest(raw['result'])
    with pytest.raises(ValueError,match='actual pinned head'): publish(api,context,raw,repository=repo)
    assert not comments and not statuses


def test_proposed_registry_integrity_is_checked(tmp_path):
    api,context,artifact,repo,*_ = setup(tmp_path)
    bad = commit(repo.root,{'.ai-review/debt.json':'{}'})
    with pytest.raises(ValueError): validate_proposed_profile(repo,bad)


def test_waiver_expiry_invalidates_saved_success(tmp_path):
    api, context, artifact, repo, comments, statuses, calls = setup(tmp_path,status='waived',expires_at='2020-01-01T00:00:00Z')
    raw = deepcopy(artifact)
    raw['result']['status'] = 'success'
    raw['result']['project_debt'][0]['verdict'] = 'waived'
    raw['result_sha256'] = digest(raw['result'])
    with pytest.raises(ValueError,match='lifecycle or expiry'): preview(context,raw)


@pytest.mark.parametrize('case,state,verdict', [('active','failure','open'),('fixed','success','fixed'),('incomplete','error','inconclusive')])
def test_trusted_ci_debt_lifecycle(tmp_path,monkeypatch,case,state,verdict):
    api, old, artifact, repo, comments, statuses, calls = setup(tmp_path,fixed=case=='fixed')
    if case == 'incomplete':
        def unknown(*args): raise ValueError('scope unavailable')
        monkeypatch.setattr('reviewer.debt.scan',unknown)
    git(repo.root,'checkout','--detach',old.base_sha)
    env = dict(GITHUB_REPOSITORY='owner/repo',GITHUB_RUN_ID='10',GITHUB_RUN_ATTEMPT='1',
        GITHUB_WORKFLOW_SHA=old.base_sha,GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='pull_request_target',
        GITHUB_ACTOR='author',GITHUB_TRIGGERING_ACTOR='author')
    context = prepare_snapshot(api,repo.root,4,env,fetcher=lambda *args:old.head_sha)
    result = analyze_snapshot(api,repo.root,context,env,model_factory=Pass,fetcher=lambda *args:old.head_sha)
    assert result['result']['findings'] == []
    assert preview(context,result)['state'] == state
    assert result['result']['project_debt'][0]['verdict'] == verdict
    assert publish(api,context,result,repository=repo) == state
    assert len(comments) == 2 and all('Project debt' in c['body'] or 'Долг проекта' in c['body'] for c in comments)


def test_nonempty_registry_needs_actual_git_objects_for_publication(tmp_path):
    api,context,artifact,repo,comments,statuses,calls = setup(tmp_path)
    with pytest.raises(ValueError,match='pinned Git objects'): publish(api,context,artifact)
    assert not comments and not statuses


@pytest.mark.parametrize('point',['summary','status'])
def test_waiver_expiring_during_publication_invalidates_own_status(tmp_path,monkeypatch,point):
    from datetime import datetime,timedelta,timezone
    deadline = datetime(2099,1,1,tzinfo=timezone.utc)
    clock = [deadline-timedelta(seconds=1)]
    class Clock:
        @classmethod
        def now(cls,tz): return clock[0]
    api,context,artifact,repo,comments,statuses,calls = setup(tmp_path,status='waived',expires_at='2099-01-01T00:00:00Z')
    monkeypatch.setattr('reviewer.debt_publishing.datetime',Clock)
    original = api.request
    def racing(method,path,body=None,**kwargs):
        response = original(method,path,body,**kwargs)
        if method=='POST' and (point=='summary' and path.endswith('/comments')
            or point=='status' and path.startswith('/statuses/') and body['state']=='success'):
            clock[0] = deadline+timedelta(seconds=1)
        return response
    api.request = racing
    start(api,context)
    with pytest.raises(StaleReview,match='lifecycle'): publish(api,context,artifact,repository=repo)
    assert statuses[0]['state'] == 'error'


