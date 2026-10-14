import pytest

from reviewer.ci import analyze_snapshot,check_context
from tests.test_review_ci import Pass
from tests.test_review_owner_runtime import captured


def test_prepared_owner_source_reaches_analyze_without_model_factory(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs = captured(tmp_path,monkeypatch)
    def forbidden(): raise AssertionError('No model on authenticated cache')
    raw = analyze_snapshot(api,root,ctx,env,model_factory=forbidden,fetcher=lambda *args:ctx.head_sha)
    assert raw['result']['trace']['model_calls']==0
    missing = ctx.model_copy(update={'analysis_source':None,'analysis_origin':None})
    with pytest.raises(ValueError): check_context(root,missing,env)
    with pytest.raises(ValueError): analyze_snapshot(api,root,missing,env,model_factory=forbidden,fetcher=lambda *args:ctx.head_sha)


def test_explicit_fresh_reaches_bounded_normal_model_path(tmp_path,monkeypatch):
    api,ctx,env,root,records,proofs = captured(tmp_path,monkeypatch,fresh=True)
    created = []
    def factory(): created.append(True);return Pass()
    raw = analyze_snapshot(api,root,ctx,env,model_factory=factory,fetcher=lambda *args:ctx.head_sha)
    assert len(created)==1 and raw['result']['trace']['model_calls']==1
