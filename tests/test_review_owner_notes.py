from reviewer.debt_publishing import ASSESSMENT_MARKER,DEBT_MARKER,OWNER_DEBT_MARKER,publish_debt
from reviewer.inline_publishing import publish_inline
from reviewer.models import ReviewResult
from reviewer.publishing import assert_current,start
from tests.review_debt_fixtures import setup as debt_setup
from tests.test_review_inline_publishing import setup


def test_inline_note_wraps_original_and_is_removed_without_duplicate_review(tmp_path):
    api,ctx,result,repo,files,inline,reviews,writes,guard,pr = setup(tmp_path)
    publish_inline(api,ctx,result,repo,guard=guard)
    original = inline[0]['body'].partition('\n')[2]
    publish_inline(api,ctx,result,repo,guard=guard,owner_note=lambda findings:'Owner acceptance; reason and expiry')
    assert inline[0]['body'].endswith(original) and '--- Original report ---' in inline[0]['body']
    assert len(writes)==1 and len(inline)==1
    publish_inline(api,ctx,result,repo,guard=guard,owner_note=lambda findings:'')
    assert inline[0]['body'].partition('\n')[2]==original and len(writes)==1


def test_oversized_inline_note_keeps_original_and_uses_summary_fallback(tmp_path):
    api,ctx,result,repo,files,inline,reviews,writes,guard,pr = setup(tmp_path)
    fallback = publish_inline(api,ctx,result,repo,guard=guard,owner_note=lambda findings:'Ж'*40_000)
    assert fallback and 'Owner inline note' in fallback[0]
    assert '--- Original report ---' not in inline[0]['body'] and result.findings[0].message in inline[0]['body']


def test_debt_note_is_outside_original_and_does_not_nest_on_incomplete_repeat(tmp_path):
    api,ctx,raw,repo,issues,statuses,calls = debt_setup(tmp_path)
    start(api,ctx)
    guard = lambda:assert_current(api,ctx)
    result = ReviewResult.model_validate(raw['result'])
    publish_debt(api,ctx,result,guard=guard,owner_notes={'state-storage':'Owner acceptance; reason and expiry'})
    body = next(c['body'] for c in issues if c['body'].startswith(DEBT_MARKER))
    original = body.partition(ASSESSMENT_MARKER)[2].partition(OWNER_DEBT_MARKER)[0].strip()
    assert 'open' in original and 'Owner acceptance' not in original
    partial = result.model_copy(update={'status':'error','execution':'partial'})
    for _ in range(2):
        publish_debt(api,ctx,partial,guard=guard)
        body = next(c['body'] for c in issues if c['body'].startswith(DEBT_MARKER))
        assert body.partition(ASSESSMENT_MARKER)[2].strip()==original and 'Owner acceptance' not in body
