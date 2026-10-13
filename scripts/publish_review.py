"""Preview a validated artifact; --publish explicitly writes summary and commit status."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reviewer.github import GitHub, GitHubError
from reviewer.publishing import RunContext, preview, publish


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    try:
        for path in (args.context, args.artifact):
            if path.stat().st_size > 2_000_000:
                raise ValueError('Artifact exceeds size limit')
        context = RunContext.model_validate_json(args.context.read_bytes())
        raw = json.loads(args.artifact.read_bytes())
        rendered = preview(context, raw)
        if args.publish:
            api = GitHub(os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_TOKEN'])
            state = publish(api, context, raw)
        else:
            print(rendered['body'])
            state = rendered['state']
        return {'success': 0, 'failure': 1, 'error': 2}[state]
    except (ValueError, KeyError, TypeError, OSError, GitHubError) as error:
        print(f'GitHub review failed ({type(error).__name__}); result was not approved', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
