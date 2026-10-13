"""Unknown transport outcomes must not duplicate public review reports."""
import pytest

from reviewer.ci import mark_unfinished, select_prs
from reviewer.github import GitHubError, UncertainSummaryPost
from reviewer.publishing import SUMMARY_MARKER, upsert_summary
from tests.test_review_ci import setup
from tests.test_review_publishing import fixture


@pytest.mark.parametrize('accepted', [True, False])
def test_summary_unknown_post_never_creates_a_second_comment(tmp_path, accepted):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    original = api.request
    posts = []
    def uncertain(method, path, body=None, **kwargs):
        if method=='POST' and path.endswith('/comments'):
            posts.append(body)
            if accepted:
                original(method,path,body,**kwargs)
            raise GitHubError('Unknown write outcome')
        return original(method,path,body,**kwargs)
    api.request = uncertain
    if accepted:
        upsert_summary(api,4,SUMMARY_MARKER+'\nVerified review',before_write=lambda:None)
    else:
        with pytest.raises(UncertainSummaryPost):
            upsert_summary(api,4,SUMMARY_MARKER+'\nVerified review',before_write=lambda:None)
    assert len(posts)==1


def test_uncertain_summary_failure_finalizes_status_without_another_comment(tmp_path):
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    select_prs(api, {'number':4}, env)
    mark_unfinished(api,4,env,'Summary POST uncertain',summary=False)
    assert statuses[0]['state']=='error'
    assert not any(method=='POST' and path.endswith('/comments') for method,path,body in calls)


@pytest.mark.parametrize('failure', [KeyError, TypeError, ValueError, OSError])
def test_failed_confirmation_preserves_unknown_post_classification(tmp_path, failure):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    original, checks = api.request, []
    def uncertain(method, path, body=None, **kwargs):
        response = original(method,path,body,**kwargs)
        if method=='POST' and path.endswith('/comments'):
            raise GitHubError('Response lost after acceptance')
        return response
    def guard():
        checks.append(True)
        if len(checks)>1:
            raise failure('Malformed freshness response')
    api.request = uncertain
    with pytest.raises(UncertainSummaryPost):
        upsert_summary(api,4,SUMMARY_MARKER+'\nReview',before_write=guard)
    assert sum(method=='POST' and path.endswith('/comments') for method,path,body in calls)==1
