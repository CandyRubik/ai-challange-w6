"""Offline facts/policy-template helper; write drafts only."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reviewer.onboarding import facts,policy_template


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['facts','prompt'])
    parser.add_argument('--revision',default='HEAD');parser.add_argument('--profile',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();root=Path.cwd().resolve()
    try:
        target=(args.output or root/'.ai-review'/'drafts'/(args.stage+'.json')).resolve()
        target.relative_to(root/'.ai-review'/'drafts')
        if args.stage=='prompt' and args.profile is None: raise ValueError('Prompt preview needs an explicit draft profile')
        result=facts(root,args.revision) if args.stage=='facts' else policy_template(root,args.revision,args.profile)
        target.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent,mode='w',encoding='utf-8',delete=False) as stream:
            stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n');temporary=Path(stream.name)
        try: temporary.replace(target)
        finally: temporary.unlink(missing_ok=True)
        print(str(target));return 0
    except (ValueError,KeyError,TypeError,OSError):
        print('Onboarding input invalid; use a valid Git revision/profile and an output under .ai-review/drafts',file=sys.stderr);return 2


if __name__=='__main__': raise SystemExit(main())
