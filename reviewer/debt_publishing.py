"""Validate trusted debt coverage and recheck pinned Git sources before publication."""
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib

from .debt import assess, expiry, load_registry, validate_registry
from .github import GitHubError, bot_authored, raw_body
from .report import debt_details, normalize_comment, text

DEBT_MARKER = '<!-- ai-review:debt:v1 -->'
ASSESSMENT_MARKER = '<!-- ai-review:debt:assessment:v1 -->'



def validate_debt(context, result):
    validate_registry(context.debt_registry, context.profile)
    if result.status == 'error' and not result.debt_sha256 and not result.project_debt:
        return  # Analysis may have failed before loading the registry; never a successful check.
    legacy_empty = not context.debt_registry.entries and context.debt_sha256 == hashlib.sha256(b'').hexdigest()
    if result.debt_sha256 != context.debt_sha256 and not (legacy_empty and not result.debt_sha256):
        raise ValueError('Review debt registry hash mismatch')
    items = {a.debt_id:a for a in result.project_debt}
    if len(items) != len(result.project_debt) or set(items) != {e.id for e in context.debt_registry.entries}:
        raise ValueError('Review must assess each trusted debt entry exactly once')
    for entry in context.debt_registry.entries:
        item = items[entry.id]
        if (item.rule_id, item.title, item.blocking) != (entry.rule_id, entry.title, entry.blocking):
            raise ValueError('Debt assessment differs from its trusted entry')
        inactive = entry.status in ('candidate','resolved') or entry.status == 'waived' and expiry(entry.expires_at) > datetime.now(timezone.utc)
        allowed = {entry.status} if inactive else {'open','inconclusive', *({'fixed'} if entry.closure == 'python-import-boundary' else set())}
        if item.verdict not in allowed:
            raise ValueError('Debt verdict contradicts its trusted lifecycle or expiry')
        if result.status != 'error' and item.verdict == 'inconclusive':
            raise ValueError('Incomplete project debt must produce error')
        if result.status == 'success' and item.blocking and item.verdict == 'open':
            raise ValueError('Active blocking project debt cannot produce success')
        if len(set(item.checked_paths)) != len(item.checked_paths) or any(
            not any(fnmatchcase(path, p) for p in entry.scope) for path in item.checked_paths):
            raise ValueError('Debt scan coverage is outside its trusted scope')
        if not inactive and entry.closure == 'python-import-boundary':
            if item.verdict == 'fixed' and (item.evidence or item.violation_count):
                raise ValueError('Fixed debt cannot retain violations')
            if item.verdict == 'open' and (not item.evidence or item.violation_count < len(item.evidence)):
                raise ValueError('Open static debt requires evidence')
            if any(ref.sha != context.head_sha or not any(fnmatchcase(ref.path,p) for p in entry.scope) for ref in item.evidence):
                raise ValueError('Static debt evidence is outside the captured head/scope')
        elif item.evidence != entry.evidence:
            raise ValueError('Owner-confirmed debt evidence differs from the registry')


def verify_debt(repository, context, result):
    if repository is None:
        if context.debt_registry.entries:
            raise ValueError('Publishing project debt requires pinned Git objects in trusted Actions')
        return
    registry, registry_hash = load_registry(repository, context.base_sha, context.profile)
    if registry != context.debt_registry or registry_hash != context.debt_sha256:
        raise ValueError('Publishing debt registry differs from trusted base')
    if result.debt_sha256 and result.project_debt != assess(repository, context.head_sha, registry, context.profile):
        raise ValueError('Publishing debt assessment differs from the actual pinned head')


def publish_debt(api, context, result, *, guard):
    from .publishing import upsert_comment
    existing = [c for c in api.items(f'/issues/{context.pr_number}/comments')
                if bot_authored(c) and raw_body(c).startswith(DEBT_MARKER)]
    if not context.debt_registry.entries and not existing:
        return
    complete = result.status != 'error' and result.execution == 'complete'
    previous = raw_body(existing[-1]).partition(ASSESSMENT_MARKER)[2] if existing else ''
    if complete:
        details = normalize_comment('\n\n'.join([*debt_details(result)[1:], *[
            f'Owner: {text(e.confirmed_by or "unconfirmed")}; {text(e.id)}: {text(e.reason or "candidate")}.'
            for e in context.debt_registry.entries]]))
        if len(details.encode()) > 54_000:
            details = details.encode()[:54_000].decode('utf-8',errors='ignore')+'\n\nDetails shortened; full assessment is in the run artifact.'
        assessment = '\n\n'.join([f'Assessed head: `{context.head_sha}`; registry: `{result.debt_sha256}`.',
            f'[Assessment run]({context.run_url})', details])
    else:
        assessment = previous or 'No completed project debt assessment is available; inspect the run artifact.'
    note = ('Owner-confirmed debt is assessed separately from new PR findings.' if complete else
            'Review incomplete; the previous assessment below is retained and project debt is not cleared.')
    body = normalize_comment('\n\n'.join([DEBT_MARKER, '# Project debt',
        f'Current head: `{context.head_sha}`; review status: **{result.status}**.', note,
        f'[Current workflow run]({context.run_url})', ASSESSMENT_MARKER, assessment.strip()]))
    if len(body.encode()) > 58_000:
        raise GitHubError('Project debt comment cannot preserve the prior assessment within the presentation budget')
    upsert_comment(api,context.pr_number,body,marker=DEBT_MARKER,before_write=guard)
    guard()

