import json
from pathlib import Path
import subprocess
import sys

import pytest

from reviewer import review
from reviewer.debt import suggest
from reviewer.git import Repository
from reviewer.models import Profile
from reviewer.report import markdown
from tests.test_reviewer import PROFILE, commit, git, repository


def debt_repository(tmp_path, **overrides):
    root, _ = repository(tmp_path)
    old = commit(root, {'app/state/service.py':'import sqlite3\n'})
    entry = dict(id='state-storage', rule_id='ARCH-STATE-PURE', title='State uses storage',
        status='active', blocking=True, scope=['app/state/*.py'], closure='python-import-boundary',
        evidence=[dict(path='app/state/service.py', sha=old, line=1, quote='import sqlite3')],
        confirmed_by='maintainer', reason='Confirmed by the project owner')
    entry.update(overrides)
    raw = dict(schema_version=1, entries=[entry])
    base = commit(root, {'.ai-review/debt.json':json.dumps(raw)})
    return root, base, raw


def test_active_debt_blocks_unrelated_change_without_becoming_a_pr_finding(tmp_path):
    root, base, _ = debt_repository(tmp_path)
    head = commit(root, {'docs/note.md':'Typo fixed\n'})
    result = review(root, base, head, project_debt=True)
    assert (result.status, result.execution, result.findings) == ('failure', 'complete', [])
    item, = result.project_debt
    assert (item.verdict, item.violation_count, item.checked_paths) == ('open', 1, ['app/state/service.py'])
    assert item.evidence[0].sha == head and item.evidence[0].line == 1
    assert ('Project debt' in markdown(result) or 'Долг проекта' in markdown(result))
    assert review(root, base, head).status == 'success'  # local opt-in until CI integration


@pytest.mark.parametrize('status,expected', [('candidate','candidate'), ('resolved','resolved'), ('waived','waived')])
def test_inactive_or_validly_waived_debt_does_not_block(tmp_path, status, expected):
    values = dict(status=status)
    if status == 'waived':
        values['expires_at'] = '2099-01-01T00:00:00Z'
    root, base, _ = debt_repository(tmp_path, **values)
    result = review(root, base, base, project_debt=True)
    assert result.status == 'success' and result.project_debt[0].verdict == expected


def test_fix_checks_entire_rule_scope_and_ignores_proposed_registry(tmp_path):
    root, base, raw = debt_repository(tmp_path)
    raw['entries'][0]['status'] = 'resolved'
    head = commit(root, {'.ai-review/debt.json':json.dumps(raw)})
    assert review(root, base, head, project_debt=True).status == 'failure'
    head = commit(root, {'app/state/service.py':'pass\n', 'app/state/other.py':'import sqlite3\n'})
    result = review(root, base, head, project_debt=True)
    assert result.project_debt[0].verdict == 'open'
    assert result.project_debt[0].evidence[0].path == 'app/state/other.py'
    head = commit(root, {'app/state/other.py':'pass\n'})
    assert review(root, base, head, project_debt=True).project_debt[0].verdict == 'fixed'


@pytest.mark.parametrize('source', ['__import__("sqlite3")\n', 'def broken(\n', 'bad\0text'])
def test_unknown_closure_never_produces_success(tmp_path, source):
    root, base, _ = debt_repository(tmp_path)
    head = commit(root, {'app/state/service.py':source})
    result = review(root, base, head, project_debt=True)
    assert result.status == 'error' and result.project_debt[0].verdict == 'inconclusive'


def test_rename_does_not_evade_debt_check(tmp_path):
    root, base, _ = debt_repository(tmp_path)
    git(root, 'mv', 'app/state/service.py', 'app/state/renamed.py')
    head = commit(root, {'docs/note.md':'note'})
    item, = review(root, base, head, project_debt=True).project_debt
    assert item.verdict == 'open' and item.evidence[0].path.endswith('renamed.py')


def test_manual_closure_requires_owner_even_after_code_change(tmp_path):
    root, base, _ = debt_repository(tmp_path, closure='owner-attestation')
    head = commit(root, {'app/state/service.py':'pass\n'})
    item, = review(root, base, head, project_debt=True).project_debt
    assert item.verdict == 'open' and 'explicit closure' in item.reason


def test_expired_waiver_is_rechecked(tmp_path):
    root, base, _ = debt_repository(tmp_path, status='waived', expires_at='2020-01-01T00:00:00Z')
    assert review(root, base, base, project_debt=True).status == 'failure'


@pytest.mark.parametrize('edit', [
    {'confirmed_by':'outsider'}, {'reason':' '}, {'rule_id':'unknown'}, {'scope':['app/state/service.py']},
    {'status':'waived'}, {'status':'waived','expires_at':'2099-01-01T00:00:00'},
    {'rule_id':'BUGS','closure':'owner-attestation'},
])
def test_invalid_confirmations_or_weak_scopes_fail_closed(tmp_path, edit):
    root, base, _ = debt_repository(tmp_path, **edit)
    assert review(root, base, base, project_debt=True).status == 'error'


@pytest.mark.parametrize('raw', ['', '{}', '{"schema_version":true,"entries":[]}', '{"schema_version":2,"entries":[]}'])
def test_present_invalid_registry_is_not_treated_as_absent(tmp_path, raw):
    root, base = repository(tmp_path)
    base = commit(root, {'.ai-review/debt.json':raw})
    assert review(root, base, base, project_debt=True).status == 'error'


def test_evidence_is_checked_against_pinned_history(tmp_path):
    root, base, raw = debt_repository(tmp_path)
    raw['entries'][0]['evidence'][0]['quote'] = 'invented'
    base = commit(root, {'.ai-review/debt.json':json.dumps(raw)})
    assert review(root, base, base, project_debt=True).status == 'error'


def test_scan_budget_and_unreadable_files_are_inconclusive(tmp_path, monkeypatch):
    root, base, _ = debt_repository(tmp_path)
    monkeypatch.setattr('reviewer.debt.MAX_SCOPE_FILES', 0)
    assert review(root, base, base, project_debt=True).status == 'error'


def test_directory_symlink_cannot_hide_scoped_descendants(tmp_path):
    root, base, _ = debt_repository(tmp_path)
    (root/'app/shared').mkdir()
    git(root, 'mv', 'app/state/service.py', 'app/shared/service.py')
    (root/'app/state/pkg').symlink_to('../shared', target_is_directory=True)
    head = commit(root, {'docs/note.md':'note'})
    item, = review(root, base, head, project_debt=True).project_debt
    assert item.verdict == 'inconclusive'


def test_unrelated_symlink_does_not_prevent_a_proven_fix(tmp_path):
    root, base, _ = debt_repository(tmp_path)
    (root/'other').symlink_to('app/state', target_is_directory=True)
    head = commit(root, {'app/state/service.py':'pass\n'})
    assert review(root, base, head, project_debt=True).project_debt[0].verdict == 'fixed'


def test_suggestion_is_unapproved_and_does_not_modify_registry(tmp_path):
    root, base, raw = debt_repository(tmp_path)
    repository = Repository(root)
    profile = Profile.model_validate(PROFILE)
    candidate, = suggest(repository, base, profile).entries
    assert candidate.status == 'candidate' and not candidate.confirmed_by
    script = Path(__file__).resolve().parents[1]/'scripts/review_debt.py'
    output = subprocess.check_output([sys.executable, str(script), '--repo', str(root), '--head', base, '--suggest'])
    assert json.loads(output)['entries'][0]['status'] == 'candidate'
    assert json.loads(repository.read(base, '.ai-review/debt.json')) == raw
    checked = subprocess.run([sys.executable, str(script), '--repo', str(root), '--base', base, '--head', base], capture_output=True)
    assert checked.returncode == 1 and json.loads(checked.stdout)['project_debt'][0]['verdict'] == 'open'


def test_absent_registry_is_empty_for_existing_repositories(tmp_path):
    root, base = repository(tmp_path)
    result = review(root, base, base, project_debt=True)
    assert result.status == 'success' and result.project_debt == [] and len(result.debt_sha256) == 64
