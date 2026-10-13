"""Explicit paid review calibration against versioned synthetic/history-derived cases."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reviewer.evaluation import Annotations, adjudicate, case_set_sha256, evaluate, evaluation_plan
from reviewer.semantic_models import Limits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[1] / '.ai-review/eval/cases.json')
    parser.add_argument('--split', choices=['development', 'holdout', 'all'], default='development')
    parser.add_argument('--run', action='store_true', help='Explicitly enable paid calls; requires DEEPSEEK_API_KEY')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--resume', type=Path, help='Retry incomplete cases of the same case set, split and bounds')
    parser.add_argument('--annotations', type=Path, help='Explicit exact-result duplicate adjudications; raw counts remain visible')
    args = parser.parse_args()
    corpus = json.loads(args.cases.read_text())
    bounds = Limits(context_bytes=32_000, output_tokens=2_000)
    previous = json.loads(args.resume.read_text()) if args.resume else None
    _, cases, cached = evaluation_plan(corpus, args.split, bounds, previous)
    pending = [c for c in cases if c['id'] not in cached or cached[c['id']]['incomplete']]
    if not args.run:
        print(json.dumps({'version': corpus['version'], 'cases': [c['id'] for c in pending],
                          'max_calls': len(pending) * 4}, indent=2))
        return 0
    annotations = json.loads(args.annotations.read_text()) if args.annotations else None
    if annotations is not None:
        labels = Annotations.model_validate(annotations)
        if labels.case_set_sha256 != case_set_sha256(corpus):
            raise ValueError('Annotation case-set mismatch')
    from reviewer.deepseek import DeepSeekReviewModel
    report = evaluate(corpus, args.split, DeepSeekReviewModel, bounds, previous=previous)
    if annotations is not None:
        report = adjudicate(report, annotations, corpus=corpus)
    output = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(output + '\n')
    else:
        print(output)
    return 0 if report['quality_gate_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
