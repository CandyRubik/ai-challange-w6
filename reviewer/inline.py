"""Inline presentation uses verified GitHub changed lines, never model-chosen API targets."""
import hashlib
import json
import re
from difflib import SequenceMatcher

from .report import finding_details, safe_comment

PREFIX = '<!-- ai-review:inline:v1:'
LIMIT = 10


def patch_lines(patch):
    changed, remaining, positions = {'LEFT':{}, 'RIGHT':{}}, None, None
    for row in patch.splitlines():
        if row.startswith('@@'):
            if remaining and any(remaining):
                raise ValueError('Truncated diff hunk')
            match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*', row)
            if not match:
                raise ValueError('Invalid diff hunk')
            old, old_count, new, new_count = match.groups()
            positions, remaining = [int(old),int(new)], [int(old_count or 1),int(new_count or 1)]
        elif row.startswith('\\ No newline'):
            continue
        elif remaining is not None:
            if not row or row[0] not in ' +-':
                raise ValueError('Invalid diff line')
            for index, side, prefix in ((0,'LEFT','-'),(1,'RIGHT','+')):
                if row[0] in (' ', prefix):
                    if remaining[index] <= 0 or positions[index] <= 0:
                        raise ValueError('Diff count mismatch')
                    if row[0] == prefix:
                        changed[side][positions[index]] = row[1:]
                    remaining[index] -= 1
                    positions[index] += 1
    if remaining is None or any(remaining):
        raise ValueError('Missing or truncated patch')
    return changed


def blob(repository, sha, path):
    text = repository.read(sha, path)
    raw = text.encode()
    oid = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    return text, oid


def anchor(context, finding, files, repository):
    ref = finding.evidence
    if ref.kind != 'text' or ref.line < 1 or finding.source and finding.source.sha != context.base_sha:
        return None
    if ref.sha == context.head_sha and context.change_status.get(ref.path) not in (None,'D'):
        path, side = ref.path, 'RIGHT'
    elif ref.sha == context.merge_base_sha:
        paths = [path for path, before in context.before_paths.items() if before == ref.path and context.change_status[path] != 'A']
        if len(paths) != 1:
            return None
        path, side = paths[0], 'LEFT'
    else:
        return None
    file = next((file for file in files if file.get('filename') == path), None)
    if not file or not isinstance(file.get('patch'), str):
        return None
    if side == 'LEFT' and ref.path != path and file.get('previous_filename') != ref.path:
        return None
    deleted, added = context.change_status[path] == 'D', context.change_status[path] == 'A'
    if (file.get('status') == 'removed') != deleted:
        return None
    try:
        after, head_oid = blob(repository,context.head_sha,path) if not deleted else ('',None)
        before, before_oid = blob(repository,context.merge_base_sha,context.before_paths[path]) if not added else ('',None)
        oid = before_oid if deleted else head_oid
        if file.get('sha') != oid:
            return None
        current = after if side == 'RIGHT' else before
        actual = set()
        for op in SequenceMatcher(None,before.splitlines(),after.splitlines()).get_opcodes():
            if op[0] != 'equal':
                first,last = (op[3],op[4]) if side == 'RIGHT' else (op[1],op[2])
                actual.update(range(first+1,last+1))
        source = current.splitlines(keepends=True)
        if ref.line > len(source):
            return None
        excerpt = ''.join(source[ref.line-1:ref.line-1+len(ref.quote.splitlines())])
        beginning = excerpt.find(ref.quote)
        if beginning < 0 or '\n' in excerpt[:beginning]:
            return None
        end = ref.line + ref.quote.count('\n') - int(ref.quote.endswith('\n'))
        for line, text in patch_lines(file['patch'])[side].items():
            if line in actual and ref.line <= line <= end and text.strip() and source[line-1].rstrip('\r\n') == text:
                return dict(path=path, side=side, line=line)
    except (ValueError, IndexError):
        return None
    return None


def plan_inline(context, result, files, repository):
    groups, fallback = {}, []
    for finding in sorted(result.findings, key=lambda f:(not f.blocking, {'critical':0,'major':1,'minor':2}[f.severity],f.evidence.path,f.evidence.line,f.rule_id)):
        target = anchor(context, finding, files, repository)
        if target is None:
            fallback.append(f'{finding.evidence.path}: no verified changed-line anchor')
            continue
        key = hashlib.sha256(json.dumps([context.head_sha,target],sort_keys=True).encode()).hexdigest()
        groups.setdefault(key, dict(key=key, anchor=target, findings=[]))['findings'].append(finding)
    selected = list(groups.values())[:LIMIT]
    fallback += ['Inline comment limit reached'] * max(0,len(groups)-LIMIT)
    for group in selected:
        group['marker'] = f'{PREFIX}{context.head_sha}:{group["key"]} -->'
        group['body'] = safe_comment(group['marker']+'\n'+'\n\n'.join(finding_details(group['findings']))+f'\n\n[Workflow run]({context.run_url})')
    return selected, fallback

