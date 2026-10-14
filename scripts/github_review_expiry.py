"""Bounded zero-model expiry workflow entrypoints."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reviewer.ci import check_code,fetch_head
from reviewer.github import GitHub,GitHubError
from reviewer.owner_expiry import expire,select


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['select','expire']);parser.add_argument('--pr',type=int)
    args=parser.parse_args();env=dict(os.environ)
    if args.stage=='expire' and args.pr is None: parser.error('--pr is required for expire')
    try:
        check_code(Path.cwd(),env['GITHUB_WORKFLOW_SHA'])
        api=GitHub(env['GITHUB_REPOSITORY'],env['GITHUB_TOKEN'])
        if args.stage=='select':
            rows=select(api,json.loads(Path(env['GITHUB_EVENT_PATH']).read_bytes()),env)
            with open(env['GITHUB_OUTPUT'],'a') as output: output.write('prs='+json.dumps(rows)+'\n')
        else:
            print(expire(api,Path.cwd(),args.pr,env,fetcher=fetch_head)+'; model calls: 0')
        return 0
    except (ValueError,KeyError,TypeError,OSError,GitHubError):
        print('Expiry proof, snapshot or GitHub operation failed; inspect run and retry',file=sys.stderr);return 2


if __name__=='__main__': raise SystemExit(main())
