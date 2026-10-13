import copy

import pytest

from reviewer.evaluation import adjudicate, fingerprint, summarize
from reviewer.models import Evidence, Finding, ReviewResult


def report_fixture():
    ref = Evidence(path='app/service.py', sha='head', line=2, quote='state.save()')
    first = Finding(rule_id='INV', blocking=True, message='State outside transaction', evidence=ref)
    second = Finding(rule_id='BUGS', blocking=True, message='Partial turn commit', evidence=ref.model_copy(update={'line':3}))
    row = summarize({'id':'atomic','expected':[dict(rule_id='INV',path=ref.path,quote=ref.quote)]},
                    ReviewResult(status='failure',findings=[first,second],trace={'context_sha256':'context'}))
    report = dict(case_set_sha256='dataset',case_ids=['atomic'],results=[row],summary={'fp':1},quality_gate_passed=False)
    annotations = dict(schema_version=1,case_set_sha256='dataset',entries=[dict(case_id='atomic',
        context_sha256='context',finding_sha256=fingerprint(second.model_dump()),expected_rule_id='INV',
        reason='Same transaction defect, independently inspected')])
    return report, annotations


def test_exact_adjudication_preserves_raw_counts_and_cannot_apply_to_another_result():
    report, annotations = report_fixture()
    original = copy.deepcopy(report)
    graded = adjudicate(report, annotations)
    assert report == original
    assert graded['raw_summary']['fp'] == 1
    assert graded['summary']['fp'] == 0
    assert graded['summary']['duplicates'] == 1
    again = adjudicate(graded, annotations)
    assert again['raw_summary']['fp'] == 1
    assert again['summary']['duplicates'] == 1
    changed = copy.deepcopy(report)
    changed['results'][0]['findings'][1]['message'] = 'An unrelated false claim'
    assert adjudicate(changed, annotations)['summary']['fp'] == 1


def test_adjudication_cannot_supply_missing_gold_or_clear_incomplete():
    report, annotations = report_fixture()
    report['results'][0].update(incomplete=True,status='error')
    assert not adjudicate(report, annotations)['quality_gate_passed']
    report['results'][0]['matched_rule_ids'] = []
    with pytest.raises(ValueError):
        adjudicate(report, annotations)
    annotations['case_set_sha256'] = 'other dataset'
    with pytest.raises(ValueError):
        adjudicate(report, annotations)


def test_bug_gold_is_not_a_normative_gold_for_adjudication():
    report, annotations = report_fixture()
    report['results'][0]['matched_rule_ids'] = ['BUGS']
    annotations['entries'][0]['expected_rule_id'] = 'BUGS'
    with pytest.raises(ValueError):
        adjudicate(report, annotations)


def test_legacy_report_requires_explicit_preparation():
    report, annotations = report_fixture()
    report['results'][0].pop('false_positive_ids')
    with pytest.raises(ValueError,match='prepare_report'):
        adjudicate(report, annotations)


def test_saved_v4_can_be_prepared_without_erasing_failed_reviews():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root/'.scratch/ai-pr-review/calibration-run-v4.json').read_text())
    corpus = json.loads((root/'.ai-review/eval/cases.json').read_text())
    labels = json.loads((root/'.ai-review/eval/annotations-v4.json').read_text())
    graded = adjudicate(report, labels, corpus=corpus)
    assert graded['raw_summary']['fp'] == 2
    assert graded['summary']['fp'] == 0
    assert graded['summary']['duplicates'] == 4
    assert graded['summary']['incomplete'] == 3
    assert not graded['quality_gate_passed']
