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


def test_supporting_evidence_cannot_change_the_causal_path_for_gold_matching():
    finding = Finding(rule_id='INV', blocking=True, message='Other defect',
        evidence=Evidence(path='app/other.py', sha='new', line=2, quote='return None'),
        supporting_evidence=[Evidence(path='app/service.py', sha='new', line=2, quote='publish()')])
    row = summarize({'expected': [dict(rule_id='INV', path='app/service.py', quote='publish()')]},
                    ReviewResult(status='failure', findings=[finding]))
    assert (row['tp'], row['fp'], row['fn']) == (0, 1, 1)


def test_unknown_and_mixed_transport_counts_are_not_zero():
    from reviewer.evaluation import metrics
    for inputs in ([None, None], [dict(request_sent=True), None], [{}, {}],
                   [dict(request_sent=True), dict(request_sent=None)]):
        row = summarize({'expected': []}, ReviewResult(trace=dict(model_calls=2, wire_inputs=inputs)))
        assert metrics([row])['api_calls'] is None


def test_confirmed_same_anchor_bug_is_a_duplicate_not_a_different_defect():
    from reviewer.evaluation import metrics
    from reviewer.report import finding_groups, markdown
    evidence = Evidence(path='app/service.py', sha='head', line=2, quote='publish()')
    policy = Finding(rule_id='INV', blocking=True, message='Approval removed', evidence=evidence)
    bug = Finding(rule_id='BUGS', blocking=True, message='Unapproved publish', evidence=evidence)
    case = {'expected': [dict(rule_id='INV', path='app/service.py', quote='publish()', duplicate_rules=['BUGS'])]}
    result = ReviewResult(status='failure', findings=[policy, bug])
    row = summarize(case, result)
    assert (row['tp'], row['fp'], row['fn'], row['duplicates']) == (1, 0, 0, 1)
    assert metrics([row])['duplicates'] == 1
    assert len(finding_groups(result.findings)) == 1
    findings_text = markdown(result).replace('## Замечания','## Findings').replace('## Покрытие','## Coverage').split('## Findings')[1].split('## Coverage')[0]
    assert findings_text.count('- **') == 1
    assert len(result.findings) == 2
    unrelated = bug.model_copy(update={'evidence': evidence.model_copy(update={'line': 3})})
    row = summarize(case, ReviewResult(status='failure', findings=[policy, unrelated]))
    assert (row['fp'], row['duplicates']) == (1, 0)


def test_same_opcode_does_not_hide_a_false_diagnosis_at_another_head_anchor():
    good = Finding(rule_id='INV', blocking=True, cause_id='one-opcode', message='State lost',
        evidence=Evidence(path='app/service.py', sha='head', line=2, quote='return None'))
    false = Finding(rule_id='BUGS', blocking=True, cause_id='one-opcode', message='Unreachable save executes',
        evidence=Evidence(path='app/service.py', sha='head', line=3, quote='save()'))
    row = summarize({'expected': [dict(rule_id='INV', path='app/service.py', quote='return None', duplicate_rules=['BUGS'])]},
                    ReviewResult(status='failure', findings=[good, false]))
    assert (row['tp'], row['fp'], row['duplicates']) == (1, 1, 0)


def test_resume_reuses_complete_cases_and_rejects_changed_inputs():
    corpus = json.loads(CASES.read_text())
    corpus['cases'] = [next(c for c in corpus['cases'] if c['id'] == 'INV-STATE-clean')]
    class Pass:
        name = 'controlled-pass'
        calls = 0
        def generate_json(self, *, messages, max_tokens, schema):
            Pass.calls += 1
            payload = json.loads(messages[-1]['content'])
            source = next(f for f in payload['fragments'] if f['role'] == 'head' and f['kind'] == 'text')
            return json.dumps({'criteria':[{**c,'verdict':'pass','reason':'Equivalent transition',
                'evidence':[dict(fragment_id=source['id'],quote=source['text'])]} for c in payload['criteria']],
                'findings':[],'recommendations':[]})
    first = evaluate(corpus, 'all', Pass)
    resumed = evaluate(corpus, 'all', Pass, previous=first)
    assert Pass.calls == 1
    assert resumed['reused_cases'] == ['INV-STATE-clean']
    changed = json.loads(json.dumps(corpus))
    changed['cases'][0]['contract'] += ' changed'
    import pytest
    with pytest.raises(ValueError):
        evaluate(changed, 'all', Pass, previous=first)


def test_eval_rejects_git_control_files_before_a_commit():
    corpus = json.loads(CASES.read_text())
    corpus['cases'] = [corpus['cases'][0]]
    corpus['cases'][0]['before']['.git/config'] = 'untrusted fixture configuration'
    import pytest
    with pytest.raises(ValueError,match='fixture path'):
        evaluate(corpus, 'all', lambda: None)


def test_resume_retries_incomplete_cases_without_erasing_prior_costs():
    corpus = json.loads(CASES.read_text())
    corpus['cases'] = [next(c for c in corpus['cases'] if c['id'] == 'INV-STATE-clean')]
    class Missing:
        name = 'controlled-failed'
        def generate_json(self, **kwargs):
            raise RuntimeError('unavailable')
    first = evaluate(corpus, 'all', Missing)
    assert first['summary']['incomplete'] == 1
    continued = evaluate(corpus, 'all', Missing, previous=first)
    assert continued['resumed_cases'] == ['INV-STATE-clean']
    assert continued['summary']['model_calls'] == 2
    assert continued['results'][0]['trace']['prior_attempt']['trace']['model_calls'] == 1
