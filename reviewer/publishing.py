"""Bind validated review data to one GitHub PR, revision and workflow generation."""
import hashlib
import re
from fnmatch import fnmatchcase


from .debt import load_registry
from .artifacts import make_artifact, preview
from .contracts import (SHA, DIGEST, STATUS_CONTEXT, SUMMARY_MARKER, RunContext, ReviewArtifact, digest, workflow_url)
from .git import Repository
from .github import GitHubError, UncertainCommentPost, UncertainSummaryPost, bot_authored, raw_body
from .models import Profile, ReviewResult
from .report import safe_comment, text
from .retrieval import code


class StaleReview(ValueError):
    pass


def capture(api, root, number, *, run_id, run_attempt):
    repo, pr = api.request('GET', ''), api.pull(number)
    if (repo['full_name'] != api.repository or pr['number'] != number
        or pr['base']['repo']['id'] != repo['id'] or pr['base']['ref'] != 'main' or pr['state'] != 'open'):
        raise ValueError('PR must be open against main in the configured repository')
    repository = Repository(root)
    base = pr['base']['sha']
    raw = repository.read(base, '.ai-review/profile.json')
    profile = Profile.model_validate_json(raw)
    debt_registry, debt_sha256 = load_registry(repository, base, profile)
    for rule in profile.rules:
        if rule.source.quote not in repository.read(base, rule.source.path):
            raise ValueError('Normative source is missing from trusted base')
    snapshot, changes = repository.snapshot(base, pr['head']['sha'])
    checks = {c.path: [r.id for r in profile.rules if any(fnmatchcase(c.path, pattern) for pattern in r.scope)]
              + (['BUGS'] if code(c.path) else []) for c in changes}
    return RunContext(repository=api.repository, repository_id=repo['id'], pr_number=number,
        base_sha=base, head_sha=pr['head']['sha'], code_sha=base,
        merge_base_sha=snapshot.merge_base_sha, diff_sha256=snapshot.diff_sha256, checks=checks, profile=profile,
        before_paths={c.path: c.before_path for c in changes}, change_status={c.path: c.status for c in changes},
        profile_sha256=hashlib.sha256(raw.encode()).hexdigest(), run_id=run_id, run_attempt=run_attempt,
        debt_registry=debt_registry, debt_sha256=debt_sha256,
        external=not pr['head']['repo'] or pr['head']['repo']['id'] != repo['id'] or pr['user']['login'] == 'dependabot[bot]')


def generation(api, head_sha):
    generations = [(0, 0)]
    for status in api.items(f'/commits/{head_sha}/statuses'):
        if status.get('context') == STATUS_CONTEXT:
            match = re.fullmatch(re.escape(f'https://github.com/{api.repository}/actions/runs/') + r'(\d+)/attempts/(\d+)', status.get('target_url') or '')
            if match:
                generations.append(tuple(map(int, match.groups())))
    return max(generations)


def assert_current(api, context, *, started=True, unique=True):
    pr = api.pull(context.pr_number)
    if (api.repository != context.repository or pr['state'] != 'open' or pr['base']['ref'] != 'main'
        or pr['base']['repo']['id'] != context.repository_id or pr['base']['sha'] != context.base_sha
        or pr['head']['sha'] != context.head_sha):
        raise StaleReview('PR revision, base or repository changed; rerun required')
    if unique and any(p['number'] != context.pr_number and p['head']['sha'] == context.head_sha for p in api.open_pulls()):
        raise StaleReview('Multiple open PRs share this head; one status cannot represent both')
    current, ours = generation(api, context.head_sha), (context.run_id, context.run_attempt)
    if current > ours or started and current != ours:
        raise StaleReview('A newer or different workflow generation owns this status')


def set_status(api, context, state, description):
    return api.request('POST', f'/statuses/{context.head_sha}', dict(state=state, context=STATUS_CONTEXT,
        target_url=context.run_url, description=description[:140]))


def start(api, context):
    assert_current(api, context, started=False)
    set_status(api, context, 'pending', 'AI review in progress')


def fail(api, context, reason='Review did not complete; rerun required'):
    assert_current(api, context, started=False, unique=False)
    set_status(api, context, 'error', reason)


def upsert_comment(api, number, body, *, marker, before_write):
    comments = api.items(f'/issues/{number}/comments')
    existing = [c for c in comments if bot_authored(c) and raw_body(c).startswith(marker)]
    before_write()
    if existing:
        api.request('PATCH', f'/issues/comments/{existing[-1]["id"]}', {'body': body})
    else:
        try:
            api.request('POST', f'/issues/{number}/comments', {'body': body})
        except GitHubError:
            for _ in range(3):
                try:
                    before_write()
                    recovered = api.items(f'/issues/{number}/comments')
                except GitHubError:
                    continue
                except (ValueError, KeyError, TypeError, OSError):
                    break
                if any(bot_authored(c) and raw_body(c) == body for c in recovered):
                    return
            uncertain = UncertainSummaryPost if marker == SUMMARY_MARKER else UncertainCommentPost
            raise uncertain('Comment POST could not be confirmed; no blind retry; rerun required') from None



def upsert_summary(api, number, body, *, before_write):
    return upsert_comment(api,number,body,marker=SUMMARY_MARKER,before_write=before_write)


def _publish(api, context, rendered, raw, repository):
    from .debt_publishing import validate_debt
    result = ReviewResult.model_validate(raw['result'])
    def guard():
        assert_current(api, context)
        try:
            validate_debt(context, result)
        except ValueError as error:
            raise StaleReview('Debt lifecycle changed during publication; rerun required') from error
    guard()
    from .debt_publishing import publish_debt
    publish_debt(api,context,result,guard=guard)
    if repository is not None:
        from .inline_publishing import publish_inline
        fallback = publish_inline(api,context,ReviewResult.model_validate(raw['result']),repository,
                                  guard=guard)
        if fallback:
            rendered = {**rendered,'body':safe_comment(rendered['body']+'\n\n## Inline coverage\n'+
                '\n'.join('- '+text(reason) for reason in fallback))}
    # Per-PR workflow serialization covers writes; snapshot is rechecked around the operation.
    upsert_summary(api, context.pr_number, rendered['body'], before_write=guard)
    guard()
    set_status(api, context, rendered['state'], f'AI review: {rendered["state"]}')
    guard()
    return rendered['state']


def publish(api, context, raw, *, repository=None):
    # Invalid content must not cause writes; subsequent transport/freshness failure invalidates our prior result.
    rendered = preview(context, raw)
    from .debt_publishing import verify_debt
    verify_debt(repository, context, ReviewResult.model_validate(raw['result']))
    try:
        return _publish(api, context, rendered, raw, repository)
    except (StaleReview, RuntimeError):
        try:
            invalidate_own_status(api, context)
        except RuntimeError:
            pass  # The workflow/CLI still fails; GitHub may be unavailable for the error write too.
        raise


def invalidate_own_status(api, context):
    latest = next((s for s in api.items(f'/commits/{context.head_sha}/statuses') if s.get('context') == STATUS_CONTEXT), {})
    if latest.get('target_url') == context.run_url:
        set_status(api, context, 'error', 'Publication or freshness check failed; rerun required')
