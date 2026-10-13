import hashlib
import json

import httpx
import pytest

from reviewer import review
from reviewer.github import GitHub, GitHubError, UncertainSummaryPost
from reviewer.publishing import capture, make_artifact, preview, publish, start, StaleReview, STATUS_CONTEXT, upsert_summary, SUMMARY_MARKER
from tests.test_reviewer import repository, commit


def fixture(tmp_path, profile=None, code='import sqlite3\n# @maintainer\n', extra=None):
    root, base = repository(tmp_path) if profile is None else repository(tmp_path, profile)
    head = commit(root, {'app/state/service.py': code, **(extra or {})})
    pr = dict(number=4, state='open', user={'login': 'author'},
        base=dict(ref='main', sha=base, repo=dict(id=1, full_name='owner/repo')),
        head=dict(sha=head, repo=dict(id=1)))
    calls, comments, statuses = [], [], []
    def handle(request):
        assert request.url.host == 'api.github.com'
        assert request.headers['Authorization'] == 'Bearer test-token'
        path = request.url.path.removeprefix('/repos/owner/repo')
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, path, body))
        if request.method == 'GET':
            data = (dict(id=1, full_name='owner/repo') if path == '' else
                    pr if path == '/pulls/4' else
                    [pr] if path == '/pulls' else
                    statuses if path == f'/commits/{head}/statuses' else comments)
        elif path == f'/statuses/{head}':
            statuses.insert(0, body)
            data = body
        elif request.method == 'PATCH':
            comments[0]['body'] = body['body']
            data = comments[0]
        else:
            data = dict(id=7, body=body['body'], user=dict(type='Bot', login='github-actions[bot]'))
            comments.append(data)
        return httpx.Response(200, json=data)
    api = GitHub('owner/repo', 'test-token', transport=httpx.MockTransport(handle))
    context = capture(api, root, 4, run_id=10, run_attempt=1)
    class PassBugs:
        name = 'controlled-bugs-pass'
        def generate_json(self, *, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            return json.dumps(dict(criteria=[{**c, 'verdict': 'pass', 'reason': 'No additional defect',
                'evidence': [dict(fragment_id=source['id'], quote=source['text'])]} for c in payload['criteria']
                for source in [next(f for f in payload['fragments'] if f['role'] == 'head' and f['kind'] == 'text' and f['path'] == c['path'])]], findings=[], recommendations=[]))
    result = review(root, base, head, model=PassBugs())
    return api, context, make_artifact(context, result), pr, calls, statuses, root


def test_pinned_failure_is_published_with_sanitized_summary_and_status(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    artifact['result']['findings'][0]['message'] += ' @maintainer <script>'
    artifact['result_sha256'] = hashlib.sha256(json.dumps(artifact['result'], sort_keys=True).encode()).hexdigest()
    start(api, context)
    assert publish(api, context, artifact) == 'failure'
    body = next(b['body'] for method, path, b in calls if method == 'POST' and path.endswith('/comments'))
    assert '@maintainer' not in body and '<script>' not in body
    assert context.head_sha in body
    assert statuses[0]['state'] == 'failure'
    assert statuses[0]['target_url'].endswith('/runs/10/attempts/1')


@pytest.mark.parametrize('changed', ['head', 'base', 'state'])
def test_stale_snapshot_never_publishes(tmp_path, changed):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    start(api, context)
    if changed == 'state':
        pr['state'] = 'closed'
    else:
        pr[changed]['sha'] = '0' * 40
    with pytest.raises(StaleReview):
        publish(api, context, artifact)
    assert not any(path.endswith('/comments') and method != 'GET' for method, path, _ in calls)


def test_later_generation_cannot_be_overwritten_by_old_success(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    start(api, context)
    newer = context.model_copy(update={'run_id': 11})
    start(api, newer)
    with pytest.raises(StaleReview):
        publish(api, context, artifact)
    assert statuses[0]['state'] == 'pending'


@pytest.mark.parametrize('tamper', ['digest', 'head', 'profile', 'origin', 'context'])
def test_artifact_identity_is_validated_before_writes(tmp_path, tamper):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    if tamper == 'digest':
        artifact['result_sha256'] = '0' * 64
    else:
        if tamper == 'head':
            artifact['result']['snapshot']['head_sha'] = '0' * 40
        elif tamper == 'profile':
            artifact['result']['profile_sha256'] = '0' * 64
        elif tamper == 'origin':
            artifact['result']['profile_origin'] = 'explicit_local'
        else:
            artifact['context']['pr_number'] = 5
        artifact['result_sha256'] = hashlib.sha256(json.dumps(artifact['result'], sort_keys=True).encode()).hexdigest()
    with pytest.raises(ValueError):
        preview(context, artifact)
    assert all(method == 'GET' for method, _, _ in calls)


def test_summary_is_updated_and_an_unknown_post_is_not_blindly_retried(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    start(api, context)
    publish(api, context, artifact)
    publish(api, context, artifact)
    assert sum(method == 'POST' and path.endswith('/comments') for method, path, _ in calls) == 1
    assert any(method == 'PATCH' and path.endswith('/7') for method, path, _ in calls)
    writes = []
    def fail(request):
        writes.append(request)
        raise httpx.ReadTimeout('unknown outcome')
    broken = GitHub('owner/repo', 'test-token', transport=httpx.MockTransport(fail))
    with pytest.raises(GitHubError):
        broken.request('POST', '/issues/4/comments', {'body': 'safe'})
    assert len(writes) == 1


def test_pagination_and_http_errors_are_explicit():
    def handle(request):
        page = int(request.url.params.get('page', '1'))
        return httpx.Response(200, json=[{'id': i} for i in range(100)] if page == 1 else [{'id': 101}])
    api = GitHub('owner/repo', 'test-token', transport=httpx.MockTransport(handle))
    assert len(api.items('/issues/4/comments')) == 101
    for status in (403, 429, 503):
        api = GitHub('owner/repo', 'test-token', transport=httpx.MockTransport(lambda request: httpx.Response(status, json={'message': 'sensitive details'})))
        with pytest.raises(GitHubError, match=str(status)) as error:
            api.request('GET', '/pulls/4')
        assert 'sensitive' not in str(error.value)


def test_capture_rejects_wrong_repository(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    pr['base']['repo']['id'] = 2
    with pytest.raises(ValueError):
        capture(api, root, 4, run_id=10, run_attempt=1)


def test_base_change_during_final_status_write_is_invalidated(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    start(api, context)
    original = api.request
    def racing(method, path, body=None, **kwargs):
        response = original(method, path, body, **kwargs)
        if method == 'POST' and path.startswith('/statuses/') and body['state'] == 'failure':
            pr['base']['sha'] = '0' * 40
        return response
    api.request = racing
    with pytest.raises(StaleReview):
        publish(api, context, artifact)
    assert statuses[0]['state'] == 'error'


def test_overwritten_newer_generation_remains_stale(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    start(api, context.model_copy(update={'run_id': 11}))
    statuses.insert(0, dict(context=STATUS_CONTEXT, target_url=context.run_url, state='success'))
    with pytest.raises(StaleReview):
        publish(api, context, artifact)


@pytest.mark.parametrize('tamper', ['blocker', 'violation', 'policy', 'evidence', 'partial', 'unknown', 'coverage', 'merge', 'tracehash', 'tracecalls'])
def test_inconsistent_review_content_is_rejected(tmp_path, tamper):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    result = artifact['result']
    if tamper == 'evidence':
        result['findings'][0]['evidence']['sha'] = '0' * 40
    elif tamper == 'policy':
        result['policy'][0]['level'] = 'should'
    elif tamper == 'merge':
        result['snapshot']['merge_base_sha'] = 'x`\n<img src="https://unit.invalid/tracker">\n`'
    elif tamper.startswith('trace'):
        result['trace']['context_sha256' if tamper == 'tracehash' else 'model_calls'] = '`<script>`'
    else:
        result['status'] = 'success'
        if tamper != 'blocker':
            result['findings'] = []
        if tamper in ('partial', 'unknown'):
            result.update(execution='partial', criteria=[])
        if tamper == 'unknown':
            result['criteria'] = [dict(path='app/state/service.py', rule_id='UNKNOWN', verdict='inconclusive', reason='unknown', evidence=[])]
        if tamper == 'coverage':
            result['criteria'] = []
    artifact['result_sha256'] = hashlib.sha256(json.dumps(artifact['result'], sort_keys=True).encode()).hexdigest()
    with pytest.raises(ValueError):
        preview(context, artifact)


def test_explicit_known_should_inconclusive_can_remain_advisory(tmp_path):
    from copy import deepcopy
    from tests.test_reviewer import PROFILE
    profile = deepcopy(PROFILE)
    profile['rules'][0]['level'] = 'should'
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path, profile)
    result = artifact['result']
    result.update(execution='partial', findings=[])
    result['criteria'][0]['verdict'] = 'inconclusive'
    artifact['result_sha256'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    assert preview(context, artifact)['state'] == 'success'


def test_complete_major_bug_remains_advisory(tmp_path):
    from copy import deepcopy
    from tests.test_reviewer import PROFILE
    from tests.test_review_semantic import Model
    class MajorBug(Model):
        def generate_json(self, **kwargs):
            draft = json.loads(super().generate_json(**kwargs))
            for criterion in draft['criteria']:
                criterion['verdict'] = 'violation' if criterion['rule_id'] == 'BUGS' else 'pass'
            draft['findings'][0].update(rule_id='BUGS', severity='major')
            return json.dumps(draft)
    profile = deepcopy(PROFILE)
    profile['rules'][0].update(check='semantic', forbidden_imports=[])
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path, profile, 'def apply(state):\n    return None\n')
    result = review(root, context.base_sha, context.head_sha, model=MajorBug())
    assert result.status == 'success'
    assert preview(context, make_artifact(context, result))['state'] == 'success'
    incomplete = result.model_copy(update={'findings': []})
    with pytest.raises(ValueError, match='Violations'):
        preview(context, make_artifact(context, incomplete))


def test_supporting_caller_cannot_supply_a_missing_second_diagnosis(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path, extra={'app/state/other.py': 'import sqlite3\n'})
    result = artifact['result']
    result['status'] = 'success'
    for c in result['criteria']:
        c['verdict'] = 'violation' if c['rule_id'] == 'BUGS' else 'pass'
    for f in result['findings']:
        f.update(rule_id='BUGS', severity='major', blocking=False, source=None)
    result['findings'][0]['supporting_evidence'] = [result['findings'][1]['evidence']]
    result['findings'] = result['findings'][:1]
    artifact['result_sha256'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    with pytest.raises(ValueError, match='Violations'):
        preview(context, artifact)


def test_renamed_before_primary_is_bound_to_the_new_reviewed_path(tmp_path):
    from tests.test_reviewer import git
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    git(root, 'mv', 'app/state/service.py', 'app/state/renamed.py')
    pr['head']['sha'] = commit(root, {'app/state/renamed.py': 'def apply(state):\n    return None\n'})
    context = capture(api, root, 4, run_id=10, run_attempt=1)
    from reviewer.models import ReviewResult
    snapshot = {key: getattr(context, key) for key in ('base_sha', 'head_sha', 'merge_base_sha', 'diff_sha256')}
    data = artifact['result']
    for c in data['criteria']:
        c.update(path='app/state/renamed.py', evidence=[])
    data['snapshot'] = snapshot
    data['findings'][0]['evidence'].update(sha=context.merge_base_sha, line=2, quote='    return state', path='app/state/service.py')
    data['findings'][0]['supporting_evidence'] = []
    assert preview(context, make_artifact(context, ReviewResult.model_validate(data)))['state'] == 'failure'


@pytest.mark.parametrize('during', ['summary', 'freshness'])
def test_own_success_is_invalidated_after_summary_race_or_failed_final_get(tmp_path, during):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    start(api, context)
    publish(api, context, artifact)
    statuses[0]['state'] = 'success'  # Earlier complete run owns a green status.
    original = api.request
    mutated = False
    def racing(method, path, body=None, **kwargs):
        nonlocal mutated
        if during == 'freshness' and mutated and method == 'GET' and path == '/pulls/4':
            raise GitHubError('GitHub GET failed: HTTP 503')
        response = original(method, path, body, **kwargs)
        if method == 'PATCH' and path.endswith('/7'):
            mutated = True
            if during == 'summary':
                pr['base']['sha'] = '0' * 40
        return response
    api.request = racing
    with pytest.raises((StaleReview, GitHubError)):
        publish(api, context, artifact)
    assert statuses[0]['state'] == 'error'


def test_two_open_prs_with_same_head_cannot_share_a_success(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    original = api.open_pulls
    api.open_pulls = lambda: [*original(), {**pr, 'number': 5}]
    with pytest.raises(StaleReview, match='Multiple'):
        start(api, context)
    assert not statuses


def test_external_and_dependabot_prs_are_identified_before_analysis(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    for head_repo, author in ((None, 'author'), ({'id': 2}, 'author'), ({'id': 1}, 'dependabot[bot]')):
        pr['head']['repo'], pr['user']['login'] = head_repo, author
        assert capture(api, root, 4, run_id=10, run_attempt=1).external


def test_cli_previews_a_failure_without_credentials_or_network(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    context_path, artifact_path = tmp_path / 'context.json', tmp_path / 'result.json'
    context_path.write_text(context.model_dump_json())
    artifact_path.write_text(json.dumps(artifact))
    env = {key: value for key, value in os.environ.items() if key not in ('GITHUB_TOKEN', 'DEEPSEEK_API_KEY')}
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/publish_review.py'),
               '--context', str(context_path), '--artifact', str(artifact_path)]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 1 and context.head_sha in result.stdout
    assert not result.stderr


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
