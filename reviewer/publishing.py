"""Bind validated review data to one GitHub PR, revision and workflow generation."""
import hashlib
import json
import re
from fnmatch import fnmatchcase
from typing import Annotated

from pydantic import Field

from .git import Repository
from .models import Profile, Record, ReviewResult
from .report import markdown
from .retrieval import code

SHA = Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
DIGEST = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
STATUS_CONTEXT = 'AI Review / project'
SUMMARY_MARKER = '<!-- ai-review:summary:v1 -->'


class StaleReview(ValueError):
    pass


class RunContext(Record):
    repository: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    repository_id: int = Field(gt=0)
    pr_number: int = Field(gt=0)
    base_sha: SHA
    head_sha: SHA
    code_sha: SHA
    merge_base_sha: SHA
    diff_sha256: DIGEST
    profile_sha256: DIGEST
    profile: Profile
    checks: dict[str, list[str]]
    before_paths: dict[str, str]
    change_status: dict[str, str]
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    external: bool

    @property
    def run_url(self):
        return f'https://github.com/{self.repository}/actions/runs/{self.run_id}/attempts/{self.run_attempt}'


class ReviewArtifact(Record):
    context: RunContext
    result: ReviewResult
    result_sha256: DIGEST


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def capture(api, root, number, *, run_id, run_attempt):
    repo, pr = api.request('GET', ''), api.pull(number)
    if (repo['full_name'] != api.repository or pr['number'] != number
        or pr['base']['repo']['id'] != repo['id'] or pr['base']['ref'] != 'main' or pr['state'] != 'open'):
        raise ValueError('PR must be open against main in the configured repository')
    repository = Repository(root)
    base = pr['base']['sha']
    raw = repository.read(base, '.ai-review/profile.json')
    profile = Profile.model_validate_json(raw)
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
        external=not pr['head']['repo'] or pr['head']['repo']['id'] != repo['id'] or pr['user']['login'] == 'dependabot[bot]')


def make_artifact(context, result):
    body = result.model_dump()
    return ReviewArtifact(context=context, result=result, result_sha256=digest(body)).model_dump()


def preview(context, raw):
    artifact = ReviewArtifact.model_validate(raw)
    result = artifact.result
    if artifact.context != context or digest(result.model_dump()) != artifact.result_sha256:
        raise ValueError('Review artifact identity or digest mismatch')
    if context.code_sha != context.base_sha or result.profile_origin != 'base':
        raise ValueError('CI requires code and profile from trusted base')
    if result.profile_sha256 and result.profile_sha256 != context.profile_sha256:
        raise ValueError('Review profile mismatch')
    if result.snapshot and result.snapshot.model_dump() != {key: getattr(context, key) for key in ('base_sha', 'head_sha', 'merge_base_sha', 'diff_sha256')}:
        raise ValueError('Review snapshot mismatch')
    if result.status != 'error' and (not result.snapshot or not result.profile_sha256 or result.execution == 'failed'):
        raise ValueError('Completed review is missing provenance')
    if result.status == 'success' and any(f.blocking for f in result.findings):
        raise ValueError('A blocking finding cannot produce success')
    if result.policy != context.profile.rules and (result.policy or result.status != 'error'):
        raise ValueError('Review rules differ from captured base policy')
    rules = {rule.id: rule for rule in context.profile.rules}
    must = {rule.id for rule in context.profile.rules if rule.level == 'must'}
    if any(item.rule_id not in {*rules, 'BUGS'} for item in [*result.criteria, *result.findings]):
        raise ValueError('Unknown review rule')
    pairs = [(c.path, c.rule_id) for c in result.criteria]
    expected = {(path, rule) for path, checks in context.checks.items() for rule in checks}
    if result.status != 'error' and (set(pairs) != expected or len(pairs) != len(expected)):
        raise ValueError('Review criteria do not cover the captured changes')
    incomplete = [c for c in result.criteria if c.verdict == 'inconclusive']
    if result.status != 'error' and any(c.rule_id in must | {'BUGS'} for c in incomplete):
        raise ValueError('Incomplete mandatory analysis must produce error')
    if result.status != 'error' and result.execution == 'partial' and not incomplete:
        raise ValueError('Partial review has no explicit unchecked criteria')
    if result.status == 'success' and any(c.rule_id in must and c.verdict == 'violation' for c in result.criteria):
        raise ValueError('Mandatory violation cannot produce success')
    def corresponds(finding, criterion):
        ref, path = finding.evidence, criterion.path
        return finding.rule_id == criterion.rule_id and (
            ref.sha == context.head_sha and ref.path == path and context.change_status.get(path) != 'D'
            or ref.sha == context.merge_base_sha and ref.path == context.before_paths.get(path) and context.change_status.get(path) != 'A')
    if (any(c.verdict == 'violation' and not any(corresponds(f, c) for f in result.findings) for c in result.criteria)
        or any(not any(c.verdict == 'violation' and corresponds(f, c) for c in result.criteria) for f in result.findings)):
        raise ValueError('Violations and findings contradict each other')
    refs = [ref for c in result.criteria for ref in c.evidence]
    for finding in result.findings:
        rule = rules.get(finding.rule_id)
        if finding.blocking != (rule.level == 'must' if rule else finding.severity == 'critical'):
            raise ValueError('Finding blocking flag contradicts base policy')
        if rule and (not finding.source or finding.source.sha != context.base_sha
                     or finding.source.path != rule.source.path or finding.source.quote != rule.source.quote):
            raise ValueError('Finding normative source differs from base policy')
        refs.extend([finding.evidence, *finding.supporting_evidence, *([finding.source] if finding.source else [])])
    if any(ref.sha not in {context.base_sha, context.head_sha, context.merge_base_sha}
           or ref.line < (1 if ref.kind == 'text' else 0) for ref in refs):
        raise ValueError('Evidence is outside the captured revisions or line range')
    for key in ('model_calls', 'optional_fragments_omitted'):
        if key in result.trace and (type(result.trace[key]) is not int or result.trace[key] < 0):
            raise ValueError('Invalid numeric trace metadata')
    if 'context_sha256' in result.trace and not re.fullmatch(r'[0-9a-f]{64}', str(result.trace['context_sha256'])):
        raise ValueError('Invalid context digest')
    body = markdown(result).replace('@', '@\u200b')
    body = ''.join(c for c in body if c in '\n\t' or ord(c) >= 32)
    if len(body.encode()) > 58_000:
        body = body.encode()[:58_000].decode('utf-8', errors='ignore') + '\n\nPresentation shortened; full result is in the run artifact.'
    return {'state': result.status, 'body': f'{SUMMARY_MARKER}\n{body}\n[Workflow run]({context.run_url})'}


def generation(api, context):
    generations = [(0, 0)]
    for status in api.items(f'/commits/{context.head_sha}/statuses'):
        if status.get('context') == STATUS_CONTEXT:
            match = re.fullmatch(re.escape(f'https://github.com/{context.repository}/actions/runs/') + r'(\d+)/attempts/(\d+)', status.get('target_url') or '')
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
    current, ours = generation(api, context), (context.run_id, context.run_attempt)
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


def _publish(api, context, rendered):
    assert_current(api, context)
    comments = api.items(f'/issues/{context.pr_number}/comments')
    existing = [c for c in comments if c.get('user', {}).get('login') == 'github-actions[bot]'
                and c['user'].get('type') == 'Bot' and c.get('body', '').startswith(SUMMARY_MARKER)]
    # Recheck immediately before the write; the result itself never chooses a target.
    assert_current(api, context)
    if existing:
        api.request('PATCH', f'/issues/comments/{existing[-1]["id"]}', {'body': rendered['body']})
    else:
        api.request('POST', f'/issues/{context.pr_number}/comments', {'body': rendered['body']})
    assert_current(api, context)
    set_status(api, context, rendered['state'], f'AI review: {rendered["state"]}')
    assert_current(api, context)
    return rendered['state']


def publish(api, context, raw):
    # Invalid content must not cause writes; subsequent transport/freshness failure invalidates our prior result.
    rendered = preview(context, raw)
    try:
        return _publish(api, context, rendered)
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
