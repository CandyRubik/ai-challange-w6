import json

import httpx
from openai import OpenAI
import pytest

import reviewer.deepseek as adapter
from reviewer.semantic_models import DraftReview, InvalidModelResponse


@pytest.mark.parametrize('mistake,code', [('json','schema'), ('missing','schema'),
    ('unknown','unknown_reference'), ('range','evidence_range'), ('blank','blank_evidence'),
    ('metadata','metadata_range'), ('length','incomplete_completion')])
def test_response_diagnostics_are_bounded_and_do_not_disclose_model_content(monkeypatch,mistake,code):
    secret = 'PRIVATE_SENTINEL_RESPONSE_VALUE'
    def handle(request):
        ref = dict(fragment_id='F1',start_line=1,end_line=1)
        if mistake == 'unknown': ref['fragment_id'] = secret
        if mistake == 'range': ref['end_line'] = 99
        if mistake == 'blank': ref.update(start_line=2,end_line=2)
        item = dict(path='app/code.py',rule_id='BUGS',verdict='pass',reason=secret,evidence=[ref],finding=None)
        if mistake == 'missing':
            del item['finding']; item[secret] = secret
        content = secret if mistake == 'json' else json.dumps(dict(assessments=[item],recommendations=[]))
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':content},
            'finish_reason':'length' if mistake == 'length' else 'stop'}]})
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-key')
    monkeypatch.setattr(adapter,'OpenAI',lambda **kwargs: OpenAI(**kwargs,
        http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    model = adapter.DeepSeekReviewModel()
    payload = dict(fragments=[dict(id='source',path='app/code.py',sha='head',role='head',
        kind='file_metadata' if mistake == 'metadata' else 'text',line=0 if mistake == 'metadata' else 1,text='code\n\n')])
    with pytest.raises(InvalidModelResponse):
        model.generate_bounded_json(messages=[dict(role='system',content='Review'),
            dict(role='user',content=json.dumps(payload))],max_tokens=1000,
            schema=DraftReview.model_json_schema(),context_bytes=32_000)
    failure = model.last_input['validation_failure']
    assert failure['code'] == code
    assert secret not in json.dumps(model.last_input)
    assert len(json.dumps(failure)) < 1000
    if mistake == 'missing':
        assert 'finding' in failure['fields'] and 'missing' in failure['types']
    model.client.close()
