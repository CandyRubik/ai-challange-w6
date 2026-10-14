"""Trusted Actions orchestration: proposed Git objects are data, never executed."""
import hashlib
import os
import re
from pathlib import Path
import subprocess
import tempfile

from . import review
from .debt import load_registry
from .git import Repository
from .models import Profile
from .publishing import (STATUS_CONTEXT, SUMMARY_MARKER, StaleReview, assert_current, capture, generation,
                         make_artifact, start, workflow_url, upsert_summary)
from .report import text
from .retrieval import document
from .semantic_models import Limits
from .owner_journal import same_snapshot
from .reuse import capture_source,check_source,reuse_result

CI_LIMITS = Limits(context_bytes=96_000, output_tokens=4_000)


def identity(env):
    if env['GITHUB_REF'] != 'refs/heads/main':
        raise ValueError('Review workflows must execute from main')
    return env['GITHUB_REPOSITORY'], int(env['GITHUB_RUN_ID']), int(env['GITHUB_RUN_ATTEMPT'])


def check_code(root, sha):
    if Repository(root).run('rev-parse', 'HEAD').decode().strip() != sha:
        raise ValueError('Executing checkout differs from trusted workflow SHA')


def check_context(root, context, env, *, captured_source=True):
    if identity(env) != (context.repository, context.run_id, context.run_attempt):
        raise ValueError('Context repository/run/attempt mismatch')
    if env['GITHUB_WORKFLOW_SHA'] != context.code_sha or context.code_sha != context.base_sha:
        raise ValueError('Workflow code must match the captured main SHA')
    check_code(root, context.code_sha)
    raw = Repository(root).read(context.base_sha, '.ai-review/profile.json')
    if hashlib.sha256(raw.encode()).hexdigest() != context.profile_sha256 or Profile.model_validate_json(raw) != context.profile:
        raise ValueError('Context profile differs from trusted Git base')
    registry, registry_hash = load_registry(Repository(root), context.base_sha, context.profile)
    if registry != context.debt_registry or registry_hash != context.debt_sha256:
        raise ValueError('Context debt registry differs from trusted Git base')
    if captured_source:
        check_source(context,env)


def fetch_head(root, number, repository, token):
    if type(number) is not int or number < 1:
        raise ValueError('Invalid PR number')
    with tempfile.TemporaryDirectory(prefix='review-fetch-') as directory:
        helper = Path(directory) / 'askpass.py'
        helper.write_text("#!/usr/bin/env python3\nimport os,sys\nprompt=sys.argv[1].lower()\n"
            "if 'username' in prompt: print('x-access-token')\n"
            "elif 'password' in prompt: print(os.environ['GITHUB_TOKEN'])\n"
            "else: raise SystemExit(1)\n")
        helper.chmod(0o700)
        try:
            subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.attributesFile=/dev/null',
                '-c', 'credential.helper=', '-c', 'http.followRedirects=false', '-C', str(root), 'fetch',
                '--no-tags', '--no-recurse-submodules', f'https://github.com/{repository}.git', f'refs/pull/{number}/head'],
                env={**os.environ, 'GITHUB_TOKEN':token, 'GIT_ASKPASS':str(helper), 'GIT_TERMINAL_PROMPT':'0'},
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except (subprocess.SubprocessError, OSError):
            raise ValueError('Pinned PR objects could not be fetched') from None
    return Repository(root).run('rev-parse', 'FETCH_HEAD^{commit}').decode().strip()


def owner_dispatch(context, env):
    owners = {owner.casefold() for owner in context.profile.owners}
    return (env['GITHUB_EVENT_NAME'] == 'workflow_dispatch'
            and env['GITHUB_ACTOR'].casefold() in owners and env.get('GITHUB_TRIGGERING_ACTOR', '').casefold() in owners)


def prepare_snapshot(api, root, number, env, *, fetcher=fetch_head):
    repository, run_id, attempt = identity(env)
    if repository != api.repository:
        raise ValueError('GitHub repository mismatch')
    check_code(root, env['GITHUB_WORKFLOW_SHA'])
    fetched = fetcher(root, number, repository, env.get('GITHUB_TOKEN', ''))
    context = capture(api, root, number, run_id=run_id, run_attempt=attempt)
    check_context(root, context, env,captured_source=False)
    if fetched != context.head_sha:
        raise StaleReview('PR changed during fetch; rerun required')
    if context.merge_base_sha != context.base_sha:
        raise ValueError('PR is behind current main; rebase before AI review')
    if env['GITHUB_EVENT_NAME'] == 'workflow_dispatch' and not owner_dispatch(context, env):
        raise ValueError('Manual review requires the current profile owner')
    if api.pull(number).get('draft') and not owner_dispatch(context, env):
        raise ValueError('Draft review is deferred; mark ready or use owner manual dispatch')
    if context.external and not owner_dispatch(context, env):
        raise ValueError('Fork/Dependabot requires owner manual dispatch on main')
    context = capture_source(api,Repository(root),context,env)
    check_context(root,context,env)
    start(api, context)
    return context


def validate_proposed_profile(repository, head):
    profile = Profile.model_validate_json(repository.read(head, '.ai-review/profile.json'))
    for rule in profile.rules:
        if rule.id == 'BUGS' or not document(rule.source.path) or rule.source.quote not in repository.read(head, rule.source.path):
            raise ValueError('Proposed profile has an invalid rule or normative source')
    load_registry(repository, head, profile)


def analyze_snapshot(api, root, context, env, *, model_factory=None, fetcher=fetch_head):
    check_context(root, context, env)
    fetched = fetcher(root, context.pr_number, context.repository, env.get('GITHUB_TOKEN', ''))
    current = capture(api, root, context.pr_number, run_id=context.run_id, run_attempt=context.run_attempt)
    if not same_snapshot(current,context) or fetched != context.head_sha:
        raise StaleReview('Captured PR context changed; rerun required')
    if context.merge_base_sha != context.base_sha:
        raise ValueError('PR is behind current main; rebase before AI review')
    assert_current(api, context)
    if (context.external or env['GITHUB_EVENT_NAME'] == 'workflow_dispatch') and not owner_dispatch(context, env):
        raise ValueError('External PR analysis requires owner manual dispatch')
    validate_proposed_profile(Repository(root), context.head_sha)
    if context.analysis_source:
        check_source(context,env)
        return make_artifact(context,reuse_result(api,Repository(root),context))
    try:
        if model_factory is None:
            from .deepseek import DeepSeekReviewModel
            model_factory = DeepSeekReviewModel
        model = model_factory()
    except (ValueError, RuntimeError):
        result = review(root, context.base_sha, context.head_sha, project_debt=True)
        result = result.model_copy(update={'status':'error', 'execution':'failed',
            'limitations':[*result.limitations, 'Review model is not configured; no cloud request sent']})
    else:
        result = review(root, context.base_sha, context.head_sha, model=model, limits=CI_LIMITS, project_debt=True)
    return make_artifact(context, result)


def write_state(api, head, env, state, reason):
    repository, run_id, attempt = identity(env)
    if not isinstance(head, str) or not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ValueError('GitHub head SHA is invalid')
    if repository != api.repository or generation(api, head) > (run_id, attempt):
        return False
    api.request('POST', f'/statuses/{head}', dict(state=state, context=STATUS_CONTEXT,
        target_url=workflow_url(repository, run_id, attempt), description=reason[:140]))
    return True


def mark_unfinished(api, number, env, reason='Review did not complete; inspect workflow and rerun', *, summary=True):
    head = api.pull(number)['head']['sha']
    _, run_id, attempt = identity(env)
    current = generation(api, head)
    if current[0] == run_id and current[1] <= attempt:
        write_state(api, head, env, 'error', 'Review did not complete; inspect workflow and rerun')
        if summary and generation(api, head) == (run_id, attempt):
            url = workflow_url(api.repository, run_id, attempt)
            body = f'{SUMMARY_MARKER}\n# Project review\nStatus: **error**; execution: **failed**.\n\n{text(reason).replace("@", "@\u200b")}\n\n[Workflow run]({url})'
            def still_ours():
                if api.pull(number)['head']['sha'] != head or generation(api, head) != (run_id, attempt):
                    raise StaleReview('PR or workflow generation changed before error publication')
            upsert_summary(api, number, body, before_write=still_ours)


def select_prs(api, event, env, *, capacity=20):
    identity(env)
    kind = env['GITHUB_EVENT_NAME']
    if kind == 'pull_request_target' and event.get('action') == 'edited' and 'base' not in event.get('changes', {}):
        return []
    if kind == 'push':
        rows = api.open_pulls()
    else:
        raw = event.get('inputs', {}).get('pr') if kind == 'workflow_dispatch' else event.get('number')
        if isinstance(raw, str) and raw.isascii() and raw.isdigit():
            raw = int(raw)
        if type(raw) is not int or raw < 1:
            raise ValueError('PR number is required')
        rows = [api.pull(raw)]
    rows = [pr for pr in rows if pr['state'] == 'open' and pr['base']['ref'] == 'main']
    overflow = len(rows) > capacity
    for pr in rows:
        write_state(api, pr['head']['sha'], env, 'error' if overflow else 'pending',
                    'Review queue capacity exceeded; manual retry required' if overflow else 'AI review queued')
    if overflow:
        raise ValueError('Review queue capacity exceeded; no paid analysis started')
    return [pr['number'] for pr in rows]
