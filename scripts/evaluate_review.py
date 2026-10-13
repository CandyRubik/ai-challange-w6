"""Explicit paid review calibration against versioned synthetic/history-derived cases."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reviewer.evaluation import evaluate, select_cases
from reviewer.semantic_models import Limits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[1] / '.ai-review/eval/cases.json')
    parser.add_argument('--split', choices=['development', 'holdout', 'all'], default='development')
    parser.add_argument('--run', action='store_true', help='Explicitly enable paid calls; requires DEEPSEEK_API_KEY')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    corpus = json.loads(args.cases.read_text())
    if not args.run:
        print(json.dumps({'version': corpus['version'], 'cases': [c['id'] for c in select_cases(corpus, args.split)],
                          'max_calls': len(select_cases(corpus, args.split)) * 2}, indent=2))
        return 0
    from reviewer.deepseek import DeepSeekReviewModel
    report = evaluate(corpus, args.split, DeepSeekReviewModel, Limits(context_bytes=32_000, output_tokens=2_000))
    output = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(output + '\n')
    else:
        print(output)
    return 0 if report['quality_gate_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
