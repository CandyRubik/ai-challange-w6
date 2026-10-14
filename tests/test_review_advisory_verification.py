import json

import pytest

from reviewer import review
from tests.test_review_verification import Verifier
from tests.test_review_semantic import semantic_repository
from tests.test_reviewer import commit,repository


PATH='app/state/service.py'
CODE='def apply(state):\n    return None\n'


class Advisory(Verifier):
    mixed,rule=False,'BUGS'
    def generate_json(self,**kwargs):
        raw=json.loads(super().generate_json(**kwargs))
        if 'criteria' in raw:
            for c in raw['criteria']: c['verdict']='violation' if c['rule_id']==self.rule else 'pass'
            raw['findings'][0].update(rule_id=self.rule,severity='major')
            if self.mixed: raw['findings'].append({**raw['findings'][0],'severity':'critical','scenario':'Another uncertain path'})
        elif self.mixed: raw['decisions'][-1]['verdict']='inconclusive'
        return json.dumps(raw)


@pytest.mark.parametrize('verdict,status,count',[('rejected','success',0),('confirmed','success',1),('inconclusive','error',0)])
def test_advisory_counterproof(tmp_path,verdict,status,count):
    root,base=semantic_repository(tmp_path)
    head=commit(root,{PATH:CODE})
    model=Advisory(verdict)
    result=review(root,base,head,model=model)
    assert result.status==status and len(result.findings)==count and model.calls==2
    assert result.trace['verification'][0]['verdict']==verdict
    assert not any(f.blocking for f in result.findings)


def test_static_blocker_survives_rejected_advisory(tmp_path):
    root,base=repository(tmp_path)
    head=commit(root,{PATH:'import sqlite3\n\n'+CODE})
    result=review(root,base,head,model=Advisory('rejected'))
    assert result.status=='failure' and len(result.findings)==1
    assert result.findings[0].blocking and result.findings[0].rule_id=='ARCH-STATE-PURE'


@pytest.mark.parametrize('rule',['BUGS','ARCH-STATE-PURE'])
def test_mixed_uncertainty_is_consistent(tmp_path,rule):
    root,base=semantic_repository(tmp_path)
    if rule!='BUGS':
        profile=json.loads((root/'.ai-review/profile.json').read_text());profile['rules'][0]['level']='should'
        base=commit(root,{'.ai-review/profile.json':json.dumps(profile)})
    head=commit(root,{PATH:CODE})
    model=Advisory('confirmed');model.mixed,model.rule=True,rule
    result=review(root,base,head,model=model)
    assert result.status=='error' and result.execution=='partial' and len(result.findings)==1
    assert next(c for c in result.criteria if c.rule_id==rule).verdict=='violation'
    assert result.limitations and not result.findings[0].blocking
