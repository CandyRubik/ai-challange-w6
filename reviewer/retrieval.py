import ast
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import PurePosixPath
import re
import time

from .models import ReviewResult
from .semantic_models import Fragment


@dataclass
class Retrieved:
    fragments: list[Fragment]
    required_count: int
    omissions: list[str]
    edits: dict[str, list[tuple[int, int]]]


class ContextUnavailable(ValueError):
    pass


def allowed(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts or ".." in parts or any(part.startswith(".env") or part in ("credentials.json", "private.env") for part in parts):
        return False
    return parts[0] in ("app", "reviewer", "tests", "scripts", "static", ".github", ".ai-review", "docs") or (
        path in ("README.md", "CONTEXT.md", "Dockerfile", "compose.yaml", "pyproject.toml")
        or path.startswith("requirements") and path.endswith(".txt"))


def document(path: str) -> bool:
    return allowed(path) and (path in ("README.md", "CONTEXT.md") or path.startswith("docs/") and path.endswith(".md"))


def code(path: str) -> bool:
    return allowed(path) and (PurePosixPath(path).suffix in (
        ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".html", ".css", ".sh", ".txt") or path == "Dockerfile")


def readable(path: str) -> bool:
    return code(path) or document(path)


def fragment(path: str, sha: str, text: str, role: str, line: int = 1, kind: str = "text") -> Fragment:
    identity = "\0".join((path, sha, role, str(line), text))
    return Fragment(id=hashlib.sha256(identity.encode()).hexdigest()[:20], path=path,
                    sha=sha, text=text, role=role, line=line, kind=kind)


def windows(path, sha, text, role, spans=None):
    lines = text.splitlines(keepends=True)
    if spans is not None and path.endswith(".py"):
        try:
            functions = [node for node in ast.walk(ast.parse(text)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            spans = list(spans)
            for node in functions:
                beginning = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
                if any(start < node.end_lineno and stop > beginning for start, stop in spans):
                    spans.append((beginning, node.end_lineno))
        except (SyntaxError, RecursionError):
            pass
    return [fragment(path, sha, "".join(lines[offset:offset + 60]), role, offset + 1)
        for offset in range(0, len(lines), 60) if spans is None or offset == 0
        or any(start < offset + 60 and max(stop, start + 1) > offset for start, stop in spans)]


def retrieve(repository, result: ReviewResult, changes, limits) -> Retrieved:
    snapshot = result.snapshot
    sources = {}
    for rule in result.policy:
        if not document(rule.source.path):
            raise ContextUnavailable("Normative source is outside the documentation allowlist")
        key = (rule.source.path, rule.source.quote)
        if key not in sources:
            text = repository.read(snapshot.base_sha, rule.source.path)
            line = text[:text.index(rule.source.quote)].count("\n") + 1
            sources[key] = fragment(rule.source.path, snapshot.base_sha, rule.source.quote, "normative", line)
    fragments, changed_lines = list(sources.values()), {}
    for change in changes:
        if not readable(change.path) or change.status != "A" and not readable(change.before_path):
            raise ContextUnavailable("Before/head source is outside the review corpus; provenance cannot be assessed")
        after = repository.read(snapshot.head_sha, change.path) if change.status != "D" else ""
        before = repository.read(snapshot.merge_base_sha, change.before_path) if change.status != "A" else ""
        edits = [op for op in SequenceMatcher(None, before.splitlines(), after.splitlines()).get_opcodes() if op[0] != "equal"]
        changed_lines["head:" + change.path] = [(op[3] + 1, op[4]) for op in edits if op[4] > op[3]]
        changed_lines["before:" + change.before_path] = [(op[1] + 1, op[2]) for op in edits if op[2] > op[1]]
        for role, path, sha, content, mode in (
            ("head", change.path, snapshot.head_sha, after, change.after_mode),
            ("before", change.before_path, snapshot.merge_base_sha, before, change.before_mode),
        ):
            if mode != "000000":
                metadata = json.dumps({"mode": mode, "status": change.status, "previous_path": change.before_path,
                    "bytes": len(content.encode()), "sha256": hashlib.sha256(content.encode()).hexdigest()})
                fragments.append(fragment(path, sha, metadata, role, 0, "file_metadata"))
        if change.status != "D":
            fragments += windows(change.path, snapshot.head_sha, after, "head", [(op[3], op[4]) for op in edits])
        if change.status != "A":
            fragments += windows(change.before_path, snapshot.merge_base_sha, before, "before", [(op[1], op[2]) for op in edits])
    required_count = len(fragments)
    query = set(re.findall(r"\w{3,}", (" ".join(f.text + " " + f.path for f in fragments
                if f.role != "normative" and f.kind == "text") + " ".join(c.path for c in changes)).casefold()))
    candidates, omissions, total_bytes, scanned = [], [], 0, 0
    deadline = time.monotonic() + 30
    changed = {change.path for change in changes}
    for sha, role in ((snapshot.head_sha, "head"), (snapshot.base_sha, "normative")):
        for path in repository.paths(sha):
            if not (code(path) if role == "head" else document(path)) or role == "head" and path in changed:
                continue
            scanned += 1
            if scanned > limits.corpus_files or time.monotonic() > deadline:
                raise ContextUnavailable("Retrieval corpus/time budget exceeded")
            try:
                content = repository.read(sha, path)
            except ValueError:
                omissions.append(path)
                continue
            total_bytes += len(content.encode())
            if total_bytes > limits.corpus_bytes:
                raise ContextUnavailable("Retrieval corpus byte budget exceeded")
            for candidate in windows(path, sha, content, role):
                tokens = set(re.findall(r"\w{3,}", (path + " " + candidate.text).casefold()))
                score = len(tokens & query)
                if score:
                    candidates.append((score, candidate))
    candidates.sort(key=lambda item: (-item[0], item[1].path, item[1].line))
    fragments += [candidate for _, candidate in candidates[:8]]
    return Retrieved(fragments, required_count, omissions, changed_lines)
