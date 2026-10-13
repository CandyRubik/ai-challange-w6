import hashlib

import pytest

from reviewer.git import Repository
from reviewer.github import GitHubError
from reviewer.inline_publishing import publish_inline
from reviewer.models import ReviewResult
from reviewer.publishing import assert_current, start
from tests.test_review_publishing import fixture


def setup(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path,code='import sqlite3\n# @maintainer\n# additional\n')
    from reviewer.models import ReviewResult
    result = ReviewResult.model_validate(artifact['result'])
    repository = Repository(root)
    contents = repository.read(context.head_sha, 'app/state/service.py').encode()
    blob = hashlib.sha1(b'blob '+str(len(contents)).encode()+b'\0'+contents).hexdigest()
    files = [dict(filename='app/state/service.py', status='modified', sha=blob,
        patch='@@ -1,2 +1,3 @@\n-def apply(state):\n-    return state\n+import sqlite3\n+# @maintainer\n+# additional')]
    comments, reviews, writes = [], [], []
    original = api.request
    def request(method, path, body=None, **kwargs):
        if method == 'GET' and path.endswith('/files'):
            return files
        if method == 'GET' and path == '/pulls/4/comments':
            return comments
        if method == 'GET' and path == '/pulls/4/reviews':
            return reviews
        if method == 'POST' and path == '/pulls/4/reviews':
            writes.append(body)
            assert body['event'] == 'COMMENT' and body['commit_id'] == context.head_sha
            review = dict(id=20+len(reviews), commit_id=context.head_sha, state='COMMENTED', body=body['body'], user=dict(login='github-actions[bot]', type='Bot'))
            reviews.append(review)
            for entry in body['comments']:
                comments.append(dict(id=100+len(comments), pull_request_review_id=review['id'], commit_id=context.head_sha, user=review['user'], **entry))
            return review
        if method == 'PATCH' and path.startswith('/pulls/comments/'):
            comment = next(c for c in comments if c['id'] == int(path.rsplit('/',1)[1]))
            comment['body'] = body['body']
            return comment
        return original(method, path, body, **kwargs)
    api.request = request
    start(api, context)
    guard = lambda: assert_current(api, context)
    return api, context, result, repository, files, comments, reviews, writes, guard, pr


def test_nullable_unrelated_bot_reviews_and_deleted_users_are_ignored(tmp_path):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    reviews.append(dict(id=99,user=dict(login='github-actions[bot]',type='Bot'),body=None,commit_id=context.head_sha,state='COMMENTED'))
    comments.append(dict(id=99,user=None,body=None))
    publish_inline(api, context, result, repo, guard=guard)
    assert len(writes)==1


def test_rerun_updates_existing_comment_without_another_review(tmp_path):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    publish_inline(api, context, result, repo, guard=guard)
    changed = result.model_copy(update={'findings':[result.findings[0].model_copy(update={'message':'Updated explanation @maintainer'})]})
    publish_inline(api, context, changed, repo, guard=guard)
    assert len(writes)==1 and len(comments)==1
    assert 'Updated explanation' in comments[0]['body'] and '@maintainer' not in comments[0]['body']


@pytest.mark.parametrize('accepted', [True, False])
def test_unknown_post_is_recovered_or_errors_without_blind_retry(tmp_path, accepted):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    original = api.request
    posts = []
    def uncertain(method, path, body=None, **kwargs):
        if method=='POST' and path.endswith('/reviews'):
            posts.append(body)
            if accepted:
                original(method,path,body,**kwargs)
            raise GitHubError('GitHub POST outcome unknown')
        return original(method,path,body,**kwargs)
    api.request = uncertain
    if accepted:
        publish_inline(api, context, result, repo, guard=guard)
        publish_inline(api, context, result, repo, guard=guard)
        assert len(comments)==1
    else:
        with pytest.raises(GitHubError):
            publish_inline(api, context, result, repo, guard=guard)
    assert len(posts)==1


def test_force_push_before_write_does_not_post_to_the_new_head(tmp_path):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    original = api.request
    def racing(method, path, body=None, **kwargs):
        response = original(method,path,body,**kwargs)
        if method=='GET' and path.endswith('/files'):
            pr['head']['sha'] = '0'*40
        return response
    api.request = racing
    with pytest.raises(ValueError):
        publish_inline(api, context, result, repo, guard=guard)
    assert not comments


@pytest.mark.parametrize('complete', [True, False])
def test_old_comments_are_marked_superseded_only_after_complete_analysis(tmp_path, complete):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    publish_inline(api, context, result, repo, guard=guard)
    verdict = 'pass' if complete else 'inconclusive'
    current = result.model_copy(update={'findings':[], 'status':'success' if complete else 'error',
        'execution':'complete' if complete else 'partial',
        'criteria':[c.model_copy(update={'verdict':verdict}) for c in result.criteria]})
    publish_inline(api, context, current, repo, guard=guard)
    assert ('ai-review:superseded:' in comments[0]['body']) == complete
    assert len(writes)==1


def test_pending_review_cannot_be_mistaken_for_published_comments(tmp_path):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    publish_inline(api,context,result,repo,guard=guard)
    reviews[0]['state'] = 'PENDING'
    with pytest.raises(GitHubError,match='confirmed'):
        publish_inline(api,context,result,repo,guard=guard)
    assert len(writes)==1


def test_unknown_post_requires_exact_bodies_in_the_committed_batch(tmp_path):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    original = api.request
    def changed(method,path,body=None,**kwargs):
        response = original(method,path,body,**kwargs)
        if method=='POST' and path.endswith('/reviews'):
            comments[0]['body'] = comments[0]['body'].split('\n',1)[0]+'\nDifferent report'
            raise GitHubError('Unknown POST')
        return response
    api.request = changed
    with pytest.raises(GitHubError,match='confirmed'):
        publish_inline(api,context,result,repo,guard=guard)
    assert len(writes)==1


def test_inline_publisher_keeps_summary_status_and_validates_before_writes(tmp_path):
    from reviewer.publishing import make_artifact, publish
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    artifact = make_artifact(context,result)
    invalid = {**artifact,'result_sha256':'0'*64}
    with pytest.raises(ValueError):
        publish(api,context,invalid,repository=repo)
    assert not writes
    assert publish(api,context,artifact,repository=repo)=='failure'
    assert len(writes)==1 and len(comments)==1
    assert any('AI review' in r.get('body','') or 'Project review' in r.get('body','') for r in api.items('/issues/4/comments'))


def test_complete_review_preserves_human_replies_and_foreign_review_comments(tmp_path):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    publish_inline(api,context,result,repo,guard=guard)
    for update in [{'user':{'login':'human','type':'User'}},{'in_reply_to_id':comments[0]['id']},
                   {'pull_request_review_id':99}]:
        comments.append({**comments[0],'id':100+len(comments),**update})
    bodies = [c['body'] for c in comments[1:]]
    publish_inline(api,context,result.model_copy(update={'findings':[]}),repo,guard=guard)
    assert [c['body'] for c in comments[1:]]==bodies
    assert 'superseded' in comments[0]['body']


def test_error_even_with_complete_execution_keeps_existing_inline_current(tmp_path):
    from reviewer.publishing import make_artifact, publish
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    publish_inline(api,context,result,repo,guard=guard)
    current = result.model_copy(update={'status':'error','execution':'complete','findings':[],
        'criteria':[c.model_copy(update={'verdict':'pass'}) for c in result.criteria]})
    assert publish(api,context,make_artifact(context,current),repository=repo)=='error'
    assert 'superseded' not in comments[0]['body']


@pytest.mark.parametrize('edited_oversize', [False, True, 'mentions'])
def test_retirement_preserves_large_original_body_without_truncation(tmp_path, edited_oversize):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    large = result.model_copy(update={'findings':[result.findings[0].model_copy(update={'message':'x'*58_000})]})
    publish_inline(api,context,large,repo,guard=guard)
    if edited_oversize:
        edited = '@'*23_000+'TAIL_SENTINEL' if edited_oversize=='mentions' else 'x'*64_000
        comments[0]['body'] = comments[0]['body'].split('\n',1)[0]+'\n'+edited
    original = comments[0]['body']
    assert len(original.encode())>58_000 or edited_oversize=='mentions'
    fallback = publish_inline(api,context,result.model_copy(update={'findings':[]}),repo,guard=guard)
    if edited_oversize:
        assert comments[0]['body']==original and fallback
    else:
        assert comments[0]['body'].endswith(original) and len(comments[0]['body'].encode())<64_000


@pytest.mark.parametrize('changed_plan', [False, True])
def test_partial_visibility_rerun_reconciles_full_batch_without_duplicates(tmp_path, changed_plan):
    api, context, result, repo, files, comments, reviews, writes, guard, pr = setup(tmp_path)
    second = result.findings[0].model_copy(update={'rule_id':'BUGS','source':None,'blocking':True,'severity':'critical',
        'evidence':result.findings[0].evidence.model_copy(update={'line':2,'quote':'# @maintainer'})})
    result = result.model_copy(update={'findings':[*result.findings,second]})
    original, visibility = api.request, {'hidden_reads':4}
    def partial(method,path,body=None,**kwargs):
        response = original(method,path,body,**kwargs)
        if method=='POST' and path.endswith('/reviews'):
            raise GitHubError('Accepted batch response lost')
        if method=='GET' and path=='/pulls/4/comments' and comments and visibility['hidden_reads']:
            visibility['hidden_reads'] -= 1
            return comments[:1]
        return response
    api.request = partial
    with pytest.raises(GitHubError,match='confirmed'):
        publish_inline(api,context,result,repo,guard=guard)
    rerun = context.model_copy(update={'run_attempt':2})
    result = result.model_copy(update={'findings':[result.findings[0],second.model_copy(update={'blocking':False,'severity':'minor'})]})
    if changed_plan:
        third = second.model_copy(update={'evidence':second.evidence.model_copy(update={'line':3,'quote':'# additional'})})
        result = result.model_copy(update={'findings':[*result.findings,third]})
    start(api,rerun)
    publish_inline(api,rerun,result,repo,guard=lambda:assert_current(api,rerun))
    assert len(writes)==(2 if changed_plan else 1) and len(comments)==(3 if changed_plan else 2)
    assert all('/attempts/2' in c['body'] for c in comments)
