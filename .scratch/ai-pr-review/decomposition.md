# AI-ревью PR — карта реализации

- Дата: 2026-10-13.
- Основание: [план и Q1–Q11](plan.md), [исследование GitHub](research.md).
- Trunk: `main`, исследован на `f7a4d1e`.
- Статус карты: согласована пользователем 2026-10-13; PR №4–11, №13–16, №18–35 слиты (№33 — временный smoke, закрыт без merge). Project-debt gate включён в CI; чистый artifact module готов. Все26paired offline inputs калибровочных сценариев сохранены.
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
| 4b | Фактический context budget | Локальный CLI учитывает формат провайдера до вызова, снимает optional chunks и заранее проверяет capacity схемы | Нет новых вызовов/секретов; обязательные источники сохраняются | 81 фактически | 4a | merged |
| 4c | Автоматический GitHub pipeline | Trusted workflow запускает анализ PR, публикует summary/status, обновляет ревью при main push | Reviewer только из main; PR data-only; отдельные jobs/permissions | 627 фактически | 4b | merged |
| 5a | Восстановление summary | Неопределённый POST подтверждается чтением; повторная финализация не создаёт дубль | Только существующий summary/status, без inline | 100 фактически | 4c | merged |
| 5b | Планирование inline | Локальный CLI показывает проверенные anchors и причины summary fallback | Offline preview, без API и публикации | 310 фактически | 5a | merged |
| 5c0 | Общий нормализатор комментариев | Повторно используемая нормализация без усечения; прежний presentation cap сохраняется | Чистая локальная функция, wire protocol не меняется | 20 фактически | 5b | merged |
| 5c | Публикация inline | Batch COMMENT, восстановление неопределённого POST и superseded-маркеры старых замечаний | Только закреплённый результат и повторная проверка актуальности | 434 фактически | 5c0 | merged |
| 5d | Причинность на повторяющемся коде | Git diff определяет causal ranges и не относит неизменённый хвост к новой ошибке | Offline regression/replay сохранённых ответов; новые paid eval не запускаются | 118 фактически | 5c | merged |
| 5e1 | Безопасная диагностика model response | Trace отличает schema, references, ranges, blank evidence и envelope errors | Только фиксированные коды и allowlisted имена; prompt/verdict/retries прежние | 109 фактически | 5d | merged |
| 5e2 | Смежные mandatory spans | Уже выбранные смежные окна кода объединяются, evidence может пересекать старый seam | Точный текст/path/SHA/role; gaps, normative и optional не меняются | 60 фактически | 5e1 | merged |
| 6a | Локальный реестр долга | CLI проверяет active debt на head отдельно от PR findings и предлагает статические candidates | Явный локальный opt-in, пустой реестр; текущий CI не включает долг | 422 фактически | 5e2 | merged |
| 6b0 | Trusted debt context | Registry/hash из base закреплены в Actions context; старые raw artifacts читаются с прежним digest | Metadata/provenance этого среза; пустой реестр | 85 фактически | 6a | merged |
| 6b0r | Чистый модуль артефактов | Data contract и pure validation отделены от GitHub writes; прежние импорты совместимы | Механический перенос без изменения AST/JSON/поведения | 255 фактически | 6b0 | merged |
| 6b0s | Точные imported-function contexts | Полные определения функций с imports имеют приоритет над большими lexical windows | Статический поиск без исполнения; прежниеallowlist/corpus/budgets | 253 фактически | 6b0r | merged |
| 6b1 | Долг в CI gate | Покрытие и actual head валидируются, active долг влияет на check/summary | Registry из base, повторный expiry guard вокруг writes | 181 фактически | 6b0s | merged |
| 6b2a | Общий upsert комментариев | Recoverable POST/owned marker общий для summary и будущего debt | Прежний summary API и status-only finalizer сохранены | 53 фактически | 6b1 | merged |
| 6b2 | Общий комментарий долга | Один обновляемый project-level comment отдельно от inline новых дефектов | Snapshot/lifecycle guards и восстановление uncertain POST | 213 фактически | 6b2a | merged |
| 7a | Offline policy владельца | Preview scoped dismiss/waiver/resolve/revoke без CI authority | Оригинальный result/flags неизменны; pure validation | 402 фактически | 6b2 | merged |
| 7b | Actions provenance adapter | Bounded collections и безопасный artifact JSON download для решений/analysis | Fixed GitHub origin, no auth to signed blob, exact member/digest/caps | 306 фактически | 7a | merged |
| 7c | Проверенный источник и журнал | Read-only source CLI и replay reader по Actions job/artifact proof | actor+trigger_actor/current owners/main code; comments не authority | 411 фактически | 7b | merged |
| 7d0 | Reviewable owner report | Offline Markdown показывает effective/original, remaining blockers и owner notes | Pure rendering, без CI authority или записи | 196 фактически | 7c | merged |
| 7d1 | Явный reuse анализа | Owner dispatch повторно использует complete source того же снимка без LLM | Канонический Actions proof, digest, actual debt; нет paid fallback | 194 фактически | 7d0 | merged |
| 7d2a | Owner notes | Оригинал inline/debt сохраняется при добавлении или снятии owner note | Optional hooks не дают полномочий и не меняют verdict | 64 фактически | 7d1 | merged |
| 7d2 | Сервис применения решений | Проверенный journal меняет effective check и оформляет owner notes | Journal/expiry guard вокруг writes; original неизменён | 264 фактически | 7d2a | merged |
| 7d3a | Owner context module | Typed journal/hash/cache selection и строгий replay guard | Самостоятельный API; CI activation отдельно | 144 фактически | 7d2 | merged |
| 7d3b | CI wiring | Prepare/analyze/publish подключают проверенный owner context | Включается только при ownerworkflow вtrustedbase | 58 фактически | 7d3a | in-progress |
| 7d3c | Owner dispatch | Authorize/apply workflow на main, без LLM | Owner actor+trigger, source freshness/provenance, perPR concurrency | 400–600 | 7d3b | planned |
| 7e | Истечение решений | Zero-LLM sweeper инвалидирует success с истёкшим exception | Bounded20PR, отдельный error до rerun; schedule best effort | 200–400 | 7d3c | planned |
| 5f | Опровержение advisory findings | Советующие semantic findings также проходят adversarial verification | Прежние4calls/96KB/4000tokens; raw historical counts сохранены | 100–300 | 7e | planned |
| 8 | Интервью по проекту, после MVP | Skill собирает факты, проводит интервью и формирует профиль/preview prompt для утверждения | Отдельный onboarding; CI использует прежний валидированный артефакт | 350–550 | 5f | planned |

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
- Поставка разделена на 5a (summary recovery), 5b (offline anchors) и 5c (автоматический batch). Общий diff укладывался в кодовый бюджет, но полный обязательный RAG-контекст занимал 130453 байта при согласованном CI-лимите 96000. Разделение сохраняет контекст и лимит без скрытого усечения.

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

- 2026-10-13: PR №10 слит после396local tests, зелёного CI и обоих subset-review0open. Workflow4c восстановлен без конфликтов на main2cf7136. В полном review закрыты retarget edited, deferred draft и failed-only rerun (same-run older attempt→error; newer run сохраняется). Cloud key/recurring CI ещё не разрешены и не настроены.

- 2026-10-13: PR №11 слит (CIgreen,413local, обе оси0open). Пользователь явно разрешил W5→Actions Secret DEEPSEEK_API_KEY и recurring CI:4requests/PR run,96KB/4000tokens,20PRqueue. Secret настроен после merge, без W6 file/argv/log copy. Bootstrap main run38454906674 success. Temporary PR12: failure/complete для forbidden import(2calls), success/complete для pure helper(1call); тот же summary6153557233 обновлён. PR закрыт без merge, smoke branch удалён. [Evidence](ci-smoke.json). Дополнительных calibration runs нет, прежний бюджет79/104 не менялся.

- 2026-10-14: полный inline self-review0open и455localtests, но CIдал2InvalidModelResponseпри92KB. Разделён общий formatter5c0 и inline5c, чтобы уменьшить mandatory context без ослабления validation/caps. Полныйdiffсохранён, PR15сужендоformatter.

- 2026-10-14: PR25 merged b372fc9 after CIgreen and Standards0/Spec0. AI38489005837 complete/success,1call94140bytes. Full local529 +final dependency21. Gate restored after module split:181code,76checks,95075wire offline, expiry present. No additional paid calibration.

- 2026-10-14: PR23 merged4bd3233; AI38489504054 success/complete,1call95165bytes,findings0. Full6b2offline required98684bytes after8optional removed, noHTTP. Shared upsert cut as6b2a53code; remaining general report follows without changing requested behavior or caps.

- 2026-10-14: PR26 merged after CIgreen,Standards0/Spec0,43localchecks; AI38490129020 complete/success,1call69279bytes,findings0. General report213code/36checks fits95741bytes offline. Full self-review0open after one-time54KB details cap, originalmetadata outside truncation and preserved oversizedlegacy failure.

- 2026-10-14: PR27 merged e9bf732 after CI560green and Standards0/Spec0. Linux-only oversized pytestID fixed with explicitascii/unicode IDs, runtime unchanged. Final AI38491021152 complete/success1call95855bytes. Owner work remains same approved scope; split policy/provenance/journal/apply/expiry to stay review-sized. Actual advisory false BUGS from PR25/27 require same bounded counterargument stage before onboarding; no new blocking rules or model budget.

- 2026-10-14: PR28 mergedf80e8dc,CIgreen/Standards0/Spec0,23policytests; AI38491992951 complete/success2calls68765/69101bytes(formatretry). Actionsadapter306code/38tests/live-read smoke verified; journal remains separate.

- 2026-10-14: PR29 mergedd47f15e afterCIgreen/38tests/Standards0/Spec0/liveread; AI38492986348 complete/success1call69914bytes,findings0. 7c keepsworkflowactivationwithapplicationservice; readonlysource/journal reader411code28tests+live verified, race/mainanchor fixed. NoAtomicGETpromise.

- 2026-10-14: PR30 merged3146bea, CIgreen/Standards0/Spec0/28tests+live source; AI38495921898 complete/success1call63822bytes,findings0. Owner presentation cut7d0 provides standalone offlineMarkdown196code/8tests, protectsmetadata and no-input-leak notes. Application/workflow remainsnext, sameapprovedscope.

- 2026-10-14: PR31 merged9fb41d5 после CIgreen/Standards0/Spec0/8presentation tests. Срез7d1 отдельно даёт ручной zero-model reuse; owner application остаётся следующим срезом, без расширения полномочий или API budget.

- 2026-10-14: PR32 mergedbcdaef2 после CIgreen/Standards0/Spec0; AI38498491557 complete/success1call93851bytes. PR33 temporary: source38498722722 complete/success1call49869; reuse38498881674 complete/success0calls, исходные verdict/findings/criteria/policy/debt совпали. PR закрыт без merge. Full application8717 потребовал103447bytes mandatory; notes выделены отдельно, лимиты прежние.

- 2026-10-14: PR34 merged1ceae44 после finalreadyCIgreen/Standards0/Spec0;4focused notes cases+35prior combined. Auto-review первоначально связал merge с локальнымservice84441a5, проверка точногоheadf26f782 иtree подтвердиларазделение, повтор разрешён. Service initial/empty journal иValueError expiry races закрыты,21tests pass.

- 2026-10-14: PR35 mergedae556cd после finalCIgreen/Standards0/Spec0;14application+8report tests. AI38500003169 complete/success1call92412bytes, findings0. Captured replay отдельно отdispatch:169reviewablelines,9runtime+28CI/reusepass.

- 2026-10-14: Fullreplay mandatory103619bytes потребовал разделитьcontext module иCI wiring. Source deletion теперь проверяется как mismatch ожидаемогоcache/origin, безpaidfallback. Limitsнерасширены.
