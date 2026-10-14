"""Trusted owner dispatch stages; never construct a model or execute PR code."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reviewer.ci import check_context,fetch_head,mark_unfinished,prepare_snapshot
from reviewer.decision_records import DecisionEnvelope
from reviewer.github import GitHub,GitHubError,UncertainSummaryPost
from reviewer.git import Repository
from reviewer.owner_authorize import authorize,number
from reviewer.owner_publishing import publish_owned
from reviewer.owner_report import OWNER_MARKER,journal
from reviewer.owner_runtime import assert_owners,capture_owners
from reviewer.publishing import StaleReview,assert_current,make_artifact,upsert_comment
from reviewer.reuse import reuse_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['authorize','apply','finalize'])
    args = parser.parse_args()
    env,api,pr = dict(os.environ),None,None
    directory = Path('.review');directory.mkdir(exist_ok=True)
    try:
        event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_bytes())
        inputs = event['inputs'];pr=number(inputs.get('pr'))
        api = GitHub(env['GITHUB_REPOSITORY'],env['GITHUB_TOKEN'])
        if args.stage=='finalize':
            mark_unfinished(api,pr,env,'Owner decision pipeline did not complete; inspect the run and retry')
            return 0
        if args.stage=='authorize':
            envelope = authorize(api,Path.cwd(),env,inputs,fetcher=fetch_head)
            (directory/'decision.json').write_text(envelope.model_dump_json(indent=2))
            with open(env['GITHUB_OUTPUT'],'a') as output: output.write(f'pr={pr}\n')
            return 0
        envelope = DecisionEnvelope.model_validate_json((directory/'decision.json').read_bytes())
        context = prepare_snapshot(api,Path.cwd(),pr,env,fetcher=fetch_head,start_status=False,owner_policy=False)
        repository = Repository(Path.cwd())
        context = capture_owners(api,repository,context,env)
        check_context(Path.cwd(),context,env)
        if context.owner_records.get(envelope.id)!=envelope:
            raise ValueError('Current decision differs from the authenticated journal')
        assert_current(api,context);assert_owners(api,repository,context)
        if not context.analysis_source:
            def guard(): assert_current(api,context);assert_owners(api,repository,context)
            bodies = {key:record.decision for key,record in context.owner_records.items()}
            upsert_comment(api,pr,journal(context,bodies),marker=OWNER_MARKER,before_write=guard)
            raise ValueError('Decision recorded; no current complete analysis to apply; run AI Review afresh')
        raw = make_artifact(context,reuse_result(api,repository,context))
        (directory/'result.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
        state = publish_owned(api,context,raw,repository,expected=context.owner_sha256)
        print(f'Owner decision applied: {state}; model calls: 0')
        return 2 if state=='error' else 0
    except (ValueError,KeyError,TypeError,OSError,GitHubError) as error:
        print('Owner decision failed validation, freshness or publication; inspect inputs and rerun',file=sys.stderr)
        if api is not None and pr is not None:
            try:
                mark_unfinished(api,pr,env,'Owner decision could not be applied; rerun or request fresh analysis',summary=not isinstance(error,UncertainSummaryPost))
            except (ValueError,KeyError,TypeError,GitHubError): pass
        return 2


if __name__=='__main__': raise SystemExit(main())
