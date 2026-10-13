"""Read committed code and project rules without executing the reviewed project."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reviewer import review
from reviewer.report import markdown
from reviewer.models import ReviewResult
from reviewer.semantic_models import Limits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--profile", type=Path, help="Explicit trusted local profile for bootstrap")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--explain-policy", action="store_true")
    parser.add_argument("--semantic", action="store_true", help="Explicitly enable bounded DeepSeek review (up to two calls)")
    parser.add_argument("--context-bytes", type=int, default=48_000)
    parser.add_argument("--output-tokens", type=int, default=4_000)
    args = parser.parse_args()
    try:
        model = None
        if args.semantic:
            from reviewer.deepseek import DeepSeekReviewModel
            model = DeepSeekReviewModel()
        result = review(args.repo, args.base, args.head, profile_path=args.profile, model=model,
                        limits=Limits(context_bytes=args.context_bytes, output_tokens=args.output_tokens))
    except ValueError:
        result = ReviewResult(execution="failed", status="error", limitations=["Invalid limits or DeepSeek configuration"])
    print(result.model_dump_json(indent=2) if args.format == "json" else markdown(result, explain_policy=args.explain_policy), end="\n")
    return {"success": 0, "failure": 1, "error": 2}[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
