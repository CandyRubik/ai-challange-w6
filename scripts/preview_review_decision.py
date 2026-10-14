"""Preview owner policy offline; output cannot authorize a CI exception."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reviewer.artifacts import preview
from reviewer.contracts import ReviewArtifact
from reviewer.decisions import OwnerProposal, effects, fingerprint, propose


def load(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError('Decision input exceeds size limit')
    return json.loads(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact',type=Path,required=True)
    parser.add_argument('--proposal',type=Path)
    parser.add_argument('--actor')
    args = parser.parse_args()
    try:
        raw = load(args.artifact)
        artifact = ReviewArtifact.model_validate(raw)
        preview(artifact.context,raw)
        output = dict(authority='offline-preview-only',finding_ids=[dict(id=fingerprint(f),rule=f.rule_id,
            path=f.evidence.path,blocking=f.blocking,message=f.message) for f in artifact.result.findings])
        if args.proposal:
            proposal = OwnerProposal.model_validate(load(args.proposal))
            decision = propose(artifact.context,artifact.result,proposal,args.actor or '')
            output.update(decision=decision.model_dump(),effects=effects(artifact.context,artifact.result,{'preview':decision}).model_dump())
        print(json.dumps(output,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,KeyError,TypeError,OSError) as error:
        print(str(error),file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
