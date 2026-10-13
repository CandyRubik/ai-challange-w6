# AI-ревью PR — карта реализации

- Дата: 2026-10-13.
- Основание: [план и Q1–Q11](plan.md), [исследование GitHub](research.md).
- Trunk: `main`, исследован на `f7a4d1e`.
- Статус карты: согласована пользователем 2026-10-13; PR №4–9 слиты; quality gate пройден. Срез 4b (context budget) готов от main `6d7e66b`; workflow выделен в 4c и сохранён для следующего PR.
- Бюджет: целевой размер ≤600 reviewable lines (added + removed), максимум 1000 с отдельным обоснованием. Это оценки; при росте срез делится до открытия PR. Документация и большие входные fixtures не входят в кодовый бюджет; prompt/profile, ручные тесты и workflow считаются проверяемой логикой.

## Последовательность

| № | PR | Самостоятельный результат | Безопасность включения | Бюджет строк | Зависит от | Статус |
|---|---|---|---|---|---|---|
| 1 | Ручной профиль и локальный аудит правил | CLI объясняет применимые правила и проверяет первые формализуемые границы на закреплённом Git diff, выводит JSON/Markdown | Новый отдельный CLI, основной чат не вызывает его | 639 фактически | — | merged |
| 2 | Локальное AI-ревью с code/docs RAG | CLI анализирует PR-снимок через DeepSeek с retrieval и выдаёт bugs/architecture/recommendations с источниками | Явный локальный запуск, без автоматических внешних публикаций | 717 фактически | 1 | merged |
| 3 | Калибровка блокирующих выводов | Версионированный eval и проверка контраргументов отсекают неподтверждённые finding; видны precision/coverage/cost | Проверяется качество уже работающего CLI; paid eval запускается явно | 504 фактически | 2 | merged |
| 3b | Устойчивый формат и повторная калибровка | Единый assessment вместо противоречивых массивов, надёжная привязка evidence, свежий holdout | Исправляет выявленную incomplete-rate до автоматизации; API только явно | 630 фактически | 3 | merged |
| 3c | Восстановление невалидного ответа и оценка дублей | Один явный bounded retry при невалидном ответе, проверяемые аннотации спорных eval-диагнозов, завершение failed cases | Невалидные ответы не дают success; прошлые raw counts сохраняются; без нового полного paid-прогона | 471 фактически | 3b | merged |
| 4a | GitHub-адаптер результата | Закреплённый PR context, проверка provenance готового JSON, summary и head-bound status | Явный CLI; без API key и workflow; snapshot/generation проверяются до публикации | 629 фактически | 3c | merged |
| 4b | Фактический context budget | Локальный CLI учитывает формат провайдера до вызова, снимает optional chunks и заранее проверяет capacity схемы | Нет новых вызовов/секретов; обязательные источники сохраняются | 81 фактически | 4a | ready |
| 4c | Автоматический GitHub pipeline | Trusted workflow запускает анализ PR, публикует summary/status, обновляет ревью при main push | Reviewer только из main; PR data-only; отдельные jobs/permissions | 627 фактически | 4b | queued |
| 5 | Inline и устойчивость публикации | Причинные изменения получают корректные inline anchors, rerun не дублирует результат, старое поколение не заменяет новое | Публикует уже проверенный результат; summary fallback при отсутствии anchor | 350–550 | 4c | planned |
| 6 | Подтверждённый долг проекта | Активный реестр перепроверяется на head, отдельные общие комментарии влияют на check; fixing PR распознаётся | Реестр из trusted base, изначально пустой; candidates требуют подтверждения | 400–600 | 5 | planned |
| 7 | Решения владельца | Scoped dismiss/waiver/resolve с причиной и сроком переживают rerun и пересчитывают check | Trusted manual workflow, проверка владельца и provenance журнала | 400–600 | 6 | planned |
| 8 | Интервью по проекту, после MVP | Skill собирает факты, проводит интервью и формирует профиль/preview prompt для утверждения | Отдельный onboarding; CI использует прежний валидированный артефакт | 350–550 | 7 | planned |

День 32 минимально закрывается после среза 4c. Согласованный MVP с inline, долгом и решениями владельца закрывается после среза 7. Интервью и последующее выделение репозитория не задерживают первую версию.

## Границы срезов

### 1. Профиль и локальный аудит

- Входит: schema и начальный ручной профиль; immutable snapshot/diff; безопасное чтение Git; explain-policy; первый AST-check чистоты StateService; результат с evidence и coverage.
- Не входит: LLM, GitHub publishing, долг проекта, глобальное переустройство RAG.
- Проверка: временный repo с новым must-нарушением, чистым изменением, rename и неподдерживаемым файлом; неизвестная схема отклоняется.
- Самостоятельная ценность: инженер может проверить профиль и формализуемые границы до настройки API key.
- Размер: 639 reviewable lines, на 39 выше целевых 600 и ниже cap 1000. Дополнение — исправления и тесты обнаруженных self-review случаев alias/dynamic imports, конкретных подмодулей и эквивалентной записи. Вынос этих исправлений отдельно оставил бы известные false-positive/false-success в первом CLI.
- Проверки и результаты двух осей self-review: [отчёт первого среза](slice1-review.md).

### 2. Локальное AI-ревью

- Входит: retrieval по symbols/imports/тексту docs; контекст before/base/head; DeepSeek adapter; finding schema; проверка evidence; Markdown и JSON; явные лимиты.
- Не входит: автоматический запуск, E5-веса в CI, полное доказательство свойств программы.
- Проверка: связанному неизменённому caller и нормативному документу находится место в контексте; модельная выдумка и truncated JSON не становятся finding.
- Если реальные объёмы выходят из бюджета, выделить самостоятельный локальный RAG-preview перед подключением генерации, не увеличивать PR без пересмотра карты.
- Фактически 717 reviewable lines, ниже cap 1000: превышение цели на 117 строк — функциональные checks, бюджеты и regression cases causal evidence/privacy/decorators. Они нужны до поставки сквозного CLI; отдельный follow-up оставил бы известные утечки контекста или ложные вердикты. Изменения не включают будущие eval/publisher/debt.
- Реальный DeepSeek smoke: 1 call, 6918 байт bundle, 5.231 s, complete; нормативное нарушение обнаружено. Итог двух осей self-review и проверки — [отчёт](slice2-review.md).

### 3. Качество блокирующих выводов

- Входит: попытка опровергнуть critical/must finding; размеченные дефекты и чистые рефакторинги; holdout; eval CLI и измерения; различение false finding и incomplete.
- Не входит: автоматическая корректировка профиля по оценкам модели, обещание эксплуатационной точности на маленькой выборке.
- Проверка: seeded invariant regression найдена, действующее исключение/чистый refactor не блокируются; counts и ошибочные выводы видны в отчёте.
- Ценность среза: экспериментальная проверка основной гипотезы до автоматических комментариев и cloud-spend на каждом PR.

### 4. GitHub summary и status

- Входит: trusted target/manual workflow; snapshot/provenance между jobs; summary review; commit status на head; invalidation при main push; ограниченная очередь/retries; fork/Dependabot/error paths.
- Не входит: inline, настройки branch protection, GitHub App, merge queue.
- Проверка: GitHub mock; один реальный smoke PR после появления workflow в main; bad PR даёт failure, чистый success, API/model failure error.
- Bootstrap: `pull_request_target` использует main. Пока workflow не влит, проверять локально и через mocks; наличие YAML в feature branch само по себе не запускает trusted flow.

### 5. Inline и публикация

- Входит: diff anchor validator, batch review, run/finding identity, восстановление после неопределённого POST, recheck актуальности, сериализация поколений, summary fallback.
- Не входит: arbitrary agent tools, автоматическое исправление кода, автоматический APPROVE/merge.
- Проверка: LEFT/RIGHT, rename/delete, caller вне diff, force-push, два PR одного SHA, rerun и частично успешная публикация.

### 6. Долг проекта

- Входит: реестр с candidate/active/resolved/waived; локальное предложение кандидатов; перепроверка active и closure criteria на head; отдельные project-level comments и влияние на check.
- Не входит: автоматическое признание модельной гипотезы обязательным долгом, полный аудит всего repo на каждом PR, внешний трекер.
- Проверка: unrelated doc PR получает failure от active debt; candidate его не вызывает; исправляющий PR снимает нарушение по проверяемому условию; невозможность перепроверки видна отдельно.

### 7. Решения владельца

- Входит: ручной trusted dispatch; actor authorization, обязательная причина, scope и expiry; проверяемый журнал решений в PR; replay, повторное вычисление status и owner-confirmed closure.
- Не входит: снятие finding любым автором, интерпретация свободного текста моделью как разрешения, самовольное ослабление профиля из head.
- Проверка: копия marker от пользователя отклоняется; другой actor/SHA/profile/истёкший waiver не применяется; повтор не теряет решение.

### 8. Интервью

- Входит: репозиторный skill, detection фактов о стеке, recommended варианты/контрпримеры, явное утверждение профиля и preview prompt.
- Не входит: новый chat UI, возврат удалённых user profiles, интерактивность внутри CI.
- Проверка: сквозной onboarding этого проекта и одного второго небольшого repo; неизменённый профиль даёт тот же compiled prompt.

## Интеграционная стратегия и cleanup

Все срезы добавляют завершённый CLI или автономную возможность reviewer; текущий chat runtime их не вызывает. Workflow подключается после калибровки, когда его зависимости уже в main. Временные feature flags, замена старого chat-flow и branch by abstraction не нужны; обязательного cleanup таких механизмов нет.

Секреты и ограничения paid-run настраиваются перед PR4 smoke. Во время первой настройки проверка может явно сообщать configuration error, но не должна выдавать ложный success. Возможность настоящего required gate остаётся отдельной задачей после решения вопроса GitHub-тарифа.

## Журнал решений

- 2026-10-13: rebase текущей ветки на свежий main завершён без конфликтов; HEAD `f7a4d1e`.
- 2026-10-13: Q1–Q11 определили manual-profile MVP, DeepSeek, минимизацию FP, inline новых дефектов, project-level active debt, owner decisions и check без текущей гарантии запрета merge.
- 2026-10-13: отдельный reviewer/CLI выбран для будущего переноса; текущий docs-only RAG и chat lifecycle остаются самостоятельными продуктами.
- 2026-10-13: предложены lexical/symbol RAG без обязательных embedding-весов, trusted data-only workflow и отдельный head-bound commit status. Подробности и ограничения — в плане.
- 2026-10-13: пользователь согласовал план и карту. Первый срез использует ранее согласованный тестовый шов ReviewInput → ReviewResult; проверены Git-снимок, нормативные источники, imports, CLI и неполный анализ без LLM.
- 2026-10-13: по явному разрешению пользователя PR №4 слит; CI и 287 локальных тестов прошли. Второй срез начат после обновления main.

- 2026-10-13: пользователь разрешил слить PR №5 и последующие PR по карте после зелёного CI и self-review. Разрешение не включает перенос ключей или дополнительные API-запросы.

- 2026-10-13: первый реальный eval (20 cases/27 calls) не прошёл quality gate: 11 incomplete, 0 clean blocked. Добавлен самостоятельный срез 3b: structured output/grounding и повторная калибровка до автоматического workflow. Подробности и source-version caveat — [slice3-review](slice3-review.md). Единственное разрешение на этот eval использовано; новые API calls требуют отдельного разрешения.

- 2026-10-13: PR №6 слит после зелёного CI и self-review (Standards 0 open, Spec 0 open). Срез 3b использует line-assessments-v2; предыдущие 20 cases теперь development, 6 новых holdout. Первый eval не используется как успешная калибровка новой версии.

- 2026-10-13: второй eval дал 11/13 найденных регрессий, 12/13 clean pass, 0 clean blocked, 3 incomplete, 38 API calls. Четыре исходных FP — повтор того же дефекта под must/BUGS; версия 3 разметки отличает duplicates и объединяет одинаковые anchors в Markdown, сохраняя машинные findings. Номера строк/короткие IDs показываются явно; прежние invalid ranges не принимаются и не «чинятся» молча.

- 2026-10-13: срез 3b завершён (630 code lines, 343 tests, обе оси 0 open). Превышение цели на30 — исправления обнаруженных grounding/provenance ошибок и regression tests, ниже cap1000. Последний полный eval: 13/13 clean pass, 3 incomplete из-за невалидного JSON/blank citation. Добавлен самостоятельный 3c для явного bounded format retry и проверяемой adjudication дублей до активации workflow. Источник — [slice3b-review](slice3b-review.md).

- 2026-10-13: пользователь предложил компьютер как self-hosted CI для будущих embedding/E5 весов. В текущем MVP retrieval по symbols/text работает на обычном GitHub runner; самостоятельная машина остаётся инфраструктурной альтернативой после измерения пользы векторного поиска. Runner сейчас не устанавливается.

- 2026-10-13: срез 3c завершил только три incomplete случая последнего eval (6 новых requests, всего79/104 одобренных). Итог26 cases: TP13/FP0/FN0/incomplete0, 13 clean pass, 0 clean blocked, 5 duplicates после двух независимо проверенных exact annotations; rawFP2 сохранены. Quality gate пройден, дальнейшие paid-прогоны остановлены. [Две оси review и ограничения](slice3c-review.md).

- 2026-10-13: PR №8 слит после зелёного CI и обеих осей 0 open. Срез 4 разделён до реализации: 4a поставляет самостоятельный GitHub publisher готового результата, 4b подключает trusted workflow и разрешённый cloud-анализ. Это удерживает API/provenance и инфраструктуру в отдельных review-sized PR; поведение MVP не меняется.

- 2026-10-13: срез 4a — 629 code lines, на29 выше цели600 и ниже cap1000. Дополнение — исправления обнаруженных self-review false-green/false-rejection: pinned policy/coverage, допустимые SHA, advisory BUGS, stale/HTTP cleanup и rename identity. Отделение оставило бы известные ошибки проверки результата в уже поставленном publisher. Workflow/inline/debt в срез не включены.

- 2026-10-13: PR №9 слит после зелёного CI (394tests), обе оси0open. Полный CI diff вырос до708code lines; до открытия PR выделен самостоятельный packing-срез4b (81строка), полезный локальному CLI. Reviewed workflow4c сохранён отдельным patch и применяется после merge4b. В нём627строк: превышение цели27 — обязательные trusted-job/provenance/failure guards и regression cases retarget/draft/rerun, без независимой будущей функции.

- 2026-10-13: offline packing на PR9: internal90.7KB не отражал actual103.1KB; transport-aware packing снимает2optional chunks и даёт94.9KB при cap96KB. Все42 сохранённых calibration payloads побайтно прежние (max27018bytes); новых APIcalls нет. Capacity критериев проверяется до paid generation. Workflow требует актуального main в head, чтобы head-only RAG представлял будущий merge result; behind-main даёт error/rebase.
