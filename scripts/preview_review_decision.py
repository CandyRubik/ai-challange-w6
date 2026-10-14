"""Preview owner policy offline; output cannot authorize a CI exception."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reviewer.artifacts import preview
from reviewer.contracts import ReviewArtifact
from reviewer.debt import DebtRegistry
from reviewer.decisions import OwnerDecision, OwnerProposal, effects, fingerprint, propose


def load(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError('Decision input exceeds size limit')
    return json.loads(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact',type=Path,required=True)
    parser.add_argument('--proposal',type=Path)
    parser.add_argument('--actor')
    parser.add_argument('--proposed-registry',type=Path)
    parser.add_argument('--known-decisions',type=Path)
    args = parser.parse_args()
    try:
        raw = load(args.artifact)
        artifact = ReviewArtifact.model_validate(raw)
        preview(artifact.context,raw)
        output = dict(authority='offline-preview-only',finding_ids=[dict(id=fingerprint(f),rule=f.rule_id,
            path=f.evidence.path,blocking=f.blocking,message=f.message) for f in artifact.result.findings])
        if args.proposal:
            proposal = OwnerProposal.model_validate(load(args.proposal))
            registry = DebtRegistry.model_validate(load(args.proposed_registry)) if args.proposed_registry else None
            history = load(args.known_decisions) if args.known_decisions else {}
            if not isinstance(history,dict) or len(history)>100:
                raise ValueError('Offline decision history must be a bounded object')
            known = {key:OwnerDecision.model_validate(value) for key,value in history.items()}
            decision = propose(artifact.context,artifact.result,proposal,args.actor or '',proposed_registry=registry,known_decisions=known)
            output.update(decision=decision.model_dump(),effects=effects(artifact.context,artifact.result,{**known,'preview':decision}).model_dump())
        print(json.dumps(output,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,KeyError,TypeError,OSError):
        print('Decision preview input or policy is invalid; check artifact, owner, scope and UTC expiry',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
