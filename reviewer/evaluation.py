"""Reproducible, explicitly invoked review calibration; reviewed code is never executed."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from . import review
from .semantic_models import Limits


def select_cases(corpus, split):
    if corpus['version'] != 1 or split not in ('development', 'holdout', 'all'):
        raise ValueError('Unknown case set version or split')
    cases = corpus['cases']
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate case IDs')
    return [c for c in cases if split == 'all' or c['split'] == split]


def summarize(case, result):
    blockers = [f for f in result.findings if f.blocking]
    remaining = list(case['expected'])
    tp = 0
    for finding in blockers:
        match = next((e for e in remaining if e['rule_id'] == finding.rule_id and any(ref.path == e['path'] and any(quote in ref.quote for quote in [e['quote'], *e.get('alternatives', [])])
                          for ref in [finding.evidence, *finding.supporting_evidence])), None)
        if match is not None:
            remaining.remove(match)
            tp += 1
    return {'id': case.get('id'), 'tp': tp, 'fp': len(blockers) - tp, 'fn': len(remaining),
            'clean': not case['expected'], 'blocked': bool(blockers), 'status': result.status,
            'incomplete': result.execution != 'complete' or result.status == 'error',
            'trace': result.trace, 'profile_sha256': result.profile_sha256,
            'snapshot': result.snapshot.model_dump() if result.snapshot else None,
            'findings': [f.model_dump() for f in blockers], 'limitations': result.limitations}


def metrics(rows):
    counts = {key: sum(row[key] for row in rows) for key in ('tp', 'fp', 'fn', 'incomplete')}
    return {**counts, 'cases': len(rows),
            'precision': counts['tp'] / (counts['tp'] + counts['fp']) if counts['tp'] + counts['fp'] else None,
            'recall': counts['tp'] / (counts['tp'] + counts['fn']) if counts['tp'] + counts['fn'] else None,
            'clean_blocked': sum(r['clean'] and r['blocked'] for r in rows),
            'clean_passed': sum(r['clean'] and r['status'] == 'success' and not r['incomplete'] for r in rows),
            'model_calls': sum(r.get('trace', {}).get('model_calls', 0) for r in rows),
            'elapsed_ms': sum(r.get('trace', {}).get('elapsed_ms', 0) for r in rows),
            'token_usage': [usage for r in rows for usage in r.get('trace', {}).get('token_usage', [])]}


def evaluate(corpus, split, model_factory, limits=None):
    rows = []
    for case in select_cases(corpus, split):
        with tempfile.TemporaryDirectory(prefix='review-eval-') as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-q')
            def commit(files):
                for name, contents in files.items():
                    path = root / name
                    if Path(name).is_absolute() or '..' in Path(name).parts:
                        raise ValueError('Invalid fixture path')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(contents)
                git('add', '.')
                git('-c', 'user.name=Review Eval', '-c', 'user.email=eval@invalid', 'commit', '-qm', 'fixture')
                return git('rev-parse', 'HEAD')
            base = commit({**case['before'], 'README.md': case['contract'],
                           '.ai-review/profile.json': json.dumps(case['profile'])})
            head = commit(case['after'])
            result = review(root, base, head, model=model_factory(), limits=limits or Limits(context_bytes=32_000, output_tokens=2_000))
            rows.append(summarize(case, result))
    summary = metrics(rows)
    return {'case_set_version': corpus['version'], 'split': split,
            'case_set_sha256': hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest(),
            'summary': summary, 'results': rows,
            'quality_gate_passed': bool(rows) and summary['incomplete'] == 0 and summary['fn'] == 0
                                   and summary['clean_blocked'] == 0 and (summary['precision'] or 0) >= 0.95}
