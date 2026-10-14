import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from reviewer.git import Repository
from reviewer.inline import plan_inline
from reviewer.models import Evidence, ReviewResult
from reviewer.publishing import capture, make_artifact
from tests.test_review_publishing import fixture
from tests.test_reviewer import commit, git


def setup(tmp_path):
    api, context, artifact, pr, calls, statuses, root = fixture(tmp_path)
    result, repository = ReviewResult.model_validate(artifact['result']), Repository(root)
    contents = repository.read(context.head_sha, 'app/state/service.py').encode()
    blob = hashlib.sha1(b'blob '+str(len(contents)).encode()+b'\0'+contents).hexdigest()
    files = [dict(filename='app/state/service.py', status='modified', sha=blob,
        patch='@@ -1,2 +1,2 @@\n-def apply(state):\n-    return state\n+import sqlite3\n+# @maintainer')]
    return api, context, result, repository, files, pr


def test_head_anchor_and_two_rules_on_one_line_make_one_comment(tmp_path):
    api, context, result, repo, files, pr = setup(tmp_path)
    second = result.findings[0].model_copy(update={'rule_id':'BUGS','source':None,'blocking':False})
    result = result.model_copy(update={'findings':[*result.findings, second]})
    groups, fallback = plan_inline(context, result, files, repo)
    assert not fallback and len(groups) == 1
    assert groups[0]['anchor'] == dict(path='app/state/service.py', side='RIGHT', line=1)
    assert 'ARCH-STATE-PURE' in groups[0]['body'] and 'BUGS' in groups[0]['body']
    assert '@maintainer' not in groups[0]['body']


@pytest.mark.parametrize('bad', ['sha', 'truncated', 'missing', 'quote', 'caller', 'metadata'])
def test_unprovable_anchors_fall_back_to_summary(tmp_path, bad):
    api, context, result, repo, files, pr = setup(tmp_path)
    finding = result.findings[0]
    if bad == 'sha':
        files[0]['sha'] = '0'*40
    elif bad == 'truncated':
        files[0]['patch'] = '@@ -1,2 +1,2 @@\n+import sqlite3'
    elif bad == 'missing':
        files[0].pop('patch')
    elif bad == 'quote':
        finding = finding.model_copy(update={'evidence':finding.evidence.model_copy(update={'quote':'not present'})})
    elif bad == 'caller':
        finding = finding.model_copy(update={'evidence':Evidence(path='app/unchanged.py',sha=context.head_sha,line=1,quote='call()')})
    else:
        finding = finding.model_copy(update={'evidence':finding.evidence.model_copy(update={'kind':'file_metadata','line':0})})
    groups, fallback = plan_inline(context, result.model_copy(update={'findings':[finding]}), files, repo)
    assert not groups and fallback


def test_before_anchor_uses_left(tmp_path):
    api, context, result, repo, files, pr = setup(tmp_path)
    ref = Evidence(path='app/state/service.py',sha=context.merge_base_sha,line=2,quote='    return state')
    finding = result.findings[0].model_copy(update={'evidence':ref})
    groups, fallback = plan_inline(context, result.model_copy(update={'findings':[finding]}), files, repo)
    assert groups[0]['anchor']['side']=='LEFT' and groups[0]['anchor']['line']==2


@pytest.mark.parametrize('repeated', [False, True])
def test_stale_patch_cannot_anchor_a_line_unchanged_against_current_base(tmp_path, repeated):
    api, context, result, repo, files, pr = setup(tmp_path)
    path = files[0]['filename']
    before = 'pass\n'*300 if repeated else 'import json\n\ndef apply(state):\n    return state\n'
    after = 'pass\n'*150+'added = 1\n'+'pass\n'*150 if repeated else before.replace('return state','return None')
    pr['base']['sha'] = commit(repo.root,{path:before})
    pr['head']['sha'] = commit(repo.root,{path:after})
    context = capture(api,repo.root,4,run_id=10,run_attempt=1)
    raw = after.encode()
    files[0].update(sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),
        patch='@@ -199 +200 @@\n-old_from_a_different_base\n+pass' if repeated else '@@ -0,0 +1 @@\n+import json')
    finding = result.findings[0].model_copy(update={'source':None,
        'evidence':Evidence(path=path,sha=context.head_sha,line=200 if repeated else 1,quote='pass\n' if repeated else 'import json\n')})
    groups, fallback = plan_inline(context,result.model_copy(update={'findings':[finding]}),files,repo)
    assert not groups and fallback


@pytest.mark.parametrize('deleted', [False, True])
def test_real_rename_and_delete_keep_before_evidence_on_left(tmp_path, deleted):
    api, context, result, repo, files, pr = setup(tmp_path)
    root, old = repo.root, 'app/state/service.py'
    if deleted:
        git(root,'rm',old)
        head = commit(root,{'README.md':repo.read(context.base_sha,'README.md')+'\n'})
        path, status = old, 'removed'
        contents = repo.read(context.merge_base_sha,old)
        patch = '@@ -1,2 +0,0 @@\n-def apply(state):\n-    return state'
    else:
        path, status = 'app/state/renamed.py', 'renamed'
        git(root,'mv',old,path)
        head = commit(root,{path:'def apply(state):\n    return None\n'})
        contents = repo.read(head,path)
        patch = '@@ -1,2 +1,2 @@\n def apply(state):\n-    return state\n+    return None'
    pr['head']['sha'] = head
    context = capture(api,root,4,run_id=10,run_attempt=1)
    raw = contents.encode()
    file = dict(filename=path,status=status,sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),patch=patch)
    if not deleted:
        file['previous_filename'] = old
    ref = Evidence(path=old,sha=context.merge_base_sha,line=2,quote='    return state')
    finding = result.findings[0].model_copy(update={'source':None,'evidence':ref})
    groups,fallback = plan_inline(context,result.model_copy(update={'findings':[finding]}),[file],repo)
    assert not fallback and groups[0]['anchor']==dict(path=path,side='LEFT',line=2)


def test_multihunk_absolute_lines_and_unicode_are_preserved():
    from reviewer.inline import patch_lines
    patch='@@ -2 +2 @@\n-старое\n+новое\n@@ -20,2 +20,2 @@\n keep\n-old\n+new\n\\ No newline at end of file'
    assert patch_lines(patch)=={'LEFT':{2:'старое',21:'old'},'RIGHT':{2:'новое',21:'new'}}


def test_inline_limit_keeps_remaining_findings_in_summary(tmp_path):
    api, context, result, repo, files, pr = setup(tmp_path)
    path = files[0]['filename']
    contents = ''.join(f'value_{i} = {i}\n' for i in range(11))
    pr['head']['sha'] = commit(repo.root,{path:contents})
    context = capture(api,repo.root,4,run_id=10,run_attempt=1)
    raw = contents.encode()
    files[0].update(sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),
                    patch='@@ -1,2 +1,11 @@\n-def apply(state):\n-    return state\n'+''.join('+'+line+'\n' for line in contents.splitlines()))
    findings = [result.findings[0].model_copy(update={'evidence':Evidence(path=path,sha=context.head_sha,line=i+1,quote=line)})
                for i,line in enumerate(contents.splitlines())]
    groups,fallback = plan_inline(context,result.model_copy(update={'findings':findings}),files,repo)
    assert len(groups)==10 and fallback==['Достигнут лимит inline-комментариев']


def test_cli_shows_verified_plan_without_credentials_or_network(tmp_path):
    api, context, result, repo, files, pr = setup(tmp_path)
    artifact, root = make_artifact(context,result), repo.root
    for name,data in [('context.json',context.model_dump()),('result.json',artifact),('files.json',files)]:
        (tmp_path/name).write_text(json.dumps(data))
    command = [sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/publish_review.py'),
        '--context',str(tmp_path/'context.json'),'--artifact',str(tmp_path/'result.json'),
        '--inline-plan',str(tmp_path/'files.json'),'--repo',str(root)]
    env = {k:v for k,v in os.environ.items() if not k.startswith(('GITHUB_','DEEPSEEK_'))}
    run = subprocess.run(command,env=env,capture_output=True,text=True)
    assert run.returncode==1 and not run.stderr
    assert json.loads(run.stdout)['comments'][0]['line']==1
    invalid = subprocess.run([*command,'--publish'],env=env,capture_output=True,text=True)
    assert invalid.returncode==2 and 'not allowed' in invalid.stderr
