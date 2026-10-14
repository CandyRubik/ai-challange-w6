"""Validate trusted debt coverage and recheck pinned Git sources before publication."""
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib

from .debt import assess, expiry, load_registry, validate_registry



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

