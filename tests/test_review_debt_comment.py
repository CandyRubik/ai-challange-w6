from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from reviewer.debt_publishing import ASSESSMENT_MARKER, DEBT_MARKER
from reviewer.ci import mark_unfinished
from reviewer.github import GitHubError, UncertainCommentPost, UncertainSummaryPost
from reviewer.git import Repository
from reviewer.models import ReviewResult
from reviewer.publishing import SUMMARY_MARKER, StaleReview, make_artifact, publish, start
from tests.review_debt_fixtures import setup
from tests.test_review_publishing import fixture


def test_one_project_comment_updates_independently_of_summary_and_human_marker(tmp_path):
    api,ctx,raw,repo,comments,statuses,calls = setup(tmp_path)
    human = dict(id=100,body=DEBT_MARKER+' human note',user=dict(login='owner',type='User'))
    comments.append(human)
    start(api,ctx)
    assert publish(api,ctx,raw,repository=repo) == 'failure'
    general = next(c for c in comments if c['id'] != 100 and c['body'].startswith(DEBT_MARKER))
    original_id = general['id']
    assert 'open' in general['body'] and ctx.head_sha in general['body']
    assert 'Owner: maintainer' in general['body'] and len(comments) == 3
    assert publish(api,ctx,raw,repository=repo) == 'failure'
    assert len(comments) == 3 and general['id'] == original_id
    assert human['body'] == DEBT_MARKER+' human note'
    assert not any(method != 'GET' and '/reviews' in path for method,path,_ in calls)


@pytest.mark.parametrize('fixed,status',[(False,'candidate'),(True,'active')])
def test_candidate_and_proved_fix_do_not_block(tmp_path,fixed,status):
    api,ctx,raw,repo,comments,statuses,calls = setup(tmp_path,fixed=fixed,status=status)
    start(api,ctx)
    assert publish(api,ctx,raw,repository=repo) == 'success'
    assert ('fixed' if fixed else 'candidate') in comments[0]['body']
    assert statuses[0]['state'] == 'success'


def test_empty_registry_updates_prior_project_comment(tmp_path):
    api,ctx,raw,pr,calls,statuses,root = fixture(tmp_path,code='pass\n')
    api.request('POST','/issues/4/comments',{'body':DEBT_MARKER+'\nold debt'})
    start(api,ctx)
    assert publish(api,ctx,raw,repository=Repository(root)) == 'success'
    comments = api.items('/issues/4/comments')
    assert 'contains no project debt entries' in comments[0]['body']
    assert 'old debt' not in comments[0]['body']


def test_incomplete_review_retains_prior_assessment_without_nested_banners(tmp_path):
    api,ctx,raw,repo,comments,statuses,calls = setup(tmp_path)
    start(api,ctx)
    publish(api,ctx,raw,repository=repo)
    old = comments[0]['body'].partition(ASSESSMENT_MARKER)[2].strip()
    result = ReviewResult.model_validate(raw['result']).model_copy(update={'status':'error','execution':'partial'})
    incomplete = make_artifact(ctx,result)
    for _ in range(2):
        assert publish(api,ctx,incomplete,repository=repo) == 'error'
        assert comments[0]['body'].partition(ASSESSMENT_MARKER)[2].strip() == old
        assert comments[0]['body'].count('Review incomplete;') == 1
    assert len(comments) == 2 and statuses[0]['state'] == 'error'


@pytest.mark.parametrize('accepted',[True,False])
def test_uncertain_debt_post_never_blindly_repeats_or_disables_error_summary(tmp_path,accepted):
    api,ctx,raw,repo,comments,statuses,calls = setup(tmp_path)
    original,posts = api.request,[]
    def uncertain(method,path,body=None,**kwargs):
        if method == 'POST' and path.endswith('/comments') and body['body'].startswith(DEBT_MARKER):
            posts.append(body)
            if accepted: original(method,path,body,**kwargs)
            raise GitHubError('Unknown transport outcome')
        return original(method,path,body,**kwargs)
    api.request = uncertain
    start(api,ctx)
    if accepted:
        assert publish(api,ctx,raw,repository=repo) == 'failure'
        assert len(comments) == 2
    else:
        with pytest.raises(UncertainCommentPost) as error:
            publish(api,ctx,raw,repository=repo)
        assert not isinstance(error.value,UncertainSummaryPost)
        assert statuses[0]['state'] == 'error'
        env = dict(GITHUB_REPOSITORY=ctx.repository,GITHUB_RUN_ID=str(ctx.run_id),
                   GITHUB_RUN_ATTEMPT=str(ctx.run_attempt),GITHUB_REF='refs/heads/main')
        mark_unfinished(api,ctx.pr_number,env,'Project debt comment could not be confirmed')
        assert len(comments) == 1 and comments[0]['body'].startswith(SUMMARY_MARKER)
    assert len(posts) == 1


def test_force_push_during_debt_write_stops_success(tmp_path):
    api,ctx,raw,repo,comments,statuses,calls = setup(tmp_path,fixed=True)
    original_request,original_pull,changed = api.request,api.pull,[False]
    def request(method,path,body=None,**kwargs):
        response = original_request(method,path,body,**kwargs)
        if method == 'POST' and path.endswith('/comments'): changed[0] = True
        return response
    def pull(number):
        pr = deepcopy(original_pull(number))
        if changed[0]: pr['head']['sha'] = '0'*40
        return pr
    api.request,api.pull = request,pull
    start(api,ctx)
    with pytest.raises(StaleReview): publish(api,ctx,raw,repository=repo)
    assert statuses[0]['state'] == 'error'
    assert not any(s['state'] == 'success' for s in statuses)


def test_expiry_during_general_comment_uses_lifecycle_guard(tmp_path,monkeypatch):
    deadline = datetime(2099,1,1,tzinfo=timezone.utc)
    clock = [deadline-timedelta(seconds=1)]
    class Clock:
        @classmethod
        def now(cls,tz): return clock[0]
    api,ctx,raw,repo,comments,statuses,calls = setup(tmp_path,status='waived',expires_at='2099-01-01T00:00:00Z')
    monkeypatch.setattr('reviewer.debt_publishing.datetime',Clock)
    original = api.request
    def request(method,path,body=None,**kwargs):
        response = original(method,path,body,**kwargs)
        if method == 'POST' and path.endswith('/comments'): clock[0] = deadline+timedelta(seconds=1)
        return response
    api.request = request
    start(api,ctx)
    with pytest.raises(StaleReview): publish(api,ctx,raw,repository=repo)
    assert comments[0]['body'].startswith(DEBT_MARKER) and statuses[0]['state'] == 'error'


@pytest.mark.parametrize('title',['x'*60_000,'Ж'*30_000])
def test_large_assessment_keeps_original_metadata_and_survives_incomplete_rerun(tmp_path,title):
    api,ctx,raw,repo,comments,statuses,calls = setup(tmp_path,title=title)
    start(api,ctx)
    publish(api,ctx,raw,repository=repo)
    old = comments[0]['body'].partition(ASSESSMENT_MARKER)[2].strip()
    assert ctx.run_url in old and ctx.head_sha in old and ctx.debt_sha256 in old
    assert 'Details shortened;' in old and len(comments[0]['body'].encode()) <= 58_000
    newer = ctx.model_copy(update={'run_attempt':2})
    result = ReviewResult.model_validate(raw['result']).model_copy(update={'status':'error','execution':'partial'})
    incomplete = make_artifact(newer,result)
    start(api,newer)
    for _ in range(2):
        assert publish(api,newer,incomplete,repository=repo) == 'error'
        assert comments[0]['body'].partition(ASSESSMENT_MARKER)[2].strip() == old
        assert newer.run_url in comments[0]['body'] and len(comments[0]['body'].encode()) <= 58_000


def test_oversized_legacy_assessment_is_preserved_before_patch_failure(tmp_path):
    api,ctx,raw,repo,comments,statuses,calls = setup(tmp_path)
    previous = DEBT_MARKER+'\n'+ASSESSMENT_MARKER+'\n'+'x'*58_000
    comments.append(dict(id=100,body=previous,user=dict(login='github-actions[bot]',type='Bot')))
    result = ReviewResult.model_validate(raw['result']).model_copy(update={'status':'error','execution':'partial'})
    start(api,ctx)
    with pytest.raises(GitHubError,match='cannot preserve'):
        publish(api,ctx,make_artifact(ctx,result),repository=repo)
    assert comments[0]['body'] == previous and statuses[0]['state'] == 'error'
    assert not any(method == 'PATCH' for method,_,_ in calls)
