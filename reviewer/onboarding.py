"""Read committed project facts and compile an offline policy template."""
from collections import Counter
import ast
import hashlib
import json
from pathlib import PurePosixPath
import re
import time
import tomllib

from .generation import prepare_messages
from .git import Repository
from .models import Profile
from .retrieval import document,readable
from .semantic import SYSTEM
from .semantic_models import DraftReview
from .verification import SYSTEM as VERIFY_SYSTEM,Verification


def facts(root,revision,*,file_limit=500,byte_limit=2_000_000):
    repo=Repository(root)
    sha=repo.run('rev-parse','--verify','--end-of-options',revision+'^{commit}').decode().strip()
    paths=repo.paths(sha);modules=[];dependencies=[];omissions=[];used=0;count=0;started=time.monotonic()
    for path in paths:
        parts=PurePosixPath(path).parts
        if any(p.startswith('.env') or p in ('credentials.json','private.env') for p in parts): continue
        manifest=PurePosixPath(path).name
        if not (path.endswith('.py') or document(path) or manifest=='pyproject.toml' or manifest.startswith('requirements') and manifest.endswith('.txt')): continue
        if count>=file_limit or time.monotonic()-started>30:
            omissions.append('Fact file/time budget reached');break
        count+=1
        try:
            size=int(repo.run('cat-file','-s',sha+':'+path))
            if used+size>byte_limit:
                omissions.append('Fact byte budget reached');break
            used+=size
            content=repo.read(sha,path)
            if path.endswith('.py'):
                tree=ast.parse(content)
                imports={n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.module}
                imports|={a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names}
                modules.append(dict(path=path,imports=sorted(imports),symbols=[dict(name=n.name,line=n.lineno) for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef))]))
            entries=[]
            if manifest=='pyproject.toml':
                project=tomllib.loads(content).get('project',{})
                entries=project.get('dependencies',[])
            elif manifest.startswith('requirements') and manifest.endswith('.txt'): entries=content.splitlines()
            if not isinstance(entries,list) or any(not isinstance(entry,str) for entry in entries):
                raise ValueError('Unsupported dependency declaration')
            for entry in entries:
                match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?(?:\s*[<>=!~].*)?',entry.split('#',1)[0].strip())
                if match: dependencies.append(dict(name=match[1].lower(),path=path))
        except (ValueError,SyntaxError,RecursionError,TypeError,AttributeError):
            omissions.append(path+': source unavailable or unsupported')
    return dict(authority='facts-only-not-project-rules',revision=sha,files_considered=count,source_bytes_budgeted=used,omissions=omissions,
        extensions=dict(sorted(Counter(PurePosixPath(p).suffix for p in paths).items())),dependencies=dependencies,modules=modules,
        documentation=[p for p in paths if document(p)],workflows=[p for p in paths if p.startswith('.github/workflows/')],
        unsupported_review_paths=[p for p in paths if PurePosixPath(p).suffix in
            {'.py','.js','.ts','.tsx','.jsx','.java','.kt','.go','.rs','.swift','.rb','.cs','.c','.cpp','.h'} and not readable(p)],
        static_checks=['python-import-boundary'])


def policy_template(root,revision,profile_path):
    if any(p.startswith('.env') or p in ('credentials.json','private.env') for p in profile_path.resolve().parts):
        raise ValueError('A profile must not be read from a credential path')
    if profile_path.stat().st_size>512_000: raise ValueError('Draft profile exceeds size limit')
    profile=Profile.model_validate_json(profile_path.read_bytes())
    if any(r.id=='BUGS' for r in profile.rules): raise ValueError('BUGS is reserved for the runtime general check')
    repo=Repository(root)
    sha=repo.run('rev-parse','--verify','--end-of-options',revision+'^{commit}').decode().strip()
    unresolved=[]
    for rule in profile.rules:
        try:
            if not document(rule.source.path) or rule.source.quote not in repo.read(sha,rule.source.path):
                unresolved.append(rule.id)
        except ValueError: unresolved.append(rule.id)
    payload=dict(policy=[r.model_dump() for r in profile.rules],criteria=[],fragments=[],changed_lines={},causal_changes={})
    messages=[dict(role='system',content=SYSTEM),dict(role='user',content=json.dumps(payload,ensure_ascii=False))]
    generation,_,_=prepare_messages(messages,DraftReview.model_json_schema())
    verifier,_,_=prepare_messages([dict(role='system',content=VERIFY_SYSTEM),dict(role='user',content=json.dumps({**payload,'candidates':[]},ensure_ascii=False))],Verification.model_json_schema())
    size=len(json.dumps(generation,ensure_ascii=False).encode())
    return dict(authority='draft-preview-only',profile_origin='explicit_local',complete_pr_payload=False,revision=sha,
        profile_sha256=hashlib.sha256(json.dumps(profile.model_dump(),sort_keys=True).encode()).hexdigest(),
        profile=profile.model_dump(),unresolved_normative_rules=unresolved,generation_template_bytes=size,
        runtime_context_limit=96_000,runtime_slots=['snapshot','changed criteria','code/docs fragments','causal changes','candidate findings'],
        generation_messages=generation,verification_messages=verifier)
