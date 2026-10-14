"""Pure presentation of owner effects; rendering is not authentication."""
from .artifacts import preview
from .contracts import SUMMARY_MARKER,workflow_url
from .decisions import effects,fingerprint
from .report import normalize_comment,text

OWNER_MARKER = '<!-- ai-review:owner-journal:v1 -->'


def bounded_body(header,content,footer):
    content = normalize_comment(content)
    budget = min(54_000,58_000-len(normalize_comment('\n\n'.join([*header,*footer])).encode())-300)
    if budget<0:
        raise ValueError('Owner report metadata exceeds presentation budget')
    if len(content.encode())>budget:
        content = content.encode()[:budget].decode('utf-8',errors='ignore')+'\n\nPresentation shortened; full records and original analysis are in the workflow artifacts.'
    return normalize_comment('\n\n'.join([*header,content,*footer]))


def decision_details(identity,decision):
    p = decision.proposal
    return (f'**{text(identity)}** — {text(decision.actor)}; {text(p.action)}; '
        f'targets: {text(", ".join(p.targets))}; scope: {text(", ".join(p.scope) or "exact target/snapshot")}; '
        f'until: {text(p.expires_at or "explicit revocation")}; reason: {text(p.reason)}.')


def owner_notes(plan,decisions):
    identities = sorted(set(plan.accepted_findings.values())|set(plan.accepted_debt.values()))
    return [decision_details(identity,decisions[identity]) for identity in identities]


def render(context,raw,decisions,*,now=None):
    original = preview(context,raw)
    from .contracts import ReviewArtifact
    result = ReviewArtifact.model_validate(raw).result
    plan = effects(context,result,decisions,now=now)
    if not decisions and plan.state==original['state']:
        return original,plan
    prefix = [SUMMARY_MARKER,'# Project review',f'Effective check: **{plan.state}**; original analysis: **{result.status}** / **{result.execution}**.',
        f'Head: `{context.head_sha}`; base: `{context.base_sha}`.',
        f'Remaining blocking findings: {len(plan.remaining_findings)}; project debt: {len(plan.remaining_debt)}.',
        f'Exception deadline: {text(plan.expires_at or "none")}.',
        'Original findings, severity and blocking flags are unchanged in the full result. Owner exceptions affect only the effective check.']
    details = ['## Owner decisions',*owner_notes(plan,decisions)]
    if not plan.accepted_findings and not plan.accepted_debt:
        details.append('No applicable owner acceptance; expired, revoked or out-of-scope records do not change this check.')
    details += ['## Original analysis',original['body'].partition('\n')[2]]
    return dict(state=plan.state,body=bounded_body(prefix,'\n\n'.join(details),[f'[Current workflow run]({context.run_url})'])),plan


def finding_note(findings,plan,decisions):
    accepted = [(fingerprint(f),plan.accepted_findings.get(fingerprint(f))) for f in findings]
    lines = [f'Finding `{identity}`: '+decision_details(owner,decisions[owner]) for identity,owner in accepted if owner]
    if not lines:
        return ''
    return normalize_comment('\n\n'.join(['Owner acceptance for the findings listed below. Original report is preserved; check the latest summary/journal for current applicability.',*lines]))


def debt_note(debt_id,plan,decisions):
    identity = plan.accepted_debt.get(debt_id)
    return normalize_comment('Owner acceptance: '+decision_details(identity,decisions[identity])) if identity else ''


def journal(context,decisions,plan=None):
    lines = [decision_details(identity,decision) for identity,decision in sorted(decisions.items())]
    content = normalize_comment('\n\n'.join(lines) if lines else 'No current authenticated owner decisions.')
    header = [OWNER_MARKER,'# Owner decision journal',f'Head: `{context.head_sha}`.',
        'Authority comes from canonical Actions authorize jobs and artifacts. Deleting or editing this comment does not change decisions.',
        f'Effective check: **{plan.state if plan else "error"}**.']
    footer = ['Decision artifact runs: '+', '.join(f'[{text(identity)}]({workflow_url(context.repository,*map(int,identity.split("-")))})'
        for identity in sorted(decisions) if identity.count('-')==1 and all(p.isdigit() for p in identity.split('-'))),
        f'[Current workflow run]({context.run_url})']
    return bounded_body(header,content,footer)
