import json

import pytest

from reviewer.generation import ground_response
from reviewer.retrieval import windows
from reviewer.semantic_models import DraftReview


def response(start, stop):
    return json.dumps(dict(assessments=[dict(path='app/code.py',rule_id='BUGS',verdict='pass',
        finding=None,reason='No defect',evidence=[dict(fragment_id='F1',start_line=start,end_line=stop)])],
        recommendations=[]))


@pytest.mark.parametrize('ending', ['\n','\r\n'])
def test_contiguous_mandatory_windows_preserve_exact_bytes_and_ground_cross_boundary(ending):
    source = ''.join(f'value_{i} = {i}{ending}' for i in range(90))
    selected = windows('app/code.py','head',source,'head',[(60,61)])
    assert len(selected) == 1 and selected[0].text == source and selected[0].line == 1
    payload = dict(fragments=[f.model_dump() for f in selected])
    result = json.loads(ground_response(response(59,62),DraftReview.model_json_schema(),payload,{'F1':selected[0].id}))
    assert result['criteria'][0]['evidence'] == [dict(fragment_id=selected[0].id,line=59,end_line=62)]


def test_gaps_are_never_filled_and_unprovided_ranges_still_fail():
    source = ''.join(f'value_{i} = {i}\n' for i in range(210))
    selected = windows('app/code.py','head',source,'head',[(190,191)])
    assert [(f.line,len(f.text.splitlines())) for f in selected] == [(1,60),(181,30)]
    payload = dict(fragments=[f.model_dump() for f in selected])
    with pytest.raises(ValueError,match='supplied immutable source'):
        ground_response(response(59,182),DraftReview.model_json_schema(),payload,
                        {f'F{i+1}':f.id for i,f in enumerate(selected)})


def test_optional_code_and_docs_stay_in_bounded_ranked_windows():
    source = ''.join(f'value_{i} = {i}\n' for i in range(210))
    for path, role in [('app/code.py','head'),('docs/design.md','normative')]:
        selected = windows(path,'head',source,role)
        assert [f.line for f in selected] == [1,61,121,181]
    assert len(windows('docs/design.md','base',source,'normative',[(80,81)])) == 2


def test_full_function_and_decorators_keep_all_previously_required_source():
    source = 'def decorate(fn):\n    return fn\n\n@decorate\ndef large():\n'+''.join(f'    value_{i} = {i}\n' for i in range(130))
    selected = windows('app/code.py','head',source,'before',[(80,81)])
    assert len(selected) == 1 and selected[0].text == source
    assert selected[0].role == 'before' and selected[0].sha == 'head'
