"""Entrypoints for trusted, separated Actions jobs."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reviewer.ci import (analyze_snapshot, check_code, check_context, mark_unfinished,
                         prepare_snapshot, select_prs, fetch_head)
from reviewer.git import Repository
from reviewer.github import GitHub, GitHubError, UncertainSummaryPost
from reviewer.publishing import RunContext, StaleReview, publish, assert_current
from reviewer.owner_runtime import enabled
from reviewer.owner_publishing import publish_owned


def load(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError('Review artifact exceeds size limit')
    return json.loads(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['select', 'prepare', 'analyze', 'publish'])
    parser.add_argument('--pr', type=int)
    parser.add_argument('--directory', type=Path, default=Path('.review'))
    args = parser.parse_args()
    root, env, api = Path.cwd(), dict(os.environ), None
    args.directory.mkdir(parents=True, exist_ok=True)
    context_path, result_path = args.directory/'context.json', args.directory/'result.json'
    try:
        check_code(root, env['GITHUB_WORKFLOW_SHA'])
        api = GitHub(env['GITHUB_REPOSITORY'], env['GITHUB_TOKEN'])
        if args.stage == 'select':
            numbers = select_prs(api, load(Path(env['GITHUB_EVENT_PATH'])), env)
            with open(env['GITHUB_OUTPUT'], 'a') as output:
                output.write('prs=' + json.dumps(numbers) + '\n')
        elif args.stage == 'prepare':
            context = prepare_snapshot(api, root, args.pr, env)
            context_path.write_text(context.model_dump_json(indent=2))
        else:
            context = RunContext.model_validate(load(context_path))
            if context.pr_number != args.pr:
                raise ValueError('Artifact belongs to another PR')
            check_context(root, context, env)
            if args.stage == 'analyze':
                artifact = analyze_snapshot(api, root, context, env)
                result_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
            else:
                assert_current(api,context)
                if fetch_head(root,args.pr,context.repository,env['GITHUB_TOKEN']) != context.head_sha:
                    raise StaleReview('PR changed during publisher fetch')
                repository = Repository(root)
                state = (publish_owned(api,context,load(result_path),repository,expected=context.owner_sha256)
                         if enabled(repository,context) else publish(api,context,load(result_path),repository=repository))
                print(f'AI review: {state} @ {context.head_sha}')
                return {'success':0, 'failure':1, 'error':2}[state]
        return 0
    except (ValueError, KeyError, TypeError, OSError, GitHubError) as error:
        reason = str(error) if type(error) is ValueError or isinstance(error, (StaleReview, GitHubError)) else f'{args.stage}: snapshot, policy or authorization validation failed ({type(error).__name__})'
        if args.stage == 'publish' and isinstance(error, FileNotFoundError):
            reason = 'Current-attempt review artifact missing; re-run all jobs or start a new dispatch'
        previous_error = args.directory/'error.json'
        if args.stage == 'publish' and previous_error.exists():
            try:
                reason = str(load(previous_error).get('reason', reason))
            except (ValueError, TypeError, OSError):
                pass
        (args.directory/'error.json').write_text(json.dumps(dict(stage=args.stage, reason=reason)))
        print(reason, file=sys.stderr)
        if args.stage == 'publish' and api is not None and args.pr is not None:
            try:
                mark_unfinished(api, args.pr, env, reason, summary=not isinstance(error,UncertainSummaryPost))
            except (ValueError, KeyError, TypeError, GitHubError):
                print('GitHub error status could not be written; rerun required', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
