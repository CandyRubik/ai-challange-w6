import json
from pathlib import Path
import subprocess
import sys

import pytest

from reviewer.onboarding import facts,policy_template
from tests.test_reviewer import commit,git,repository

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/onboard_review.py'


def test_facts_use_committed_data_and_report_unsupported_paths_without_execution(tmp_path):
    root,base=repository(tmp_path)
    head=commit(root,{'requirements.txt':'fastapi==0.115\n--index-url https://PRIVATE_VALUE@invalid.test\n',
        'src/server.py':'import fastapi\nfrom .generation import prompt\nfrom .. import config\nraise RuntimeError("Never execute")\ndef handle(): pass\n',
        'app/Worker.kt':'class Worker\n',
        'private.env':'PRIVATE_VALUE=secret\n'})
    result=facts(root,head)
    assert dict(name='fastapi',path='requirements.txt') in result['dependencies']
    assert 'src/server.py' in result['unsupported_review_paths']
    assert 'app/Worker.kt' in result['unsupported_review_paths']
    assert next(m for m in result['modules'] if m['path']=='src/server.py')['symbols'][0]['name']=='handle'
    assert next(m for m in result['modules'] if m['path']=='src/server.py')['imports']==['..','.generation','fastapi']
    assert 'PRIVATE_VALUE' not in json.dumps(result) and result['authority']=='facts-only-not-project-rules'
    assert facts(root,head,file_limit=1)['omissions']


def test_policy_template_is_deterministic_explicit_local_and_has_no_runtime_claim(tmp_path):
    root,base=repository(tmp_path)
    first=policy_template(root,base,root/'.ai-review/profile.json')
    assert first==policy_template(root,base,root/'.ai-review/profile.json')
    assert not first['complete_pr_payload'] and first['profile_origin']=='explicit_local'
    assert first['authority']=='draft-preview-only' and not first['unresolved_normative_rules']
    generation=json.loads(first['generation_messages'][1]['content'])
    assert generation['policy'][0]['id']=='ARCH-STATE-PURE' and generation['fragments']==[]
    assert 'OUTPUT_JSON_SCHEMA' in first['verification_messages'][0]['content']


def test_unapproved_sources_are_visible_and_reserved_rule_is_rejected(tmp_path):
    root,base=repository(tmp_path)
    profile=json.loads((root/'.ai-review/profile.json').read_text())
    profile['rules'][0]['source']['quote']='Unapproved new norm'
    draft=root/'draft.json';draft.write_text(json.dumps(profile))
    assert policy_template(root,base,draft)['unresolved_normative_rules']==['ARCH-STATE-PURE']
    profile['rules'][0]['id']='BUGS';draft.write_text(json.dumps(profile))
    with pytest.raises(ValueError,match='reserved'): policy_template(root,base,draft)
    credential=root/'private.env';credential.write_text(json.dumps(profile))
    with pytest.raises(ValueError,match='credential'): policy_template(root,base,credential)


@pytest.mark.parametrize('kind',['canonical','symlink','hardlink','directory-symlink'])
def test_cli_cannot_overwrite_canonical_profile_through_draft_path(tmp_path,kind):
    root,base=repository(tmp_path)
    canonical=root/'.ai-review/profile.json';original=canonical.read_bytes()
    output=canonical
    if kind=='directory-symlink':
        (root/'.ai-review/drafts').symlink_to(root/'.ai-review',target_is_directory=True)
        output=root/'.ai-review/drafts/profile.json'
    elif kind!='canonical':
        output=root/'.ai-review/drafts/profile.json';output.parent.mkdir()
        if kind=='symlink': output.symlink_to(canonical)
        else: output.hardlink_to(canonical)
    result=subprocess.run([sys.executable,str(SCRIPT),'facts','--revision',base,'--output',str(output)],cwd=root,capture_output=True,text=True)
    assert result.returncode==(0 if kind=='hardlink' else 2)
    assert canonical.read_bytes()==original


def test_second_python_repo_can_detect_stack_and_preview_an_approved_answer_deck(tmp_path):
    root,base=repository(tmp_path)
    profile=json.loads((root/'.ai-review/profile.json').read_text());profile['project']='tiny-python-api'
    head=commit(root,{'requirements.txt':'fastapi\npydantic\n','app/api.py':'from fastapi import FastAPI\napp=FastAPI()\n'})
    draft=root/'.ai-review/drafts/profile.json';draft.parent.mkdir();draft.write_text(json.dumps(profile))
    before=(root/'.ai-review/profile.json').read_bytes()
    for stage,extra in [('facts',[]),('prompt',['--profile',str(draft)])]:
        result=subprocess.run([sys.executable,str(SCRIPT),stage,'--revision',head,*extra],cwd=root,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    output=json.loads((draft.parent/'prompt.json').read_text())
    assert output['profile']['project']=='tiny-python-api' and not output['unresolved_normative_rules']
    assert (root/'.ai-review/profile.json').read_bytes()==before
