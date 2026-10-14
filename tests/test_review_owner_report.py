from datetime import datetime,timedelta,timezone
import json
import subprocess
import sys

from reviewer.decisions import OwnerProposal,effects,fingerprint,propose
from reviewer.models import ReviewResult
from reviewer.owner_report import debt_note,finding_note,journal,render
from tests.review_debt_fixtures import setup
from tests.test_review_publishing import fixture

NOW = datetime(2030,1,1,tzinfo=timezone.utc)


def accepted(ctx,result,action,targets,*,reason='Accepted explicitly'):
    p = OwnerProposal(action=action,targets=targets,reason=reason,expires_at=(NOW+timedelta(days=1)).isoformat())
    return propose(ctx,result,p,'maintainer',now=NOW)


def test_effective_report_distinguishes_original_and_remaining_blockers(tmp_path):
    _,ctx,raw,*_ = fixture(tmp_path,extra={'app/state/other.py':'import sqlite3\n'})
    result = ReviewResult.model_validate(raw['result'])
    original = json.dumps(raw,sort_keys=True)
    d = accepted(ctx,result,'dismiss',[fingerprint(result.findings[0])],reason='@maintainer <script> accepted')
    rendered,plan = render(ctx,raw,{'20-1':d},now=NOW)
    assert rendered['state']=='failure' and len(plan.remaining_findings)==1
    assert 'Effective check: **failure**' in rendered['body'] and 'Original analysis' in rendered['body']
    assert '@maintainer' not in rendered['body'] and '<script>' not in rendered['body']
    assert json.dumps(raw,sort_keys=True)==original
    note = finding_note(result.findings,plan,{'20-1':d})
    assert '20-1' in note and fingerprint(result.findings[0]) in note and 'preserved' in note


def test_expired_acceptance_is_not_presented_as_current(tmp_path):
    _,ctx,raw,*_ = fixture(tmp_path)
    result = ReviewResult.model_validate(raw['result'])
    d = accepted(ctx,result,'dismiss',[fingerprint(result.findings[0])])
    rendered,plan = render(ctx,raw,{'20-1':d},now=NOW+timedelta(days=1))
    assert rendered['state']=='failure' and not plan.accepted_findings
    assert 'No applicable owner acceptance' in rendered['body']
    assert not finding_note(result.findings,plan,{'20-1':d})


def test_waived_debt_is_open_in_original_and_explicitly_accepted(tmp_path):
    _,ctx,raw,*_ = setup(tmp_path)
    result = ReviewResult.model_validate(raw['result'])
    d = accepted(ctx,result,'waive_debt',['state-storage'])
    rendered,plan = render(ctx,raw,{'20-1':d},now=NOW)
    assert rendered['state']=='success' and result.project_debt[0].verdict=='open'
    assert 'original analysis: **failure**' in rendered['body']
    assert 'Owner acceptance' in debt_note('state-storage',plan,{'20-1':d})
    assert result.status=='failure' and 'fixed' not in debt_note('state-storage',plan,{'20-1':d})


def test_large_journal_retains_head_and_all_artifact_links(tmp_path):
    _,ctx,raw,*_ = fixture(tmp_path)
    result = ReviewResult.model_validate(raw['result'])
    d = accepted(ctx,result,'dismiss',[fingerprint(result.findings[0])],reason='Ж'*2000)
    records = {f'{i}-1':d for i in range(1,101)}
    body = journal(ctx,records,effects(ctx,result,records,now=NOW))
    assert len(body.encode())<=58_000 and ctx.head_sha in body and ctx.run_url in body
    assert 'presentation shortened' in body and '/runs/100/attempts/1' in body


def test_cli_markdown_is_explicitly_offline_preview(tmp_path):
    _,ctx,raw,*_ = fixture(tmp_path)
    path = tmp_path/'artifact.json';path.write_text(json.dumps(raw))
    result = subprocess.run([sys.executable,'scripts/preview_review_decision.py','--artifact',str(path),'--markdown'],capture_output=True,text=True)
    assert result.returncode==0 and result.stdout.startswith('OFFLINE PREVIEW — no CI authority.')
    assert 'Project review' in result.stdout and not result.stderr
