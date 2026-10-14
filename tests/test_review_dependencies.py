from reviewer import review
from reviewer.dependencies import function_contexts, imported_functions
from reviewer.git import Repository
from reviewer.retrieval import retrieve
from reviewer.semantic_models import Limits
from tests.test_reviewer import commit, repository


def test_relative_alias_resolves_only_existing_unambiguous_repo_function():
    text = 'from .debt import expiry as deadline\n\ndef check(value):\n    return deadline(value)\n'
    paths = {'reviewer/debt.py'}
    assert imported_functions('reviewer/check.py',text,[(3,4)],paths) == {'reviewer/debt.py':{'expiry'}}
    assert imported_functions('reviewer/check.py',text,[(0,1)],paths) == {'reviewer/debt.py':{'expiry'}}
    assert imported_functions('reviewer/check.py',text,[(3,4)],paths|{'reviewer/debt/__init__.py'}) == {}
    assert imported_functions('reviewer/check.py',text,[(3,4)],set()) == {}


def test_shadowing_star_import_and_unaffected_function_are_conservative():
    text = 'from app.helpers import work\n\ndef changed(value):\n    return value\n\ndef other():\n    return work()\n'
    paths = {'app/helpers.py'}
    assert imported_functions('app/check.py',text,[(3,4)],paths) == {}
    assert imported_functions('app/check.py',text,[(6,7)],paths) == {'app/helpers.py':{'work'}}
    assert imported_functions('app/check.py',text,[(5,6)],paths,direct_only=True) == {}
    assert imported_functions('app/check.py',text.replace('other():','other(work):'),[(6,7)],paths) == {}
    assert imported_functions('app/check.py',text.replace('import work','import *'),[(6,7)],paths) == {}
    assert imported_functions('app/check.py','invalid (',[(0,1)],paths) == {}


def test_definition_preserves_decorators_and_exact_large_function():
    text = 'from contextlib import contextmanager\n\n@contextmanager\ndef work():\n'+''.join(
        f'    value_{i} = {i}\n' for i in range(100))+'    yield value_99\n'
    context, = function_contexts(text,{'work'})
    assert context.spans[0] == (1,'from contextlib import contextmanager\n')
    assert context.spans[1] == (3,text[text.index('@contextmanager'):])
    assert function_contexts(text,{'unknown'}) == []


def test_priority_dependency_is_small_exact_and_never_executed(tmp_path):
    root, _ = repository(tmp_path)
    sentinel = root/'should-not-exist'
    source = f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("executed")\n\ndef expiry(value):\n    return value\n'
    source += ''.join(f'PAD_{i} = '+repr('x'*100)+'\n' for i in range(100))
    base = commit(root,{'reviewer/debt.py':source})
    head = commit(root,{'reviewer/check.py':'from .debt import expiry\n\ndef check(value):\n    return expiry(value)\n'})
    git = Repository(root)
    result = review(root,base,head)
    _,changes = git.snapshot(base,head)
    context = retrieve(git,result,changes,Limits())
    priority = context.fragments[context.required_count]
    assert priority.path == 'reviewer/debt.py' and 'def expiry(' in priority.text
    assert priority.text == source[:source.index('PAD_0')]
    assert priority.sha == head and len(priority.text.encode()) < 1500
    assert not sentinel.exists()


def test_import_candidate_outside_allowlist_is_not_read(tmp_path,monkeypatch):
    root, _ = repository(tmp_path)
    base = commit(root,{'secrets.py':'def hidden():\n    return "sensitive"\n'})
    head = commit(root,{'reviewer/check.py':'from secrets import hidden\n\ndef check():\n    return hidden()\n'})
    original = Repository.read
    def guarded(self,sha,path):
        assert path != 'secrets.py'
        return original(self,sha,path)
    monkeypatch.setattr(Repository,'read',guarded)
    git = Repository(root)
    result = review(root,base,head)
    _,changes = git.snapshot(base,head)
    context = retrieve(git,result,changes,Limits())
    assert not any(f.path == 'secrets.py' for f in context.fragments)
