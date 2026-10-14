"""Pack and validate pinned review artifacts; return a report without effects."""
import re
from .contracts import ReviewArtifact, SUMMARY_MARKER, digest
from .report import markdown


def make_artifact(context, result):
    body = result.model_dump()
    return ReviewArtifact(context=context, result=result, result_sha256=digest(body)).model_dump()



def preview(context, raw):
    artifact = ReviewArtifact.model_validate(raw)
    result = artifact.result
    if artifact.context != context or digest(raw['result']) != artifact.result_sha256:
        raise ValueError('Review artifact identity or digest mismatch')
    if context.code_sha != context.base_sha or result.profile_origin != 'base':
        raise ValueError('CI requires code and profile from trusted base')
    if result.profile_sha256 and result.profile_sha256 != context.profile_sha256:
        raise ValueError('Review profile mismatch')
    if result.snapshot and result.snapshot.model_dump() != {key: getattr(context, key) for key in ('base_sha', 'head_sha', 'merge_base_sha', 'diff_sha256')}:
        raise ValueError('Review snapshot mismatch')
    if result.status != 'error' and (not result.snapshot or not result.profile_sha256 or result.execution == 'failed'):
        raise ValueError('Completed review is missing provenance')
    if result.status == 'success' and any(f.blocking for f in result.findings):
        raise ValueError('A blocking finding cannot produce success')
    if result.policy != context.profile.rules and (result.policy or result.status != 'error'):
        raise ValueError('Review rules differ from captured base policy')
    rules = {rule.id: rule for rule in context.profile.rules}
    must = {rule.id for rule in context.profile.rules if rule.level == 'must'}
    if any(item.rule_id not in {*rules, 'BUGS'} for item in [*result.criteria, *result.findings]):
        raise ValueError('Unknown review rule')
    pairs = [(c.path, c.rule_id) for c in result.criteria]
    expected = {(path, rule) for path, checks in context.checks.items() for rule in checks}
    if result.status != 'error' and (set(pairs) != expected or len(pairs) != len(expected)):
        raise ValueError('Review criteria do not cover the captured changes')
    incomplete = [c for c in result.criteria if c.verdict == 'inconclusive']
    if result.status != 'error' and any(c.rule_id in must | {'BUGS'} for c in incomplete):
        raise ValueError('Incomplete mandatory analysis must produce error')
    if result.status != 'error' and result.execution == 'partial' and not incomplete:
        raise ValueError('Partial review has no explicit unchecked criteria')
    if result.status == 'success' and any(c.rule_id in must and c.verdict == 'violation' for c in result.criteria):
        raise ValueError('Mandatory violation cannot produce success')
    def corresponds(finding, criterion):
        ref, path = finding.evidence, criterion.path
        return finding.rule_id == criterion.rule_id and (
            ref.sha == context.head_sha and ref.path == path and context.change_status.get(path) != 'D'
            or ref.sha == context.merge_base_sha and ref.path == context.before_paths.get(path) and context.change_status.get(path) != 'A')
    if (any(c.verdict == 'violation' and not any(corresponds(f, c) for f in result.findings) for c in result.criteria)
        or any(not any(c.verdict == 'violation' and corresponds(f, c) for c in result.criteria) for f in result.findings)):
        raise ValueError('Violations and findings contradict each other')
    refs = [ref for c in result.criteria for ref in c.evidence]
    for finding in result.findings:
        rule = rules.get(finding.rule_id)
        if finding.blocking != (rule.level == 'must' if rule else finding.severity == 'critical'):
            raise ValueError('Finding blocking flag contradicts base policy')
        if rule and (not finding.source or finding.source.sha != context.base_sha
                     or finding.source.path != rule.source.path or finding.source.quote != rule.source.quote):
            raise ValueError('Finding normative source differs from base policy')
        refs.extend([finding.evidence, *finding.supporting_evidence, *([finding.source] if finding.source else [])])
    if any(ref.sha not in {context.base_sha, context.head_sha, context.merge_base_sha}
           or ref.line < (1 if ref.kind == 'text' else 0) for ref in refs):
        raise ValueError('Evidence is outside the captured revisions or line range')
    for key in ('model_calls', 'optional_fragments_omitted'):
        if key in result.trace and (type(result.trace[key]) is not int or result.trace[key] < 0):
            raise ValueError('Invalid numeric trace metadata')
    if 'context_sha256' in result.trace and not re.fullmatch(r'[0-9a-f]{64}', str(result.trace['context_sha256'])):
        raise ValueError('Invalid context digest')
    from .debt_publishing import validate_debt
    validate_debt(context, result)
    body = markdown(result).replace('@', '@\u200b')
    body = ''.join(c for c in body if c in '\n\t' or ord(c) >= 32)
    if len(body.encode()) > 58_000:
        body = body.encode()[:58_000].decode('utf-8', errors='ignore') + '\n\nPresentation shortened; full result is in the run artifact.'
    return {'state': result.status, 'body': f'{SUMMARY_MARKER}\n{body}\n[Workflow run]({context.run_url})'}

