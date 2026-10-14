from datetime import datetime,timedelta,timezone
import json
import sys

from reviewer.decision_records import DecisionEnvelope
from reviewer.publishing import start
from tests.test_review_owner_authorize import request


def test_apply_stage_uses_authenticated_source_without_model_or_secret(tmp_path,monkeypatch):
    import scripts.github_review_owner as entry
    api,env,inputs,root,old,proofs,statuses = request(tmp_path,monkeypatch)
    now = datetime.now(timezone.utc)
    body = proofs.envelope
    body['decision']['created_at']=now.isoformat()
    body['decision']['proposal']['expires_at']=(now+timedelta(hours=1)).isoformat()
    record = DecisionEnvelope.model_validate(body)
    records = {record.id:record}
    monkeypatch.setattr('reviewer.owner_runtime.enabled',lambda *args:True)
    monkeypatch.setattr('reviewer.owner_runtime.read_journal',lambda *args,**kwargs:records)
    monkeypatch.setattr('reviewer.owner_publishing.read_journal',lambda *args,**kwargs:records)
    monkeypatch.setattr('reviewer.reuse.Actions',lambda api:proofs)
    monkeypatch.setattr(entry,'GitHub',lambda *args:api)
    monkeypatch.setattr(entry,'fetch_head',lambda *args:old.head_sha)
    event = tmp_path/'event.json';event.write_text(json.dumps({'inputs':inputs}))
    for key,value in {**env,'GITHUB_EVENT_PATH':str(event),'GITHUB_TOKEN':'test-token'}.items(): monkeypatch.setenv(key,value)
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.setattr(sys,'argv',['github_review_owner.py','apply'])
    monkeypatch.chdir(root)
    directory=root/'.review';directory.mkdir()
    (directory/'decision.json').write_text(record.model_dump_json())
    start(api,old.model_copy(update={'run_id':20}))
    assert entry.main()==0 and statuses[0]['state']=='success'
    raw=json.loads((directory/'result.json').read_text())
    assert raw['result']['trace']['model_calls']==0 and raw['result']['status']=='failure'
    assert raw['result']['findings']==proofs.review['result']['findings']
