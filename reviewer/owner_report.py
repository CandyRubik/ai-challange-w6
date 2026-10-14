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
        content = content.encode()[:budget].decode('utf-8',errors='ignore')+'\n\nОтчёт сокращён; полные записи и исходный анализ доступны в артефактах запуска.'
    return normalize_comment('\n\n'.join([*header,content,*footer]))


def decision_details(identity,decision):
    p = decision.proposal
    return (f'**{text(identity)}** — {text(decision.actor)}; {text(p.action)}; '
        f'цели: {text(", ".join(p.targets))}; область: {text(", ".join(p.scope) or "точная цель и снимок")}; '
        f'срок: {text(p.expires_at or "явный отзыв")}; причина: {text(p.reason)}.')


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
    prefix = [SUMMARY_MARKER,'# Ревью проекта',f'Итог проверки: **{plan.state}**; исходный анализ: **{result.status}** / **{result.execution}**.',
        f'Коммит PR: `{context.head_sha}`; база: `{context.base_sha}`.',
        f'Осталось блокирующих замечаний: {len(plan.remaining_findings)}; записей долга: {len(plan.remaining_debt)}.',
        f'Срок исключения: {text(plan.expires_at or "нет")}.',
        'Исходные замечания, серьёзность и блокирующие флаги сохранены в полном результате. Исключения владельца влияют только на итог проверки.']
    details = ['## Решения владельца',*owner_notes(plan,decisions)]
    if not plan.accepted_findings and not plan.accepted_debt:
        details.append('Нет применимого принятия владельцем; истёкшие, отозванные или выходящие за область решения не меняют проверку.')
    details += ['## Исходный анализ',original['body'].partition('\n')[2]]
    return dict(state=plan.state,body=bounded_body(prefix,'\n\n'.join(details),[f'[Текущий запуск проверки]({context.run_url})'])),plan


def finding_note(findings,plan,decisions):
    accepted = [(fingerprint(f),plan.accepted_findings.get(fingerprint(f))) for f in findings]
    lines = [f'Замечание `{identity}`: '+decision_details(owner,decisions[owner]) for identity,owner in accepted if owner]
    if not lines:
        return ''
    return normalize_comment('\n\n'.join(['Принятие владельцем перечисленных замечаний. Исходный отчёт сохранён; актуальность принятия смотрите в последнем отчёте и журнале.',*lines]))


def debt_note(debt_id,plan,decisions):
    identity = plan.accepted_debt.get(debt_id)
    return normalize_comment('Принятие владельцем: '+decision_details(identity,decisions[identity])) if identity else ''


def journal(context,decisions,plan=None):
    lines = [decision_details(identity,decision) for identity,decision in sorted(decisions.items())]
    content = normalize_comment('\n\n'.join(lines) if lines else 'Нет актуальных подтверждённых решений владельца.')
    header = [OWNER_MARKER,'# Журнал решений владельца',f'Коммит PR: `{context.head_sha}`.',
        'Полномочия подтверждаются каноническими authorize jobs и артефактами Actions. Изменение или удаление комментария не меняет решения.',
        f'Итог проверки: **{plan.state if plan else "error"}**.']
    footer = ['Запуски с артефактами решений: '+', '.join(f'[{text(identity)}]({workflow_url(context.repository,*map(int,identity.split("-")))})'
        for identity in sorted(decisions) if identity.count('-')==1 and all(p.isdigit() for p in identity.split('-'))),
        f'[Текущий запуск проверки]({context.run_url})']
    return bounded_body(header,content,footer)
