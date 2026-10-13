import ast
from fnmatch import fnmatchcase
import hashlib
from pathlib import Path, PurePosixPath

from .git import Repository
from .models import Criterion, Evidence, Finding, Profile, ReviewResult
from .semantic_models import JsonModel, Limits


def imports(text: str, path: str, forbidden: list[str]) -> tuple[dict[str, int], set[str]]:
    tree, found, possible = ast.parse(text), {}, set()
    dynamic_names, dynamic_modules = {"__import__"}, {"importlib", "builtins"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dynamic_modules.update(alias.asname or alias.name for alias in node.names
                                   if alias.name in ("importlib", "builtins"))
        elif isinstance(node, ast.ImportFrom) and not node.level:
            dynamic_names.update(alias.asname or alias.name for alias in node.names
                if (node.module, alias.name) in (("importlib", "import_module"), ("builtins", "__import__")))
            if node.module == "importlib" and any(alias.name == "*" for alias in node.names):
                dynamic_names.add("import_module")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name) and node.func.id in dynamic_names
            or isinstance(node.func, ast.Attribute) and node.func.attr in ("import_module", "__import__")
            and isinstance(node.func.value, ast.Name) and node.func.value.id in dynamic_modules
        ):
            raise ValueError("Dynamic imports require semantic analysis")
        if isinstance(node, ast.Import):
            found.update((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            package = list(PurePosixPath(path).parts[:-1])
            if node.level > len(package):
                raise ValueError("Relative import goes beyond the package root")
            prefix = package[:len(package) - node.level + 1] if node.level else []
            module = ".".join([*prefix, *([node.module] if node.module else [])])
            if not matches(module, forbidden) and any(matches(module + "." + alias.name, forbidden) for alias in node.names):
                raise ValueError("Import may name an attribute rather than a forbidden module; semantic analysis required")
            found[module] = node.lineno
            possible.update(module + "." + alias.name for alias in node.names)
    return found, possible


def matches(name: str, forbidden: list[str]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)


def boundaries(text: str, path: str, forbidden: list[str]) -> tuple[dict[str, int], set[str]]:
    modules, possible = imports(text, path, forbidden)
    return {name: line for name, line in modules.items() if matches(name, forbidden)}, possible


def review(root: Path, base: str, head: str, *, profile_path: Path | None = None,
           model: JsonModel | None = None, limits: Limits | None = None, project_debt: bool = False) -> ReviewResult:
    repository = Repository(root)
    metadata = {"profile_origin": "explicit_local" if profile_path else "base"}
    sources, findings, criteria, not_applicable = {}, [], [], []
    try:
        snapshot, changes = repository.snapshot(base, head)
        metadata["snapshot"] = snapshot
        raw = profile_path.read_bytes().decode("utf-8") if profile_path else repository.read(snapshot.base_sha, ".ai-review/profile.json")
        metadata["profile_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
        profile = Profile.model_validate_json(raw)
        metadata["policy"] = profile.rules
        for rule in profile.rules:
            text = repository.read(snapshot.base_sha, rule.source.path)
            if rule.source.quote not in text:
                raise ValueError(f"Rule {rule.id}: source quote is absent from base")
            index = text.index(rule.source.quote)
            sources[rule.id] = Evidence(**rule.source.model_dump(), sha=snapshot.base_sha,
                                       line=text[:index].count("\n") + 1)
    except (ValueError, OSError) as error:
        return ReviewResult(**metadata, execution="failed", status="error", limitations=[str(error)])
    for change in changes:
        applicable = [rule for rule in profile.rules if any(fnmatchcase(change.path, pattern) for pattern in rule.scope)]
        if not applicable:
            not_applicable.append(change.path)
        for rule in applicable:
            if rule.check == "semantic":
                criteria.append(Criterion(path=change.path, rule_id=rule.id, verdict="inconclusive",
                                          reason="Semantic analysis is not available in this audit"))
                continue
            if change.status == "D":
                criteria.append(Criterion(path=change.path, rule_id=rule.id, verdict="pass", reason="File deleted"))
                continue
            try:
                if not change.path.endswith(".py"):
                    raise ValueError("Import-boundary check supports Python only")
                text = repository.read(snapshot.head_sha, change.path)
                current, _ = boundaries(text, change.path, rule.forbidden_imports)
                was_in_scope = any(fnmatchcase(change.before_path, pattern) for pattern in rule.scope)
                previous, possible = boundaries(repository.read(snapshot.merge_base_sha, change.before_path), change.before_path,
                    rule.forbidden_imports) if change.status != "A" and was_in_scope else ({}, set())
                violations = {name: line for name, line in current.items() if not any(
                    old == name or old.startswith(name + ".") for old in previous)}
                if any(fnmatchcase(name, candidate) for name in violations for candidate in possible):
                    raise ValueError("Prior import may already refer to this module; semantic analysis required")
            except (ValueError, SyntaxError, RecursionError) as error:
                criteria.append(Criterion(path=change.path, rule_id=rule.id, verdict="inconclusive", reason=str(error)))
                continue
            for name, line in violations.items():
                findings.append(Finding(rule_id=rule.id, blocking=rule.level == "must",
                    message=f"Forbidden dependency {name}: {rule.requirement}", source=sources[rule.id],
                    evidence=Evidence(path=change.path, sha=snapshot.head_sha, line=line, quote=text.splitlines()[line - 1])))
            criteria.append(Criterion(path=change.path, rule_id=rule.id,
                verdict="violation" if violations else "pass", reason="Static imports checked"))
    levels = {rule.id: rule.level for rule in profile.rules}
    incomplete = [c for c in criteria if c.verdict == "inconclusive"]
    status = "error" if any(levels[c.rule_id] == "must" for c in incomplete) else (
        "failure" if any(f.blocking for f in findings) else "success")
    result = ReviewResult(**metadata, findings=findings, criteria=criteria, status=status,
        execution="partial" if incomplete else "complete", not_applicable=not_applicable,
        limitations=[f"{c.path} [{c.rule_id}]: {c.reason}" for c in incomplete])
    if model is not None:
        from .semantic import analyze
        result = analyze(repository, result, changes, model, limits or Limits())
    if project_debt:
        from .debt import include_debt
        result = include_debt(repository, result, profile)
    return result
