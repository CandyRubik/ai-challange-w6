"""Read a provenance-verified Actions review; no decisions or writes are made."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reviewer.actions import Actions
from reviewer.github import GitHub,GitHubError
from reviewer.git import Repository
from reviewer.owner_journal import read_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',required=True)
    parser.add_argument('--run',type=int,required=True)
    parser.add_argument('--attempt',type=int,default=1)
    parser.add_argument('--pr',type=int,required=True)
    args = parser.parse_args()
    try:
        api = GitHub(args.repository,os.environ.get('GITHUB_TOKEN',''))
        artifact = read_review(Actions(api),Repository(Path.cwd()),args.run,args.attempt,args.pr)
        print(json.dumps(dict(authority='verified-source-read-only',source_run=args.run,attempt=args.attempt,
            status=artifact.result.status,execution=artifact.result.execution,result_sha256=artifact.result_sha256,
            base=artifact.context.base_sha,head=artifact.context.head_sha),indent=2))
        return 0
    except (ValueError,KeyError,TypeError,OSError,GitHubError):
        print('Original review source could not be verified; credentials and input values omitted',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
