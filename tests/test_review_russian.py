from copy import deepcopy
import json

from reviewer import review
from reviewer.report import markdown
from tests.test_review_semantic import Model, semantic_repository
from tests.test_reviewer import commit


def test_russian_semantic_report_preserves_original_code_and_normative_quotes(tmp_path):
    root,base=semantic_repository(tmp_path)
    head=commit(root,{'app/state/service.py':'def apply(state):\n    return None\n'})
    class Russian(Model):
        def generate_json(self,**kwargs):
            assert 'in Russian' in kwargs['messages'][0]['content']
            raw=json.loads(super().generate_json(**kwargs))
            for key in ('criteria','findings','decisions'):
                for item in raw.get(key,[]):
                    for field in ('reason','summary','scenario','impact','recommendation','counterargument'):
                        if field in item: item[field]='Переход теряет состояние; сохраните согласованный снимок'
            return json.dumps(raw,ensure_ascii=False)
    result=review(root,base,head,model=Russian())
    original=deepcopy(result.model_dump())
    body=markdown(result)
    assert result.status=='failure' and result.execution=='complete'
    assert result.trace['prompt_version']=='review-v7' and result.trace['model_calls']==2
    assert '## Замечания' in body and 'Рекомендация:' in body and 'Переход теряет состояние' in body
    assert result.findings[0].evidence.quote=='return None'
    assert result.findings[0].source.quote=='State transitions preserve the supplied state.'
    assert 'return None' in body and 'ARCH-STATE-PURE' in body
    assert result.model_dump()==original
