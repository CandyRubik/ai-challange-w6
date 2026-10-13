import json
from pathlib import Path

from reviewer.evaluation import evaluate, select_cases, summarize
from reviewer.models import Evidence, Finding, ReviewResult


CASES = Path(__file__).resolve().parents[1] / '.ai-review/eval/cases.json'


def test_quality_counts_keep_incomplete_separate_from_clean_reviews():
    finding = Finding(rule_id='INV', blocking=True, message='Lost state',
                      evidence=Evidence(path='app/test.py', quote='return None', sha='a', line=2))
    case = {'expected': [{'rule_id': 'INV', 'path': 'app/test.py', 'quote': 'return None'}]}
    rows = [summarize(case, ReviewResult(status='failure', findings=[finding])),
            summarize({'expected': []}, ReviewResult(status='failure', findings=[finding])),
            summarize({'expected': []}, ReviewResult(status='error', execution='partial'))]
    from reviewer.evaluation import metrics
    summary = metrics(rows)
    assert (summary['tp'], summary['fp'], summary['fn'], summary['incomplete']) == (1, 1, 0, 1)
    assert summary['precision'] == 0.5
    assert summary['clean_blocked'] == 1
    assert summary['clean_passed'] == 0


def test_versioned_cases_have_a_separate_holdout_and_can_run_without_network():
    corpus = json.loads(CASES.read_text())
    assert len(corpus['cases']) >= 20
    development = select_cases(corpus, 'development')
    holdout = select_cases(corpus, 'holdout')
    assert holdout and {c['id'] for c in development}.isdisjoint(c['id'] for c in holdout)
    report = evaluate(corpus, 'holdout', lambda: None)
    assert report['case_set_sha256']
    assert report['case_set_version'] == corpus['version']
    assert report['summary']['incomplete'] == len(holdout)
    assert not report['quality_gate_passed']


def test_removed_guard_matches_gold_alternative_and_supporting_evidence():
    removed = Evidence(path='app/service.py', sha='old', line=2, quote='if not approved:\n        return')
    current = Evidence(path='app/service.py', sha='new', line=2, quote='publish()')
    finding = Finding(rule_id='INV', blocking=True, message='Missing approval guard', evidence=removed,
                      supporting_evidence=[current])
    for expected in [dict(rule_id='INV', path='app/service.py', quote='publish()'),
                     dict(rule_id='INV', path='app/service.py', quote='unused', alternatives=['if not approved:'])]:
        row = summarize({'expected': [expected]}, ReviewResult(status='failure', findings=[finding]))
        assert (row['tp'], row['fp'], row['fn']) == (1, 0, 0)
