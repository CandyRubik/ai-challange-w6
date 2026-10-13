"""Committed debt/GitHub fixtures shared by context and gate contract tests."""
import json
import httpx
from reviewer import review
from reviewer.git import Repository
from reviewer.github import GitHub
from reviewer.publishing import capture, make_artifact
from tests.test_review_ci import Pass
from tests.test_review_debt import debt_repository
from tests.test_reviewer import commit


def setup(tmp_path, *, fixed=False, **entry):
    root, base, _ = debt_repository(tmp_path, **entry)
    head = commit(root, {'docs/note.md':'note', **({'app/state/service.py':'pass\n'} if fixed else {})})
    pr = dict(number=4, state='open', user={'login':'author'}, base=dict(ref='main', sha=base,
        repo=dict(id=1, full_name='owner/repo')), head=dict(sha=head, repo=dict(id=1)))
    comments, statuses, calls = [], [], []
    def request(req):
        path = req.url.path.removeprefix('/repos/owner/repo')
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, path, body))
        if req.method == 'GET':
            data = (dict(id=1,full_name='owner/repo') if path == '' else pr if path == '/pulls/4'
                else [pr] if path == '/pulls' else statuses if path.endswith('/statuses')
                else comments if path == '/issues/4/comments' else [])
        elif path.startswith('/statuses/'):
            statuses.insert(0, body); data = body
        elif req.method == 'PATCH':
            data = next(c for c in comments if str(c['id']) == path.rsplit('/',1)[1])
            data.update(body)
        else:
            data = dict(id=len(comments)+1, user=dict(login='github-actions[bot]',type='Bot'), **body)
            comments.append(data)
        return httpx.Response(200, json=data)
    api = GitHub('owner/repo','test-token',transport=httpx.MockTransport(request))
    context = capture(api, root, 4, run_id=10, run_attempt=1)
    result = review(root, base, head, model=Pass(), project_debt=True)
    return api, context, make_artifact(context,result), Repository(root), comments, statuses, calls

