import json

import pytest

from reviewer.ci import analyze_snapshot, prepare_snapshot, select_prs, validate_proposed_profile, mark_unfinished, fetch_head
from reviewer.git import Repository
from reviewer.publishing import StaleReview, preview
from tests.test_reviewer import commit, git
from tests.test_review_publishing import fixture


def setup(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    git(root, 'checkout', '--detach', context.base_sha)
    env = dict(GITHUB_REPOSITORY='owner/repo', GITHUB_RUN_ID='10', GITHUB_RUN_ATTEMPT='1',
        GITHUB_WORKFLOW_SHA=context.base_sha, GITHUB_REF='refs/heads/main',
        GITHUB_EVENT_NAME='pull_request_target', GITHUB_ACTOR='maintainer', GITHUB_TRIGGERING_ACTOR='maintainer')
    return api, context, env, pr, calls, statuses, root


class Pass:
    name = 'controlled-ci-model'
    calls = 0
    def generate_json(self, *, messages, **kwargs):
        Pass.calls += 1
        payload = json.loads(messages[-1]['content'])
        return json.dumps(dict(criteria=[{**c, 'verdict':'pass', 'reason':'No additional defect',
            'evidence':[dict(fragment_id=f['id'], quote=f['text'])]} for c in payload['criteria']
            for f in [next(f for f in payload['fragments'] if f['role']=='head' and f['path']==c['path'] and f['kind']=='text')]],
            findings=[], recommendations=[]))


def test_trusted_ci_analyzes_head_as_data_and_preserves_static_failure(tmp_path):
    api, old, env, pr, calls, statuses, root = setup(tmp_path)
    context = prepare_snapshot(api, root, 4, env, fetcher=lambda *args: old.head_sha)
    artifact = analyze_snapshot(api, root, context, env, model_factory=Pass, fetcher=lambda *args: old.head_sha)
    assert preview(context, artifact)['state'] == 'failure'
    assert git(root, 'rev-parse', 'HEAD') == context.base_sha
    assert git(root, 'show', 'HEAD:app/state/service.py').endswith('return state')


@pytest.mark.parametrize('bad', ['repository', 'run', 'attempt', 'workflow', 'checkout'])
def test_context_and_executing_code_are_checked_before_model_calls(tmp_path, bad):
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    if bad == 'checkout':
        git(root, 'checkout', '--detach', context.head_sha)
    else:
        key = {'repository':'GITHUB_REPOSITORY', 'run':'GITHUB_RUN_ID', 'attempt':'GITHUB_RUN_ATTEMPT', 'workflow':'GITHUB_WORKFLOW_SHA'}[bad]
        env[key] = {'repository':'other/repo', 'workflow':'0'*40}.get(bad, '99')
    def forbidden():
        raise AssertionError('No model construction allowed')
    with pytest.raises(ValueError):
        analyze_snapshot(api, root, context, env, model_factory=forbidden, fetcher=lambda *args: context.head_sha)


def test_fork_requires_current_owner_manual_dispatch(tmp_path):
    api, old, env, pr, calls, statuses, root = setup(tmp_path)
    pr['head']['repo'] = {'id':2}
    with pytest.raises(ValueError, match='manual'):
        prepare_snapshot(api, root, 4, env, fetcher=lambda *args: old.head_sha)
    env['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
    env['GITHUB_TRIGGERING_ACTOR'] = 'another-writer'
    with pytest.raises(ValueError, match='owner'):
        prepare_snapshot(api, root, 4, env, fetcher=lambda *args: old.head_sha)
    env['GITHUB_TRIGGERING_ACTOR'] = 'maintainer'
    assert prepare_snapshot(api, root, 4, env, fetcher=lambda *args: old.head_sha).external


def test_draft_waits_for_ready_or_an_owner_dispatch(tmp_path):
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    pr['draft'] = True
    with pytest.raises(ValueError, match='Draft'):
        prepare_snapshot(api, root, 4, env, fetcher=lambda *args: context.head_sha)
    env['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
    assert prepare_snapshot(api, root, 4, env, fetcher=lambda *args: context.head_sha).head_sha == context.head_sha


def test_retarget_triggers_review_but_title_and_body_edits_do_not(tmp_path):
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    before = len(calls)
    assert select_prs(api, {'number':4, 'action':'edited', 'changes':{'title':{'from':'old'}}}, env) == []
    assert len(calls) == before
    assert select_prs(api, {'number':4, 'action':'edited', 'changes':{'base':{'ref':{'from':'other'}}}}, env) == [4]
    assert statuses[0]['state'] == 'pending'


def test_stale_fetch_is_rejected_and_missing_key_becomes_error_artifact(tmp_path):
    api, old, env, pr, calls, statuses, root = setup(tmp_path)
    with pytest.raises(StaleReview):
        prepare_snapshot(api, root, 4, env, fetcher=lambda *args: '0'*40)
    context = prepare_snapshot(api, root, 4, env, fetcher=lambda *args: old.head_sha)
    def missing():
        raise ValueError('Key missing')
    artifact = analyze_snapshot(api, root, context, env, model_factory=missing, fetcher=lambda *args: old.head_sha)
    assert preview(context, artifact)['state'] == 'error'


def test_queue_overflow_errors_all_heads_instead_of_silent_truncation(tmp_path):
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    env['GITHUB_EVENT_NAME'] = 'push'
    api.open_pulls = lambda: [pr, {**pr, 'number':5}]
    with pytest.raises(ValueError, match='capacity'):
        select_prs(api, {}, env, capacity=1)
    assert statuses and all(s['state']=='error' for s in statuses)


def test_proposed_profile_integrity_is_checked_without_adopting_its_rules(tmp_path):
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    head = commit(root, {'.ai-review/profile.json':'{}'})
    with pytest.raises(ValueError):
        validate_proposed_profile(Repository(root), head)


def test_ci_requires_main_ancestry_instead_of_reviewing_an_unmerged_head(tmp_path):
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    main = commit(root, {'docs/unrelated.md':'New main change\n'})
    env['GITHUB_WORKFLOW_SHA'] = pr['base']['sha'] = main
    with pytest.raises(ValueError, match='rebase'):
        prepare_snapshot(api, root, 4, env, fetcher=lambda *args: context.head_sha)


def test_missing_artifact_errors_the_owned_status_and_publishes_reason(tmp_path):
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    select_prs(api, {'number':4}, env)
    mark_unfinished(api, 4, env, 'Fork requires owner manual dispatch')
    assert statuses[0]['state'] == 'error'
    assert any('Fork requires' in body.get('body','') for method,path,body in calls if method=='POST')
    newer = {**env, 'GITHUB_RUN_ID':'11'}
    select_prs(api, {'number':4}, newer)
    mark_unfinished(api, 4, env)
    assert statuses[0]['state'] == 'pending' and statuses[0]['target_url'].endswith('/runs/11/attempts/1')


def test_failed_only_rerun_invalidates_an_earlier_success_of_the_same_run(tmp_path):
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    select_prs(api, {'number':4}, env)
    statuses[0]['state'] = 'success'
    rerun = {**env, 'GITHUB_RUN_ATTEMPT':'2'}
    mark_unfinished(api, 4, rerun, 'Current-attempt artifact missing; re-run all jobs')
    assert statuses[0]['state'] == 'error' and statuses[0]['target_url'].endswith('/runs/10/attempts/2')


@pytest.mark.parametrize('failed', [False, True])
def test_git_fetch_keeps_token_out_of_argv_and_files(tmp_path, monkeypatch, failed):
    import subprocess
    api, context, env, pr, calls, statuses, root = setup(tmp_path)
    token = 'capture-only-test-token'
    def run(argv, **kwargs):
        assert token not in repr(argv)
        from pathlib import Path
        assert token not in Path(kwargs['env']['GIT_ASKPASS']).read_text()
        assert kwargs['env']['GITHUB_TOKEN'] == token
        assert 'checkout' not in argv and '--no-recurse-submodules' in argv
        if failed:
            raise subprocess.CalledProcessError(1, argv, stderr=token.encode())
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(Repository, 'run', lambda self,*args: (context.head_sha+'\n').encode())
    if failed:
        with pytest.raises(ValueError) as error:
            fetch_head(root, 4, 'owner/repo', token)
        assert token not in str(error.value)
    else:
        assert fetch_head(root, 4, 'owner/repo', token) == context.head_sha
