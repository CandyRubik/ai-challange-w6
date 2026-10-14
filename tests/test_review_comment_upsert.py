import pytest

from reviewer.ci import mark_unfinished
from reviewer.github import GitHubError, UncertainCommentPost, UncertainSummaryPost
from reviewer.publishing import SUMMARY_MARKER, start, upsert_comment
from tests.review_debt_fixtures import setup


@pytest.mark.parametrize('accepted',[True,False])
def test_generic_unknown_post_recovers_once_or_allows_error_summary(tmp_path,accepted):
    api,ctx,raw,repo,comments,statuses,calls = setup(tmp_path)
    marker,body = '<!-- custom-owned-comment -->','custom report'
    original,posts = api.request,[]
    def uncertain(method,path,data=None,**kwargs):
        if method == 'POST' and path.endswith('/comments') and data['body'].startswith(marker):
            posts.append(data)
            if accepted: original(method,path,data,**kwargs)
            raise GitHubError('Unknown transport outcome')
        return original(method,path,data,**kwargs)
    api.request = uncertain
    start(api,ctx)
    if accepted:
        upsert_comment(api,4,marker+body,marker=marker,before_write=lambda:None)
        assert len(comments) == 1 and comments[0]['body'] == marker+body
    else:
        with pytest.raises(UncertainCommentPost) as error:
            upsert_comment(api,4,marker+body,marker=marker,before_write=lambda:None)
        assert not isinstance(error.value,UncertainSummaryPost)
        env = dict(GITHUB_REPOSITORY=ctx.repository,GITHUB_RUN_ID=str(ctx.run_id),
                   GITHUB_RUN_ATTEMPT=str(ctx.run_attempt),GITHUB_REF='refs/heads/main')
        mark_unfinished(api,4,env,'Generic comment uncertain')
        assert len(comments) == 1 and comments[0]['body'].startswith(SUMMARY_MARKER)
    assert len(posts) == 1
