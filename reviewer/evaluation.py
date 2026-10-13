"""Reproducible, explicitly invoked review calibration; reviewed code is never executed."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from . import review
from .semantic_models import Limits
from .models import Record, Text, Finding, ReviewResult
from .retrieval import readable
from pydantic import Field


def case_set_sha256(corpus):
    return hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class Adjudication(Record):
    case_id: Text
    context_sha256: Text
    finding_sha256: Text
    expected_rule_id: Text
    reason: Text


class Annotations(Record):
    schema_version: int = Field(ge=1, le=1)
    case_set_sha256: Text
    entries: list[Adjudication] = Field(max_length=100)
    reviewed_by: list[Text] = Field(default_factory=list)


def select_cases(corpus, split):
    if corpus['version'] not in (1, 2, 3) or split not in ('development', 'holdout', 'all'):
        raise ValueError('Unknown case set version or split')
    cases = corpus['cases']
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate case IDs')
    return [c for c in cases if split == 'all' or c['split'] == split]


def summarize(case, result):
    blockers = [f for f in result.findings if f.blocking]
    remaining = list(case['expected'])
    tp, duplicates, unmatched = 0, 0, []
    matched = []
    for finding in blockers:
        match = next((e for e in remaining if e['rule_id'] == finding.rule_id and e['path'] == finding.evidence.path and any(ref.path == e['path'] and any(quote in ref.quote for quote in [e['quote'], *e.get('alternatives', [])])
                          for ref in [finding.evidence, *finding.supporting_evidence])), None)
        if match is not None:
            remaining.remove(match)
            tp += 1
            matched.append((match, finding))
        else:
            unmatched.append(finding)
    false_positive_ids = []
    for finding in unmatched:
        if any(finding.rule_id in expected.get('duplicate_rules', []) and (finding.evidence == original.evidence or finding.paired_anchor_id and finding.paired_anchor_id == original.paired_anchor_id)
               for expected, original in matched):
            duplicates += 1
        else:
            false_positive_ids.append(fingerprint(finding.model_dump()))
    return {'id': case.get('id'), 'tp': tp, 'fp': len(blockers) - tp - duplicates, 'fn': len(remaining), 'duplicates': duplicates,
            'matched_rule_ids': [expected['rule_id'] for expected, _ in matched], 'false_positive_ids': false_positive_ids,
            'clean': not case['expected'], 'blocked': bool(blockers), 'status': result.status,
            'incomplete': result.execution != 'complete' or result.status == 'error',
            'trace': result.trace, 'profile_sha256': result.profile_sha256,
            'snapshot': result.snapshot.model_dump() if result.snapshot else None,
            'findings': [f.model_dump() for f in blockers], 'limitations': result.limitations}


def metrics(rows):
    inputs = [wire for row in rows for wire in row.get('trace', {}).get('wire_inputs', [])]
    calls = sum(row.get('trace', {}).get('model_calls', 0) for row in rows)
    counts = {key: sum(row.get(key, 0) for row in rows) for key in ('tp', 'fp', 'fn', 'incomplete', 'duplicates')}
    return {**counts, 'cases': len(rows),
            'precision': counts['tp'] / (counts['tp'] + counts['fp']) if counts['tp'] + counts['fp'] else None,
            'recall': counts['tp'] / (counts['tp'] + counts['fn']) if counts['tp'] + counts['fn'] else None,
            'clean_blocked': sum(r['clean'] and r['blocked'] for r in rows),
            'clean_passed': sum(r['clean'] and r['status'] == 'success' and not r['incomplete'] for r in rows),
            'model_calls': calls,
            'api_calls': sum(wire['request_sent'] for wire in inputs)
                         if len(inputs) == calls and all(isinstance(wire, dict) and type(wire.get('request_sent')) is bool for wire in inputs) else None,
            'elapsed_ms': sum(r.get('trace', {}).get('elapsed_ms', 0) for r in rows),
            'token_usage': [usage for r in rows for usage in r.get('trace', {}).get('token_usage', [])]}


def quality_gate(summary):
    return (summary['cases'] > 0 and summary['incomplete'] == 0 and summary['fn'] == 0
            and summary['clean_blocked'] == 0 and (summary['precision'] or 0) >= 0.95)


def refresh_row(case, old):
    result = ReviewResult(status=old['status'], execution='partial' if old['incomplete'] else 'complete',
        snapshot=old['snapshot'], profile_sha256=old['profile_sha256'],
        findings=[Finding.model_validate(f) for f in old['findings']], trace=old['trace'], limitations=old['limitations'])
    return summarize(case, result)


def prepare_report(report, corpus):
    if report['case_set_sha256'] != case_set_sha256(corpus):
        raise ValueError('Report case-set mismatch')
    cases = {c['id']: c for c in select_cases(corpus, report['split'])}
    if len(report['results']) != len(cases) or {r['id'] for r in report['results']} != set(cases):
        raise ValueError('Report case IDs mismatch')
    prepared = json.loads(json.dumps(report))
    prepared['source_report_sha256'] = fingerprint(report)
    prepared.setdefault('raw_summary', prepared['summary'])
    prepared['case_ids'] = list(cases)
    prepared['results'] = [refresh_row(cases[row['id']], row) for row in prepared['results']]
    prepared['summary'] = metrics(prepared['results'])
    prepared['quality_gate_passed'] = quality_gate(prepared['summary'])
    return prepared


def adjudicate(report, annotations, *, corpus=None):
    if corpus is not None:
        report = prepare_report(report, corpus)
    if 'case_ids' not in report or any(not {'false_positive_ids','matched_rule_ids'}.issubset(row) for row in report['results']):
        raise ValueError('Use prepare_report with the matching corpus before adjudication')
    if len(report['results']) != len(report['case_ids']) or {r['id'] for r in report['results']} != set(report['case_ids']):
        raise ValueError('Report case IDs mismatch')
    labels = Annotations.model_validate(annotations)
    if labels.case_set_sha256 != report['case_set_sha256']:
        raise ValueError('Annotation case-set mismatch')
    graded = json.loads(json.dumps(report))
    graded.setdefault('raw_summary', graded['summary'])
    rows = {row['id']: row for row in graded['results']}
    graded['unused_annotations'] = []
    for entry in labels.entries:
        row = rows.get(entry.case_id)
        if row is None or row['trace'].get('context_sha256') != entry.context_sha256:
            graded['unused_annotations'].append(entry.model_dump())
            continue
        finding = next((f for f in row['findings'] if fingerprint(f) == entry.finding_sha256), None)
        if finding is None or entry.finding_sha256 not in row['false_positive_ids']:
            graded['unused_annotations'].append(entry.model_dump())
            continue
        if finding['rule_id'] != 'BUGS' or entry.expected_rule_id == 'BUGS' or entry.expected_rule_id not in row['matched_rule_ids'] or row['clean']:
            raise ValueError('Duplicate annotation needs a matched normative gold finding')
        row.setdefault('automatic_counts', {k:row[k] for k in ('tp','fp','fn','duplicates')})
        row['fp'] -= 1
        row['duplicates'] += 1
        row['false_positive_ids'].remove(entry.finding_sha256)
        row.setdefault('adjudications', []).append(entry.model_dump())
    graded['annotations_sha256'] = fingerprint(annotations)
    graded['summary'] = metrics(graded['results'])
    graded['quality_gate_passed'] = quality_gate(graded['summary'])
    return graded


def evaluation_plan(corpus, split, bounds, previous=None):
    dataset_hash = case_set_sha256(corpus)
    cases = select_cases(corpus, split)
    cached = {}
    if previous is not None:
        if previous['case_set_sha256'] != dataset_hash or previous['split'] != split or previous.get('limits') != bounds.model_dump():
            raise ValueError('Resume case-set, split or bounds mismatch')
        cached = {row['id']: row for row in previous['results']}
        if len(cached) != len(previous['results']) or set(cached) != {c['id'] for c in cases}:
            raise ValueError('Resume case IDs mismatch')
    return dataset_hash, cases, cached


def evaluate(corpus, split, model_factory, limits=None, *, previous=None):
    bounds = limits or Limits(context_bytes=32_000, output_tokens=2_000)
    dataset_hash, cases, cached = evaluation_plan(corpus, split, bounds, previous)
    rows, resumed, reused = [], [], []
    for case in cases:
        old = cached.get(case['id'])
        if old and not old['incomplete']:
            rows.append(refresh_row(case, old))
            reused.append(case['id'])
            continue
        with tempfile.TemporaryDirectory(prefix='review-eval-') as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.attributesFile=/dev/null', '-C', directory, *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-q')
            def commit(files):
                for name, contents in files.items():
                    path = root / name
                    if Path(name).is_absolute() or '..' in Path(name).parts or not readable(name):
                        raise ValueError('Invalid fixture path')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(contents)
                git('add', '.')
                git('-c', 'user.name=Review Eval', '-c', 'user.email=eval@invalid', 'commit', '-qm', 'fixture')
                return git('rev-parse', 'HEAD')
            base = commit({**case['before'], 'README.md': case['contract'],
                           '.ai-review/profile.json': json.dumps(case['profile'])})
            head = commit(case['after'])
            result = review(root, base, head, model=model_factory(), limits=bounds)
            row = summarize(case, result)
            if old:
                trace = row['trace']
                trace['prior_attempt'] = old
                for key in ('model_calls', 'elapsed_ms'):
                    trace[key] = trace.get(key, 0) + old['trace'].get(key, 0)
                for key in ('token_usage', 'wire_inputs'):
                    trace[key] = old['trace'].get(key, []) + trace.get(key, [])
                resumed.append(case['id'])
            rows.append(row)
    summary = metrics(rows)
    return {'case_set_version': corpus['version'], 'split': split, 'case_ids':[c['id'] for c in cases],
            'case_set_sha256': dataset_hash, 'limits': bounds.model_dump(),
            'resumed_cases': resumed, 'reused_cases': reused,
            'resume_of_sha256': fingerprint(previous) if previous else None,
            'summary': summary, 'results': rows,
            'quality_gate_passed': quality_gate(summary)}
