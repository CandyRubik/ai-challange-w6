import json

import httpx
import pytest
from openai import OpenAI

from reviewer import review
import reviewer.deepseek as adapter
from tests.test_review_semantic import Model, semantic_repository, wire_response
from tests.test_reviewer import commit


@pytest.mark.parametrize('failure,expected_calls', [('initial',3), ('verification',3), ('both',4), ('exhausted',2)])
def test_invalid_response_has_one_visible_retry_per_stage(tmp_path, monkeypatch, failure, expected_calls):
    requests, failed = [], set()
    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        payload = json.loads(body['messages'][-1]['content'])
        stage = 'verification' if 'candidates' in payload else 'initial'
        invalid = failure == 'exhausted' or (failure in (stage, 'both') and stage not in failed)
        failed.add(stage)
        content = '{ invalid JSON' if invalid else wire_response(Model().generate_json(
            messages=body['messages'], max_tokens=body['max_tokens'], schema={}), payload)
        return httpx.Response(200, json={'id':'test','object':'chat.completion','created':1,'model':'test',
            'usage':{'prompt_tokens':50,'completion_tokens':11,'total_tokens':61},
            'choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}]})
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-key')
    monkeypatch.setattr(adapter,'OpenAI',lambda **kwargs: OpenAI(**kwargs,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    root, base = semantic_repository(tmp_path)
    head = commit(root, {'app/state/service.py':'def apply(state):\n    return None\n'})
    result = review(root, base, head, model=adapter.DeepSeekReviewModel())
    assert result.status == ('error' if failure == 'exhausted' else 'failure')
    assert len(requests) == result.trace['model_calls'] == expected_calls
    assert len(result.trace['token_usage']) == expected_calls
    assert len(result.trace['wire_inputs']) == expected_calls
    assert sum(u['total_tokens'] for u in result.trace['token_usage']) == 61 * expected_calls
    assert result.trace['response_retries']
    for body in requests:
        if 'RESPONSE_FORMAT_RETRY' in body['messages'][0]['content']:
            assert body['temperature'] == 0
    if failure == 'exhausted':
        assert not result.findings


def test_generic_json_model_retries_structural_output_without_changing_its_signature(tmp_path):
    class Generic:
        name = 'generic-original-interface'
        def __init__(self): self.calls = 0
        def generate_json(self, *, messages, max_tokens, schema):
            self.calls += 1
            return '{ invalid JSON' if self.calls == 1 else Model().generate_json(messages=messages,max_tokens=max_tokens,schema=schema)
    root, base = semantic_repository(tmp_path)
    head = commit(root, {'app/state/service.py':'def apply(state):\n    return None\n'})
    model = Generic()
    assert review(root, base, head, model=model).status == 'failure'
    assert model.calls == 3
    from reviewer.semantic_models import Limits
    size = review(root, base, head, model=Model()).trace['context_bytes']
    limited = Generic()
    result = review(root, base, head, model=limited, limits=Limits(context_bytes=size))
    assert result.status == 'error'
    assert limited.calls == result.trace['model_calls'] == 1


@pytest.mark.parametrize('envelope', [{}, {'choices':None}, {'choices':[{'index':0,'message':None,'finish_reason':'stop'}]}, None])
def test_invalid_api_envelope_returns_error_instead_of_crashing(tmp_path, monkeypatch, envelope):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json=envelope)
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-key')
    monkeypatch.setattr(adapter,'OpenAI',lambda **kwargs: OpenAI(**kwargs,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    root, base = semantic_repository(tmp_path)
    head = commit(root, {'app/state/service.py':'def apply(state):\n    return None\n'})
    result = review(root, base, head, model=adapter.DeepSeekReviewModel())
    assert result.status == 'error'
    assert len(requests) == 2
