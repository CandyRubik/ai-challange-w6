# Срез 4a — GitHub publisher

Base: `06afa6b30fc9b78d2e68742bb30de6c00af91a91`.

## Standards

Закрыты P2: failed freshness GET оставлял предыдущий success; partial/unknown rules
принимались без доказанного покрытия; небезопасные metadata проходили в legacy Markdown.
Проверяется captured snapshot/policy/checks, numeric/hash metadata и evidence revisions.
Весь HTTP этап охвачен best-effort инвалидацией своего status; недоступный GitHub не
превращает CLI в success. Позитивные known-should и major BUGS сохраняют advisory success.
Обнаруженная регрессия rename исправлена через pinned before-path/SHA/status mapping.
Итог независимой проверки: **0 open**. Actionable smell judgement calls: **0**.

## Spec

Закрыты P2: must violation без finding мог дать success, result.policy могла ослабить
закреплённый профиль, чужая revision evidence принималась, cleanup не охватывал summary
race при повторе. Captured context теперь содержит сам профиль, checks и полный diff
snapshot; статус согласуется с правилами/criteria/findings. Дополнительно закрыты
регрессии advisory BUGS и cross-file supporting, подменявшего отсутствующий диагноз.
Primary causal path связывается с head либо before по pinned rename mapping.
Итог независимой проверки: **0 open**.

## Проверки и границы

- Полный локальный набор до последних двух path-regressions: **392 passed**.
- Текущий publisher suite: **34 passed**, включая обе новые регрессии.
- Compileall app/reviewer/tests/scripts, node syntax, diff-check прошли.
- Нет paid API calls, переноса ключей или реальных GitHub comments/status writes.
- Preview offline; --publish требует credentials в env и явно подготовленного manifest.
- Digest — проверка целостности; same trusted Actions run и executing code SHA проверяет
  срез 4b. Inline и recovery неопределённого POST входят в срез 5. GitHub API без CAS;
  если API недоступен и для error write, запуск остаётся неуспешным и требует rerun.

629 reviewable lines: на29 выше цели600, ниже cap1000. Превышение — исправления и
регрессии content/freshness контракта до поставки publisher; отделение сохранило бы
известные false-green/false-rejection. Будущие workflow/inline/debt не включены.
