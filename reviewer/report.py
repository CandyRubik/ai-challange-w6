import re

from .models import ReviewResult


def text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|])", r"\\\1", value).replace("\n", " ")


def finding_groups(findings):
    groups = {}
    for finding in findings:
        ref = finding.evidence
        key = ("change", finding.cause_id) if finding.cause_id else (ref.sha, ref.path, ref.line, ref.kind, ref.quote)
        groups.setdefault(key, []).append(finding)
    return list(groups.values())


def evidence_text(ref):
    location = 'метаданные' if ref.kind == 'file_metadata' else str(ref.line)
    return f"{text(ref.path)}:{location} @ `{ref.sha}` — {text(ref.quote)}"


def normalize_comment(body):
    return ''.join(c for c in re.sub('@(?!\u200b)', '@\u200b', body) if c in '\n\t' or ord(c) >= 32)


def safe_comment(body):
    body = normalize_comment(body)
    if len(body.encode()) > 58_000:
        body = body.encode()[:58_000].decode('utf-8', errors='ignore') + '\n\nОтчёт сокращён; полный результат доступен в артефакте запуска.'
    return body


def finding_details(group):
    evidence = group[0].evidence
    lines = [f"- **{text(', '.join(f.rule_id for f in group))}** ({'блокирует' if any(f.blocking for f in group) else 'совет'}): {text('; '.join(f.message for f in group))}",
             f"  Доказательство: {evidence_text(evidence)}"]
    for finding in group:
        if finding.evidence != evidence:
            lines.append(f"  {text(finding.rule_id)} — доказательство: {evidence_text(finding.evidence)}")
        if finding.source:
            lines.append(f"  Основание правила: {evidence_text(finding.source)}")
        if finding.scenario:
            lines += [f"  {text(finding.rule_id)} — сценарий: {text(finding.scenario)}; последствия: {text(finding.impact)}",
                      f"  Рекомендация: {text(finding.recommendation)}"]
    return lines


def debt_details(result):
    lines = [f'Registry: `{result.debt_sha256}`.']
    for item in result.project_debt:
        lines += [f'- **{text(item.debt_id)}** / {text(item.rule_id)}: **{item.verdict}** '
                  f'({"blocking" if item.blocking else "advisory"}) — {text(item.title)}; {text(item.reason)}; '
                  f'checked files: {len(item.checked_paths)}, violations: {item.violation_count}.']
        lines += [f'  Evidence: {evidence_text(ref)}' for ref in item.evidence]
    if not result.project_debt:
        lines.append('The trusted registry contains no project debt entries.')
    return lines


def markdown(result: ReviewResult, *, explain_policy: bool = False) -> str:
    lines = ["# Project review", f"Status: **{result.status}**; execution: **{result.execution}**."]
    if result.snapshot:
        lines += [f"Base: `{result.snapshot.base_sha}`", f"Head: `{result.snapshot.head_sha}`",
                  f"Diff base: `{result.snapshot.merge_base_sha}`"]
    lines += [f"Profile: `{result.profile_sha256}` ({result.profile_origin}).",
              "Only configured checks are assessed; this is not a full architectural review.", "", "## Findings"]
    for group in finding_groups(result.findings):
        lines += finding_details(group)
    if not result.findings:
        lines.append("No new violations found by the configured checks.")
    if result.debt_sha256 or result.project_debt:
        lines += ['', '## Project debt', *debt_details(result)]
    lines += ["", "## Coverage"]
    lines += [f"- {text(c.path)} / {text(c.rule_id)}: **{c.verdict}** — {text(c.reason)}" for c in result.criteria]
    lines += [f"- {text(path)}: outside rule scope (not assessed)." for path in result.not_applicable]
    if result.limitations:
        lines += ["", "## Limitations", *[f"- {text(reason)}" for reason in result.limitations]]
    if explain_policy:
        lines += ["", "## Policy"]
        lines += [f"- **{text(r.id)}** [{r.level}, {r.check}] {text(r.requirement)}; scope: {text(', '.join(r.scope))}" for r in result.policy]
    if result.recommendations:
        lines += ["", "## Recommendations", *[f"- {text(item)}" for item in result.recommendations]]
    if result.trace:
        lines += ["", "## Analysis", f"Model: {text(result.trace.get('model', ''))}; calls: {result.trace.get('model_calls', 0)}",
                  f"Optional fragments omitted: {result.trace.get('optional_fragments_omitted', 0)}; unavailable sources: {len(result.trace.get('optional_sources_unavailable', []))}",
                  f"Context SHA256: `{result.trace.get('context_sha256', '')}`"]
    return "\n\n".join(lines) + "\n"
