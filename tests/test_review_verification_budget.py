import json
import pytest
from reviewer.semantic_models import Limits
from reviewer.verification import Verification,verification_input

def test_every_verifier_citation_must_survive_input_packing():
    from types import SimpleNamespace
    from reviewer.semantic_models import DraftReview
    from reviewer.verification import verify
    class Model:
        def measure_input(self,*,messages,schema): return len(messages[-1]['content'].encode())+5000
        def generate_json(self,**kwargs):
            return json.dumps(dict(decisions=[dict(index=0,verdict='confirmed',reason='Fixture',counterargument='Fixture',
                evidence=[dict(fragment_id='R',quote='head'),dict(fragment_id='O',quote='removed')])]))
    item=dict(path='app/state/service.py',rule_id='BUGS',severity='major',summary='Fixture',scenario='Fixture',impact='Fixture',recommendation='Fixture',evidence=[dict(fragment_id='R',quote='head')])
    draft=DraftReview(criteria=[],findings=[item],recommendations=[])
    payload=json.dumps(dict(fragments=[dict(id='R',role='head',text='head'),dict(id='O',role='head',text='removed'*3000)]))
    trace=dict(model_calls=0,token_usage=[],wire_inputs=[])
    with pytest.raises(ValueError,match='outside its packed input'):
        verify(draft,[SimpleNamespace(blocking=True)],[],payload,Model(),Limits(context_bytes=10_000),trace,lambda c:SimpleNamespace(),
            {'app/state/service.py':'head'},required_count=1)
    assert trace['model_calls']==1 and trace['verification_optional_fragments_omitted']==['O']


def test_verification_packer_preserves_mandatory_and_candidate_citations_by_actual_wire():
    class Wire:
        def measure_input(self,*,messages,schema): return len(json.dumps(messages,ensure_ascii=False).encode())+5000
    fragments=[dict(id='R',text='required'),dict(id='C',text='cited'),dict(id='O',text='optional'*2000)]
    original=json.dumps(fragments)
    candidates=[dict(index=0,evidence=[dict(fragment_id='C',quote='cited')])]
    trace={}
    data,body=verification_input(dict(fragments=fragments),candidates,Verification.model_json_schema(),Wire(),Limits(context_bytes=10_000),1,trace)
    assert data['fragments']==fragments[:2] and json.dumps(fragments)==original
    assert trace['verification_optional_fragments_omitted']==['O'] and trace['verification_input_budget_bytes']<=10_000
    assert json.loads(body)['candidates']==candidates


def test_verification_budget_cannot_drop_candidate_evidence_to_force_a_request():
    fragments=[dict(id='R',text='required'),dict(id='C',text='cited'*3000),dict(id='O',text='optional')]
    trace={}
    with pytest.raises(ValueError,match='Required verification'):
        verification_input(dict(fragments=fragments),[dict(index=0,evidence=[dict(fragment_id='C')])],
            Verification.model_json_schema(),object(),Limits(context_bytes=5_000),1,trace)
    assert trace['verification_input_budget_bytes']>5000 and trace['verification_optional_fragments_omitted']==['O']
