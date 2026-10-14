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
    lines = [f'Реестр: `{result.debt_sha256}`.']
    for item in result.project_debt:
        lines += [f'- **{text(item.debt_id)}** / {text(item.rule_id)}: **{item.verdict}** '
                  f'({"блокирует" if item.blocking else "совет"}) — {text(item.title)}; {text(item.reason)}; '
                  f'проверено файлов: {len(item.checked_paths)}, нарушений: {item.violation_count}.']
        lines += [f'  Доказательство: {evidence_text(ref)}' for ref in item.evidence]
    if not result.project_debt:
        lines.append('В доверенном реестре нет записей о долге проекта.')
    return lines


def markdown(result: ReviewResult, *, explain_policy: bool = False) -> str:
    lines = ["# Ревью проекта", f"Результат: **{result.status}**; выполнение: **{result.execution}**."]
    if result.snapshot:
        lines += [f"База: `{result.snapshot.base_sha}`", f"Коммит PR: `{result.snapshot.head_sha}`",
                  f"База diff: `{result.snapshot.merge_base_sha}`"]
    lines += [f"Профиль: `{result.profile_sha256}` ({result.profile_origin}).",
              "Оценены только настроенные проверки; полное архитектурное ревью не проводилось.", "", "## Замечания"]
    for group in finding_groups(result.findings):
        lines += finding_details(group)
    if not result.findings:
        lines.append("Настроенные проверки не обнаружили новых нарушений.")
    if result.debt_sha256 or result.project_debt:
        lines += ['', '## Долг проекта', *debt_details(result)]
    lines += ["", "## Покрытие проверки"]
    lines += [f"- {text(c.path)} / {text(c.rule_id)}: **{c.verdict}** — {text(c.reason)}" for c in result.criteria]
    lines += [f"- {text(path)}: вне области правил (не проверен)." for path in result.not_applicable]
    if result.limitations:
        lines += ["", "## Ограничения", *[f"- {text(reason)}" for reason in result.limitations]]
    if explain_policy:
        lines += ["", "## Правила проекта"]
        lines += [f"- **{text(r.id)}** [{r.level}, {r.check}] {text(r.requirement)}; область: {text(', '.join(r.scope))}" for r in result.policy]
    if result.recommendations:
        lines += ["", "## Рекомендации", *[f"- {text(item)}" for item in result.recommendations]]
    if result.trace:
        lines += ["", "## Анализ", f"Модель: {text(result.trace.get('model', ''))}; запросов: {result.trace.get('model_calls', 0)}",
                  f"Необязательных фрагментов пропущено: {result.trace.get('optional_fragments_omitted', 0)}; недоступных источников: {len(result.trace.get('optional_sources_unavailable', []))}",
                  f"SHA256 контекста: `{result.trace.get('context_sha256', '')}`"]
    return "\n\n".join(lines) + "\n"
