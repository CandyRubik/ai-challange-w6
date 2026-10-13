import re

from .models import ReviewResult


def text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|])", r"\\\1", value).replace("\n", " ")


def markdown(result: ReviewResult, *, explain_policy: bool = False) -> str:
    lines = ["# Project review", f"Status: **{result.status}**; execution: **{result.execution}**."]
    if result.snapshot:
        lines += [f"Base: `{result.snapshot.base_sha}`", f"Head: `{result.snapshot.head_sha}`",
                  f"Diff base: `{result.snapshot.merge_base_sha}`"]
    lines += [f"Profile: `{result.profile_sha256}` ({result.profile_origin}).",
              "Only configured checks are assessed; this is not a full architectural review.", "", "## Findings"]
    for finding in result.findings:
        evidence, source = finding.evidence, finding.source
        location = "metadata" if evidence.kind == "file_metadata" else str(evidence.line)
        lines += [f"- **{text(finding.rule_id)}** ({'blocking' if finding.blocking else 'advisory'}): {text(finding.message)}",
                  f"  Evidence: {text(evidence.path)}:{location} @ `{evidence.sha}` — {text(evidence.quote)}"]
        if source:
            lines.append(f"  Rule source: {text(source.path)}:{source.line} @ `{source.sha}` — {text(source.quote)}")
        if finding.scenario:
            lines += [f"  Scenario: {text(finding.scenario)}; impact: {text(finding.impact)}",
                      f"  Recommendation: {text(finding.recommendation)}"]
    if not result.findings:
        lines.append("No new violations found by the configured checks.")
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
