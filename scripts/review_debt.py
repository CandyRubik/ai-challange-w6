"""Check confirmed debt or print unapproved candidates; never write the registry."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reviewer.debt import suggest
from reviewer import review
from reviewer.git import Repository
from reviewer.models import Profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--base', default='origin/main')
    parser.add_argument('--suggest', action='store_true')
    args = parser.parse_args()
    try:
        repository = Repository(args.repo)
        if not args.suggest:
            result = review(args.repo, args.base, args.head, project_debt=True)
            print(result.model_dump_json(indent=2))
            return {'success':0, 'failure':1, 'error':2}[result.status]
        sha = repository.run('rev-parse', '--verify', '--end-of-options', args.head+'^{commit}').decode().strip()
        profile = Profile.model_validate_json(repository.read(sha, '.ai-review/profile.json'))
        print(suggest(repository, sha, profile).model_dump_json(indent=2))
        return 0
    except (ValueError, SyntaxError, RecursionError):
        print('Cannot produce complete static debt candidates', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
