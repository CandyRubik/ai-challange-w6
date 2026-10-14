"""Conservative import candidates; inspect Python syntax without importing code."""
import ast
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass
class FunctionContext:
    name: str
    priority: int
    spans: list[tuple[int, str]]


def imported_functions(path, text, spans, paths, *, direct_only=False):
    if not path.endswith('.py'):
        return {}
    try:
        tree = ast.parse(text)
    except (SyntaxError, RecursionError):
        return {}
    edits = [(start,max(stop,start+1)) for start,stop in spans]
    ranges = list(edits)
    for node in ([] if direct_only else ast.walk(tree)):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            first = min([node.lineno,*[d.lineno for d in node.decorator_list]])-1
            if any(start < node.end_lineno and stop > first for start,stop in ranges):
                ranges.append((first,node.end_lineno))
    shadowed = {n.arg for n in ast.walk(tree) if isinstance(n,ast.arg)} | {
        n.id for n in ast.walk(tree) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)} | {
        n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
    all_called = {n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)} - shadowed
    called = {n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
              and any(start < n.end_lineno and stop > n.lineno-1 for start,stop in ranges)} - shadowed
    package, wanted = list(PurePosixPath(path).parts[:-1]), {}
    for node in tree.body:
        if not isinstance(node,ast.ImportFrom) or not node.module or node.level > len(package):
            continue
        prefix = package[:len(package)-node.level+1] if node.level else []
        module = '/'.join([*prefix,*node.module.split('.')])
        candidates = [p for p in (module+'.py',module+'/__init__.py') if p in paths]
        used = all_called if any(start < node.end_lineno and stop > node.lineno-1 for start,stop in edits) else called
        symbols = {a.name for a in node.names if (a.asname or a.name) in used and a.name != '*'}
        if len(candidates) == 1 and symbols:
            wanted.setdefault(candidates[0],set()).update(symbols)
    return wanted


def function_contexts(text, names):
    try:
        tree = ast.parse(text)
    except (SyntaxError, RecursionError):
        return []
    lines, contexts, file_bytes = text.splitlines(keepends=True), [], len(text.encode())
    imports = [n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom))]
    for node in tree.body:
        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) or node.name not in names:
            continue
        first = min([node.lineno,*[d.lineno for d in node.decorator_list]])
        body = ''.join(lines[first-1:node.end_lineno])
        if file_bytes <= 1500:
            contexts.append(FunctionContext(name=node.name,priority=len(body.encode()),spans=[(1,text)]))
            continue
        if first <= 30:
            prefix = ''.join(lines[:node.end_lineno])
            if len(prefix.encode()) <= 1500:
                contexts.append(FunctionContext(name=node.name,priority=len(body.encode()),spans=[(1,prefix)]))
                continue
        used = {n.id for n in ast.walk(node) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load)}
        bound = {n.arg for n in ast.walk(node) if isinstance(n,ast.arg)} | {
            n.id for n in ast.walk(node) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)}
        needed = [n for n in imports if any((a.asname or a.name.split('.')[0]) in used-bound for a in n.names)]
        spans = []
        if needed:
            start, stop = min(n.lineno for n in needed), max(n.end_lineno for n in needed)
            spans.append((start,''.join(lines[start-1:stop])))
        spans.append((first,body))
        contexts.append(FunctionContext(name=node.name,priority=len(body.encode()),spans=spans))
    return contexts
