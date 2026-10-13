import json

import httpx
import pytest
from openai import OpenAI

from reviewer import review
import reviewer.deepseek as adapter
from tests.test_review_semantic import semantic_repository
from tests.test_reviewer import commit


@pytest.mark.parametrize('mistake', [None, 'range', 'contradiction', 'identity'])
def test_deepseek_uses_atomic_assessments_and_grounded_line_ranges(tmp_path, monkeypatch, mistake):
    calls = []
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        payload = json.loads(body['messages'][-1]['content'])
        head = next(f for f in payload['fragments'] if f['role'] == 'head' and f['kind'] == 'text')
        if mistake is None:
            assert head['text'].startswith('1| ')
            assert head['end_line'] == 6
            assert head['id'].startswith('F')
            changes = payload['causal_changes']['app/state/service.py']
            assert any('return state' in c.get('before_code', '') for c in changes)
            assert any('return None' in c.get('head_code', '') for c in changes)
        ref = dict(fragment_id=head['id'], start_line=3, end_line=3)
        if mistake == 'range':
            ref['end_line'] = 999
        if 'candidates' in payload:
            output = {'decisions': [dict(index=c['index'], rule_id=c['rule_id'], path=c['path'], verdict='confirmed', reason='The function drops state',
                counterargument='The caller still needs the supplied state', evidence=[ref]) for c in payload['candidates']]}
            if mistake == 'identity':
                output['decisions'][0]['rule_id'] = 'WRONG'
        else:
            output = {'assessments': [dict(**c, verdict='violation' if c['rule_id'] != 'BUGS' else 'pass',
                reason='Return value changed', evidence=[ref],
                finding=dict(severity='major', summary='State lost', scenario='apply receives state',
                    impact='Caller gets None', recommendation='Return state') if c['rule_id'] != 'BUGS' else None)
                for c in payload['criteria']], 'recommendations': []}
            if mistake == 'contradiction':
                output['assessments'][0]['finding'] = None
        return httpx.Response(200, json={'id':'test','object':'chat.completion','created':1,'model':'test',
            'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(output)},'finish_reason':'stop'}]})
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.setattr(adapter, 'OpenAI', lambda **kwargs: OpenAI(**kwargs,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    root, base = semantic_repository(tmp_path)
    head = commit(root, {'app/state/service.py': 'def apply(state):\n    # changed\n    return None\n\ndef other():\n    return None\n'})
    result = review(root, base, head, model=adapter.DeepSeekReviewModel())
    assert result.status == ('failure' if mistake is None else 'error')
    if mistake is None:
        assert len(calls) == 2
        assert result.findings[0].evidence.line == 3
        assert result.findings[0].evidence.quote.strip() == 'return None'
        assert 'ReviewResponse' in calls[0]['messages'][0]['content']


def test_transport_budget_stops_before_http_and_reports_unsent_request(monkeypatch):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(500)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.setattr(adapter, 'OpenAI', lambda **kwargs: OpenAI(**kwargs,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    model = adapter.DeepSeekReviewModel()
    from reviewer.semantic_models import DraftReview
    with pytest.raises(RuntimeError):
        model.generate_bounded_json(messages=[dict(role='system', content='Review'), dict(role='user', content='{"fragments": []}')],
                            max_tokens=1000, schema=DraftReview.model_json_schema(), context_bytes=1)
    assert requests == []
    assert model.last_input['request_sent'] is False


def test_source_expansion_is_not_charged_as_generated_output(tmp_path, monkeypatch):
    def handle(request):
        payload = json.loads(json.loads(request.content)['messages'][-1]['content'])
        head = next(f for f in payload['fragments'] if f['role'] == 'head' and f['kind'] == 'text')
        output = {'assessments': [dict(**c, verdict='pass', reason='State is still returned', finding=None,
            evidence=[dict(fragment_id=head['id'], start_line=1, end_line=52)]) for c in payload['criteria']],
            'recommendations': []}
        assert len(json.dumps(output).encode()) < 512 * 16
        return httpx.Response(200, json={'id':'test','object':'chat.completion','created':1,'model':'test',
            'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(output)},'finish_reason':'stop'}]})
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.setattr(adapter, 'OpenAI', lambda **kwargs: OpenAI(**kwargs,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    root, base = semantic_repository(tmp_path)
    source = ''.join(f'PAD_{i} = ' + repr('x' * 180) + '\n' for i in range(50)) + 'def apply(state):\n    return state\n'
    head = commit(root, {'app/state/service.py': source})
    from reviewer.semantic_models import Limits
    result = review(root, base, head, model=adapter.DeepSeekReviewModel(), limits=Limits(output_tokens=512, context_bytes=32_000))
    assert result.status == 'success'
    assert len(result.criteria[0].evidence[0].quote.encode()) > 512 * 16
    assert result.trace['model_calls'] == 1


@pytest.mark.parametrize('same_revision', [True, False])
def test_range_resolution_stays_within_supplied_immutable_file(same_revision):
    from reviewer.generation import ground_response
    from reviewer.semantic_models import DraftReview
    payload = {'fragments': [dict(id='one', path='app/code.py', sha='head', role='head', kind='text', line=1, text='first\nsecond\n'),
        dict(id='two', path='app/code.py', sha='head' if same_revision else 'old', role='head', kind='text', line=3, text='third\nfourth\n')]}
    raw = json.dumps({'assessments': [dict(path='app/code.py', rule_id='BUGS', verdict='pass', finding=None, reason='No defect',
        evidence=[dict(fragment_id='F1', start_line=3, end_line=3)])], 'recommendations': []})
    resolutions = []
    if same_revision:
        draft = json.loads(ground_response(raw, DraftReview.model_json_schema(), payload, {'F1':'one','F2':'two'}, resolutions))
        assert draft['criteria'][0]['evidence'][0]['fragment_id'] == 'two'
        assert resolutions[0]['requested'] == 'F1'
    else:
        with pytest.raises(ValueError):
            ground_response(raw, DraftReview.model_json_schema(), payload, {'F1':'one','F2':'two'}, resolutions)
