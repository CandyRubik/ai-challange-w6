# Срез 4c — trusted GitHub pipeline

Base: `2cf713608276ff5fe7dfd338c8247e4059ea702e`.

## Standards

Закрыт retarget freshness gap: edited при изменении base запускает review, title/body
не вызывают API/cloud. Trusted checkout, минимальные job permissions, run/attempt artifacts,
data-only Git fetch проверены. После выделения packing подтверждены все зависимости
самостоятельного workflow-среза. Итог: **0 open**, actionable smell calls0.

## Spec

Закрыты три P2: failed-only rerun оставлял previous same-run success, retarget не запускал
анализ, draft не откладывался. Same-run older attempt теперь error с summary; newer run
сохраняется. Retarget/draft covered; обе manual identity проверяются до model construction.
Subset после split проверен отдельно, scope/dependencies сохранены. Итог: **0 open**.

## Проверки

- **413 local tests passed**, compileall app/reviewer/tests/scripts, node syntax и diff-check.
- Actionlint1.7.12 проверил оба workflow; официальный release checksum проверен.
- Mock tests: revision/code/run/attempt mismatch, fork/manual owner, stale fetch, missing key,
  invalid proposed profile, queue capacity, token вне argv/file, deferred draft, retarget,
  failed-only rerun/новый run. PR head не checkout/execute, actual checkout остаётся base.
- Cloud/key transfer не выполнялись; real GitHub smoke следует после bootstrap в main
  и отдельного разрешения источника ключа/recurring CI. По умолчанию missing key →error.

627 reviewable lines:27 сверх цели600, ниже cap1000. Самостоятельный packing81lines уже
в main(PR10); оставшееся превышение — обязательные границы prepare/analyze/publish и
интеграционные регрессии. Отделение оставило бы workflow без необходимого fail-closed
контракта. Future inline/unknownPOST recovery5, debt6, owner7, onboarding8 не включены.

CI требует current main в ancestry head: без этого head-only RAG не представляет merge
result. Main push инвалидирует open PR, при behind-main нужен rebase. GitHub без CAS;
при runner/API аварии возможен pending до полного rerun. На текущем private Free тарифе
status не гарантирует запрет merge, как согласовано Q9.

После merge и отдельного явного разрешения выполнен [real smoke](ci-smoke.json):
forbidden import →failure/complete, pure helper →success/complete. Тот же bot summary
обновлён; всего3model requests, каждый≤96KB/4000output. PR12 закрыт без merge, main не
содержит smoke helper. Actions secret настроен, ключ не записывался в файлы W6.
