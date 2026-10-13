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
        lines += [f"- **{text(finding.rule_id)}** ({'blocking' if finding.blocking else 'advisory'}): {text(finding.message)}",
                  f"  Evidence: {text(evidence.path)}:{evidence.line} @ `{evidence.sha}` — {text(evidence.quote)}",
                  f"  Rule source: {text(source.path)}:{source.line} @ `{source.sha}` — {text(source.quote)}"]
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
    return "\n\n".join(lines) + "\n"
