import json
from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest

from reviewer import review

CLI = Path(__file__).resolve().parents[1] / "scripts/review_pr.py"


PROFILE = {
    "schema_version": 1, "project": "example", "owners": ["maintainer"],
    "rules": [{
        "id": "ARCH-STATE-PURE", "category": "architecture", "level": "must",
        "scope": ["app/state/*.py"], "requirement": "No storage dependencies",
        "rationale": "State transitions must be pure", "check": "python-import-boundary",
        "forbidden_imports": ["sqlite3", "app.storage", "app.providers"],
        "source": {"path": "README.md", "quote": "State transitions have no database access."},
    }],
}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


def commit(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


def repository(tmp_path, profile=PROFILE):
    git(tmp_path, "init", "-q")
    base = commit(tmp_path, {
        ".ai-review/profile.json": json.dumps(profile),
        "README.md": "# Example\nState transitions have no database access.\n",
        "app/state/service.py": "def apply(state):\n    return state\n",
    })
    return tmp_path, base


def test_new_storage_dependency_has_pinned_evidence_and_fails_review(tmp_path):
    root, base = repository(tmp_path)
    head = commit(root, {"app/state/service.py": "import sqlite3\n"})
    result = review(root, base, head)
    assert result.status == "failure"
    assert result.snapshot.head_sha == head
    finding, = result.findings
    assert finding.rule_id == "ARCH-STATE-PURE"
    assert finding.evidence.path == "app/state/service.py"
    assert finding.evidence.line == 1
    assert finding.evidence.quote == "import sqlite3"
    assert finding.evidence.sha == head


def test_semantic_rule_is_inconclusive_without_a_model(tmp_path):
    profile = deepcopy(PROFILE)
    profile["rules"][0].update(check="semantic", forbidden_imports=[])
    root, base = repository(tmp_path, profile)
    head = commit(root, {"app/state/service.py": "def apply(state):\n    return state + 1\n"})
    result = review(root, base, head)
    assert (result.execution, result.status, result.findings) == ("partial", "error", [])
    assert result.criteria[0].verdict == "inconclusive"


@pytest.mark.parametrize("statement, status, count", [
    ("from ..storage import Store", "failure", 1),
    ("from .. import storage", "error", 0),
])
def test_relative_import_distinguishes_modules_from_ambiguous_attributes(tmp_path, statement, status, count):
    root, base = repository(tmp_path)
    head = commit(root, {"app/state/service.py": statement + "\n"})
    result = review(root, base, head)
    assert result.status == status
    assert len(result.findings) == count
    if count:
        assert result.findings[0].evidence.quote == statement


def test_profile_and_code_come_from_commits_not_the_worktree(tmp_path):
    root, base = repository(tmp_path)
    head = commit(root, {"app/state/service.py": "import sqlite3\n", ".ai-review/profile.json": "{}"})
    (root / "app/state/service.py").write_text("pass\n")
    result = review(root, base, head)
    assert result.status == "failure"
    assert result.profile_origin == "base"
    assert result.findings[0].evidence.quote == "import sqlite3"


def test_existing_violation_is_not_presented_as_new(tmp_path):
    root, _ = repository(tmp_path)
    base = commit(root, {"app/state/service.py": "import sqlite3\n"})
    head = commit(root, {"app/state/service.py": "import sqlite3\n# unrelated change\n"})
    assert review(root, base, head).findings == []


def test_moving_impure_module_into_state_introduces_a_violation(tmp_path):
    root, _ = repository(tmp_path)
    base = commit(root, {"app/other.py": "import sqlite3\n"})
    git(root, "mv", "app/other.py", "app/state/other.py")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "move")
    result = review(root, base, "HEAD")
    assert result.status == "failure"
    assert result.findings[0].evidence.path == "app/state/other.py"


@pytest.mark.parametrize("text", [
    "def broken(\n", "binary\0data", "__import__('sqlite3')\n",
    "from importlib import import_module\nimport_module('sqlite3')\n",
    "import importlib as il\nil.import_module('sqlite3')\n",
    "import builtins\nbuiltins.__import__('sqlite3')\n",
    "from builtins import __import__ as load\nload('sqlite3')\n",
])
def test_unanalyzable_source_does_not_produce_success(tmp_path, text):
    root, base = repository(tmp_path)
    head = commit(root, {"app/state/service.py": text})
    result = review(root, base, head)
    assert (result.execution, result.status) == ("partial", "error")
    assert result.criteria[0].verdict == "inconclusive"


@pytest.mark.parametrize("mistake", ["version", "boolean_version", "duplicate", "empty_check", "missing_quote", "extra"])
def test_invalid_policy_is_a_configuration_error(tmp_path, mistake):
    profile = deepcopy(PROFILE)
    if mistake == "version":
        profile["schema_version"] = 2
    elif mistake == "boolean_version":
        profile["schema_version"] = True
    elif mistake == "duplicate":
        profile["rules"].append(deepcopy(profile["rules"][0]))
    elif mistake == "empty_check":
        profile["rules"][0]["forbidden_imports"] = []
    elif mistake == "missing_quote":
        profile["rules"][0]["source"]["quote"] = "This rule was not approved."
    else:
        profile["commands"] = ["execute arbitrary command"]
    root, base = repository(tmp_path, profile)
    result = review(root, base, base)
    assert (result.execution, result.status) == ("failed", "error")
    assert result.limitations


def test_cli_emits_json_and_policy_markdown_without_executing_head(tmp_path):
    root, base = repository(tmp_path)
    sentinel = root / "executed"
    head = commit(root, {"app/state/service.py": f"import sqlite3\nopen({str(sentinel)!r}, 'w').close()\n"})
    command = [sys.executable, str(CLI), "--repo", str(root), "--base", base, "--head", head]
    output = subprocess.run([*command, "--format", "json"], capture_output=True, text=True)
    assert output.returncode == 1
    assert json.loads(output.stdout)["status"] == "failure"
    output = subprocess.run([*command, "--explain-policy"], capture_output=True, text=True)
    assert ("## Policy" in output.stdout or "## Правила проекта" in output.stdout) and "No storage dependencies" in output.stdout
    assert not sentinel.exists()


def test_project_profile_has_valid_base_sources(tmp_path):
    root = Path(__file__).resolve().parents[1]
    raw = (root / ".ai-review/profile.json").read_text()
    paths = {rule["source"]["path"] for rule in json.loads(raw)["rules"]}
    git(tmp_path, "init", "-q")
    base = commit(tmp_path, {".ai-review/profile.json": raw, **{path: (root / path).read_text() for path in paths}})
    result = review(tmp_path, base, base)
    assert result.status == "success"
    assert {rule.category for rule in result.policy} == {"architecture", "forbidden", "best_practice", "invariant"}


def test_deletion_and_unicode_rename_are_read_from_git(tmp_path):
    root, base = repository(tmp_path)
    git(root, "mv", "app/state/service.py", "app/state/новый модуль.py")
    head = commit(root, {"app/state/новый модуль.py": "import sqlite3\n"})
    result = review(root, base, head)
    assert result.findings[0].evidence.path == "app/state/новый модуль.py"
    git(root, "rm", "app/state/новый модуль.py")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "delete")
    assert review(root, head, "HEAD").status == "success"


def test_symlink_is_not_followed(tmp_path):
    root, base = repository(tmp_path)
    (root / "app/state/service.py").unlink()
    (root / "app/state/service.py").symlink_to("/etc/passwd")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "symlink")
    result = review(root, base, "HEAD")
    assert result.status == "error"
    assert "Unsupported source type" in result.criteria[0].reason


def test_new_forbidden_submodule_is_not_hidden_by_existing_dependency(tmp_path):
    root, _ = repository(tmp_path)
    base = commit(root, {"app/state/service.py": "import app.storage.turns\n"})
    head = commit(root, {"app/state/service.py": "import app.storage.turns\nimport app.storage.migrations\n"})
    result = review(root, base, head)
    assert result.status == "failure"
    assert len(result.findings) == 1
    assert result.findings[0].evidence.quote == "import app.storage.migrations"


def test_attribute_is_not_reported_as_a_forbidden_module(tmp_path):
    root, _ = repository(tmp_path)
    base = commit(root, {"app/__init__.py": "storage = 123\n"})
    head = commit(root, {"app/state/service.py": "from app import storage\n"})
    result = review(root, base, head)
    assert result.findings == []
    assert result.status == "error"


@pytest.mark.parametrize("previous", ["from app.storage import turns", "from app.storage import *"])
def test_equivalent_import_spelling_is_not_a_new_violation(tmp_path, previous):
    root, _ = repository(tmp_path)
    base = commit(root, {"app/storage/__init__.py": "", "app/storage/turns.py": "",
                         "app/state/service.py": previous + "\n"})
    head = commit(root, {"app/state/service.py": "import app.storage.turns as turns\n"})
    result = review(root, base, head)
    assert result.findings == []
    assert result.status == "error"
