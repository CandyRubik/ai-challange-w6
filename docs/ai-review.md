# Локальный аудит PR

AI-ревью предоставляет независимый CLI и ручной профиль проекта. По умолчанию
он выполняет статический аудит импортов. Явный `--semantic` добавляет DeepSeek
и retrieval по коду, тестам и документации. GitHub-комментарии, калибровка
блокирующих выводов и долг проекта добавляются следующими срезами
[плана](../.scratch/ai-pr-review/plan.md).

## Запуск

После установки `requirements-dev.txt`:

```bash
python scripts/review_pr.py --repo . --base origin/main --head HEAD
python scripts/review_pr.py --base origin/main --head HEAD --format json
python scripts/review_pr.py --base origin/main --head HEAD --explain-policy
```

По умолчанию `.ai-review/profile.json` берётся из base. Изменения профиля в head
и несохранённые файлы рабочего дерева не меняют результат. Профиль, его источники,
head и merge base фиксируются хешами; содержимое PR никогда не импортируется
или исполняется. Источник может быть checkout или bare repository с доступными
объектами; CLI не делает fetch. Недостающая история/commit даёт явную ошибку.

При первом подключении профиля ещё нет в base. Явно передайте доверенный файл:

```bash
python scripts/review_pr.py --base origin/main --head HEAD \
  --profile .ai-review/profile.json --explain-policy
```

В отчёте этот режим отмечен `explicit_local`. Автоматический CI впоследствии
использует профиль base, а не этот механизм bootstrap.

Коды выхода: `0` — настроенные проверки выполнены без блокирующих находок;
`1` — найдено новое must-нарушение; `2` — ошибка или неполная обязательная проверка.
`success` относится только к правилам и покрытию в отчёте. Файлы вне scope
перечисляются как неоценённые; наличие старого нарушения не выдаётся за новый баг.

## Профиль

Схема версии 1 находится в `reviewer/models.py`; неизвестные поля, другая версия,
повторные ID и пустая конфигурация импортов отклоняются. Каждый rule имеет:

- `id`, `category` (`architecture/forbidden/best_practice/invariant`), `must/should` в `level`;
- `scope` — glob по полному относительному пути в семантике Python `fnmatch`: `*` включает `/`;
- требование `requirement`, основание `rationale`, примеры `examples`;
- `source.path` и точную `source.quote` в доверенном base;
- `check`: `python-import-boundary` с `forbidden_imports` либо `semantic`.

Для `python-import-boundary` анализируются абсолютные и относительные импорты.
Запрет распространяется на указанный модуль и его подмодули. Неоднозначные формы
вроде `from .. import storage` могут импортировать обычный атрибут: они требуют
семантического анализа и получают `inconclusive`, а не выдуманное нарушение.
Уже существующая запрещённая зависимость не становится новой
из-за перестановки строки; добавление другого запрещённого подмодуля обнаруживается.
Если прежний `from package import name` мог уже импортировать этот подмодуль,
изменение формы импорта получает `inconclusive` вместо ложного нового нарушения.
Перенос модуля в новую запрещённую область считается
новым нарушением; перенос внутри той же области сохраняет исходную базу сравнения.

Это проверка статических импортов, а не доказательство чистоты произвольного
Python. Полная чистота `StateService` вынесена в отдельное семантическое правило.
Распознанные динамические импорты, включая aliases `importlib.import_module` и
`builtins.__import__`, требуют семантического анализа. Невалидный Python,
бинарные/не-UTF-8 файлы, symlinks/submodules и источники больше 512 000 байт не
получают ложный `pass`. Удаление файла удовлетворяет только проверке его импортов.

Без `--semantic` семантическое правило даёт `inconclusive`. Для обязательного правила это означает
`execution=partial`, `status=error`, даже если статических находок нет. Ошибка
профиля или отсутствующее нормативное доказательство даёт `execution=failed`.
Необязательное непроверенное правило отображается в limitations; оно не проваливает
итог, если обязательные проверки выполнены.

JSON сохраняет точный текст evidence, SHA и строки кода и нормативного источника.
Markdown экранирует полученные данные и показывает findings, coverage, limitations
и по запросу policy. Успешный аудит не является автоматическим approve PR.

## Семантическое ревью

Настройте `DEEPSEEK_API_KEY` в окружении процесса. CLI не загружает `.env`
автоматически и не принимает ключ в аргументах. `DEEPSEEK_MODEL` и
`DEEPSEEK_BASE_URL` задают модель и endpoint адаптера; используются существующие
настройки проекта (`deepseek-v4-flash`, `https://api.deepseek.com`).

```bash
python scripts/review_pr.py --base origin/main --head HEAD --semantic --format json
python scripts/review_pr.py --semantic --context-bytes 48000 --output-tokens 4000
```

Нормативный профиль и документация читаются из base; proposed implementation —
из head, прежняя реализация — из merge base. AST расширяет изменённые участки
до полных затронутых функций, сохраняется начальный фрагмент модуля. Лексический
поиск добавляет до восьми связанных фрагментов неизменённого кода, тестов и docs.
Это ограниченный retrieval-контекст; полный граф влияния не строится.

Корпус ограничен исходниками проекта и README/docs/CONTEXT. `.env`, credentials,
private.env, data, произвольные корневые файлы и внешние ссылки исключены.
Изменённый недоступный исходник даёт incomplete; недоступные дополнительные
источники, исключения и снятые по бюджету optional chunks перечисляются в trace.
Allowlist применяется к head и before: перенос из исключённого data/credential
файла останавливает анализ до обращения к модели, даже если head уже очищен.

По умолчанию разрешены до четырёх модельных вызовов с явными format retries, 4000 токенов ответа на вызов, 48 000 байт
сериализованного prompt/schema/input, 30 изменённых файлов, 500 документов
корпуса и 2 МБ его текста. Поиск ограничен 30 секундами; HTTP timeout — 60 секунд,
thinking и скрытые SDK retries отключены. Лимиты доступны через `Limits`, два
основных лимита — также через CLI. Обязательные фрагменты не обрезаются: превышение
даёт error до платного вызова. Необязательные фрагменты снимаются с конца ранжирования.
Упаковка учитывает optional `measure_input` провайдера: DeepSeek измеряет тот же формат
messages/line references/schema, который отправляет в API. Generic JsonModel сохраняет
прежний интерфейс. Capacity критериев по output-схеме проверяется до генерации; невозможный
полный ответ даёт error без запросов. `trace.input_budget_bytes` и `input_packer_version`
отмечают эту проверку. [Offline evidence](../.scratch/ai-pr-review/ci-context-evidence.json)
показывает исправление на реальном diff и побайтную совместимость42 calibration payloads;
измерение через mock не является новым quality eval.

Модель оценивает каждую пару path/rule и общий критерий BUGS. Проверяются строгая
схема, полнота критериев, согласованность violations/findings, существование
fragment ID, уникальное точное вхождение цитаты и её связь с изменёнными строками.
Для удаления допустимо evidence из before; пустые файлы/переименования/изменения
режима имеют отдельное проверяемое evidence `kind=file_metadata`, без выдуманных
строк кода. Findings включают сценарий, последствия, рекомендацию и связанные
доказательства; критерии тоже сохраняют evidence.

Обязательное правило и critical bug дают failure; major/minor bugs и рекомендации
остаются советами. Inconclusive обязательного критерия, сбой модели, невалидные
цитаты или противоречивый результат дают error. Детерминированные проверки модель
не переопределяет. Достоверность смысловых выводов оценивается отдельным набором
реальных/чистых изменений из набора калибровки; точные цитаты не доказывают правильность
интерпретации.

Trace содержит модель, версии prompt/retriever, хеш всего входного bundle,
объём контекста, число вызовов, время и ограничения retrieval. Секреты и raw model
outputs туда не включаются. Публикации или исполнения команд по ответу модели нет.

## Калибровка блокирующих выводов

Семантический запуск использует два этапа (до четырёх запросов с bounded retries): первый ищет проблемы, второй пытается
опровергнуть `must`/critical finding по тому же закреплённому контексту. Только `confirmed`
остаётся блокирующей находкой; `rejected` снимается с объяснением в `trace.verification`,
`inconclusive` и невалидный ответ дают `error`. Статические проверки модель не отменяет.
Каждый запрос ограничен теми же context/output budgets, SDK retries отключены.

Версия 3 набора `.ai-review/eval/cases.json` содержит 26 случаев: 24 синтетических
регрессии/допустимые альтернативы и 2 случая, извлечённых из исправления `faefa30`
(исправление и его обратная регрессия). Development (20) включает просмотренные случаи
первого прогона; holdout (6) содержит свежие контракты, по которым prompt не менялся. При изменениях prompt используйте development; holdout запускайте для
контрольной проверки, не подгоняйте prompt под его ответы.

```bash
.venv/bin/python scripts/evaluate_review.py --split all
# Явный платный запуск, ключ задаётся только через окружение:
.venv/bin/python scripts/evaluate_review.py --split all --run --output /tmp/review-eval.json
```

Eval ограничивает каждый запрос 32 КБ контекста и 2000 токенов ответа, с format retries максимум 80
запросов для полного набора версии 1; для версий 2/3 — 104 запроса. Исторические прогоны
до добавления retry имели лимиты 40/52; они не переписываются. Код сценариев только читается, не исполняется.
Отчёт включает TP/FP/FN для блокирующих finding (правило, путь, ожидаемая causal quote),
precision/recall, clean-blocked, incomplete, latency, calls, token usage и хеши артефактов.
Отсутствующий usage — `null`, не нулевой расход. Gate требует всех выбранных регрессий,
ни одной чистой блокировки, полного анализа и precision ≥95%; маленькая выборка
не доказывает эту точность в эксплуатации. Цена в деньгах не оценивается без актуального
тарифа. Нельзя объявлять качество проверенным, если прошли только mock/unit tests.

### Формат генерации и grounding

DeepSeek получает один assessment на path/rule; finding вложен в violation. Противоречивые
verdict/finding отклоняются. BUGS предназначен для дополнительных дефектов, без дублирования
того же нарушения политики. Pass означает отсутствие доказанного внесённого дефекта
по критерию, а не доказательство безопасности любых отсутствующих зависимостей.

Модель получает короткие fragment ID, явно пронумерованный текст и конечную строку каждого
фрагмента; выбирает ID и абсолютный диапазон строк. Код проверяет границы и извлекает
точную цитату; одинаковые строки различаются координатами. Metadata использует диапазон 0/0.
Проверка causal diff сохраняется. Generic JsonModel по-прежнему может давать точные цитаты,
которые проверяются на уникальность, либо проверяемый диапазон.

Смежные mandatory окна одного path/SHA/role объединяются с сохранением точных
байтов. Evidence может охватывать прежний стык окон; gaps не заполняются.
Normative quotes и optional ranked chunks остаются отдельными. Code SHA и digest
фактического входа закрепляют конкретную реализацию retrieval contract.

Для module-level Python `from … import …` retrieval ищет полное определение
используемой функции в существующем Git-модуле. Новые/изменённые runtime-вызовы
имеют приоритет перед общими утилитами и test helpers; затем выбираются меньшие
функции. Передаются необходимые imports либо небольшой exact prefix/module,
без исполнения. Неоднозначные paths, shadowed names и неподдерживаемые формы
остаются на lexical fallback. Прежние8optional/corpus/byte budgets сохраняются.

Causal intervals вычисляются Git Myers, включая файлы с повторяющимися строками.
Координаты поддерживают LF и CRLF. Одиночный CR и другие необработанные Unicode
разделители строк дают явный error до модели; используйте escape-последовательности
для таких символов внутри исходника.

В `trace.wire_inputs` сохраняются хеш/байты фактических messages со схемой и факт отправки
HTTP-запроса; транспорт отдельно проверяет лимит перед отправкой. Eval различает попытки
`model_calls` и известные фактические `api_calls`; отсутствующие транспортные сведения — null.
Raw ответы и ключи в trace не попадают. `.ai-review/` исключён из optional runtime retrieval,
чтобы профиль и gold labels не выдавались за доказательство поведения. Изменения этих файлов
остаются входом PR, а нормативные правила поступают из trusted base.

При невалидном ответе `wire_inputs[].validation_failure` содержит фиксированный code:
schema, unknown_reference, evidence_range, blank_evidence, metadata_range,
decision_identity, api_envelope, incomplete_completion, response_budget либо
invalid_structure. Для schema сохраняются только allowlisted типы и имена полей;
значения, неизвестные имена полей и сырой ответ не сохраняются. Диагностика не
меняет строгую валидацию, retry budgets или verdict.
Post-ground/core schema failures также видны в `trace.validation_failures`;
generic JsonModel получает эту диагностику без изменения своего интерфейса.

Повтор одного causal anchor под несколькими правилами отображается одной группой; все
исходные findings, правила и влияние на gate сохраняются в JSON. Eval отдельно считает
`duplicates`: только явно размеченный дополнительный BUGS по тому же gold-дефекту и
тому же Evidence, уже найденному нормативным правилом, либо проверяемой before/head-паре
однострочной замены 1→1 с цитатами целой изменённой строки. Общий opcode не доказывает
один дефект: разные head-anchors, многострочные и несимметричные замены остаются FP. Это повторный диагноз,
не новый дефект. Другой anchor или неразмеченное правило остаются FP. Первые сырые counts
не переписываются; изменение метрик/разметки отмечается отдельно. Precision/recall считаются
по уникальным gold-дефектам; число повторов видно отдельно и не выдаётся за точность модели.

Соседние диагнозы одного изменения могут отображаться одной группой; отдельные evidence,
сценарии, правила и машинные findings сохраняются. Для удаления guard цитата head-контекста
должна охватывать обе стороны пустого интервала; causal evidence извлекается из фактически
удалённых before-строк. Номер chunk может разрешаться по абсолютным координатам только
в другом уже переданном фрагменте того же path/SHA/role/kind, однозначно и без изменения
диапазона. Исходный ID и разрешение видны в trace; отсутствующие строки и чужая ревизия
отклоняются. Redundant path/rule_id verifier принимаются лишь при совпадении с candidate.

### Восстановление ответа и оценка результата

Структурно невалидный ответ (JSON/schema/envelope/blank citation) допускает один явный
повтор этапа по тем же источникам и policy. Он получает краткую инструкцию исправить
структуру; сырой ответ в prompt не пересылается. Каждый запрос и расход токенов видны
в trace. Неуспешный повтор даёт error; HTTP/auth/timeouts автоматически не повторяются.
Бюджет заново проверяется и для generic JsonModel, и для фактического DeepSeek wire.
Для format retry используется temperature=0; [DeepSeek рекомендует 0 для coding/math](https://api-docs.deepseek.com/quick_start/parameter_settings/).

Калибровка текущей версии: [26 случаев с сохранёнными raw counts](../.scratch/ai-pr-review/calibration-final.json),
13/13 регрессий, 13/13 clean success, 0 incomplete. Две exact annotations независимо
подтвердили повтор корневого дефекта, исходные FP2 и все findings сохранены. Полные
прогоны не повторялись после успеха; continuation завершил только три incomplete случая.
Это небольшой уже просмотренный набор; дальнейшие изменения требуют свежего holdout.

### Подтверждённый долг проекта

`.ai-review/debt.json` из base хранит `candidate`, `active`, `resolved`, `waived`.
Candidate ничего не блокирует. Для остальных состояний нужны текущий владелец
профиля и причина; waiver ограничен UTC-сроком. Блокирующий долг ссылается на must
правило либо подтверждённый critical BUGS. Historical evidence проверяется по Git.

```bash
# Только предложение кандидатов, без записи или активации:
.venv/bin/python scripts/review_debt.py --suggest --head HEAD
# Локальная проверка без API и публикации:
.venv/bin/python scripts/review_debt.py --base origin/main --head HEAD
```

Для `python-import-boundary` перепроверяется полный scope правила на head, до 500
файлов / 2 МБ / 30 секунд. Перенос импорта в другой файл scope не снимает долг.
Отсутствие запрещённых импортов даёт `fixed`; динамический импорт, невалидный или
нечитаемый исходник даёт error. Для `owner-attestation` требуется явное закрытие
владельцем, даже если код изменился. Project debt имеет отдельные assessments,
не становится новой inline-находкой PR. Правки реестра в head не ослабляют base.

CI включает аудит trusted base registry после анализа PR. Active blocking debt
даёт failure даже на несвязанном изменении; неполное покрытие даёт error. Перед
публикацией скан повторяется по pinned head. Lifecycle/expiry проверяются вокруг
каждой записи, чтобы истёкший waiver не оставил успешный check. Отчёт показывает
долг отдельно. Один обновляемый общий комментарий показывает оценки и подтверждение
владельца; старый долг не становится inline PR finding. При неполном ревью предыдущая
оценка сохраняется с её head/run и явной пометкой; успешное полное ревью обновляет её.
Human comments и чужие markers не меняются. Неопределённый POST подтверждается чтением,
повторный POST вслепую не выполняется; сбой оставляет check неуспешным.

### GitHub-адаптер

`reviewer.contracts` хранит pinned context/artifact records, digest и run URL;
`reviewer.artifacts` упаковывает и проверяет результат без внешних эффектов;
`reviewer.publishing` отвечает за GitHub freshness/generation и запись комментариев
и status. Прежние импорты из publishing сохранены для CLI и workflow.

`reviewer.publishing` фиксирует repository ID/PR/base/head/merge-base/diff/profile/code SHA и номер
Actions run/attempt. `capture` читает профиль и нормативные источники из immutable base;
в контексте закрепляются список правил и все ожидаемые path/rule проверки по Git diff.
Primary evidence связывается с causal path по head либо before SHA и закреплённой
карте rename; supporting callers не заменяют отдельный диагноз другого файла.
workflow должен отдельно подтвердить, что выполняет именно этот code SHA. Адаптер не
исполняет head и не использует адреса/номера PR из ответа модели.

Actions context также закрепляет trusted base registry/hash долга; check_context
сверяет их с Git до анализа. Preview проверяет полное покрытие долга, lifecycle,
scope, evidence и соответствие результата registry/hash.
Digest артефакта проверяется по исходному JSON result: новые optional defaults модели
не меняют сохранённый digest; строгая схема и все проверки происхождения сохраняются.

```bash
python scripts/publish_review.py --context context.json --artifact result.json
# Offline: GitHub files JSON + доступные pinned Git objects, без credentials:
python scripts/publish_review.py --context context.json --artifact result.json \
  --inline-plan files.json --repo .
# Явная публикация подготовленного trusted-run artifact, GITHUB_TOKEN только в env:
python scripts/publish_review.py --context context.json --artifact result.json --publish
```

По умолчанию CLI только показывает Markdown и возвращает 0/1/2 по verdict; credentials
и сеть для preview не нужны. Перед записью проверяются точная context identity, digest,
SHA результата, profile origin и отсутствие противоречивого success. `start` создаёт
pending. Publisher сверяет полный snapshot, применённые правила, покрытие и допустимые
ревизии evidence; неизвестные правила и небезопасные metadata отклоняются. Digest проверяет
целостность, аутентификация источника artifact обеспечивается trusted workflow.
`publish` обновляет bot summary и head-bound commit status. Нормативный
inconclusive требует error; только необязательные непроверенные критерии могут остаться
советующими, как в локальном CLI. `fail` завершает незавершённый запуск отдельным error.

API фиксирован на api.github.com; redirects и скрытые повторы POST отключены. Pagination
обязательна, её предел даёт ошибку без частичного успешного результата. 403/429/503
отображаются как transport error без тела ответа/токена в диагностике. Summary экранирует
Markdown/HTML и mentions; сокращение длинного представления помечается, JSON сохраняется
в run artifact. При неоднозначности двух open PR с одним SHA success запрещён.

Base/head проверяются до/после записи; поколение run/attempt нельзя понизить. Обнаруженная
гонка после status write инвалидирует наш status. GitHub не предоставляет CAS для
комментариев/statuses; отказ freshness GET также вызывает best-effort инвалидацию нашего
результата. Если GitHub недоступен и для error write, CLI/workflow остаётся неуспешным;
при восстановлении API нужен rerun.
Workflow сериализует jobs одного PR. При неопределённом результате создания summary publisher
до трёх раз читает комментарии и ищет точный bot-owned отчёт, повторно проверяя актуальность.
Если запись не подтверждена, status остаётся error; повторный POST в этом запуске запрещён,
в том числе при финализации ошибки. Новый полный rerun найдёт уже созданный summary и обновит его.
До настройки API key модель не вызывается, проверка сообщает configuration error.

### GitHub Actions

`AI Review` реагирует на PR в main (opened/synchronize/reopened/ready_for_review/retarget), push main
и ручной workflow_dispatch с номером PR. Main push заново ставит pending на открытые PR;
старый вердикт не считается проверкой новой policy. На push выбираются максимум20 PR,
два параллельно; превышение очереди даёт error всем выбранным heads без paid analysis.
Pagination не обрезает успешный результат. Изменения title/body не вызывают AI.
Draft сохраняет неуспешную проверку без cloud-analysis до ready либо owner manual dispatch.

CI требует, чтобы current main был предком head. Иначе данные head не представляют будущий
merge result: новая реализация callers из main могла отсутствовать в RAG. Такой PR получает
error с просьбой rebase. Локальный CLI сохраняет сравнение с merge-base; нормативная policy
всегда из указанного base. Strict-up-to-date проверка CI уменьшает неоднозначность анализа.

Root workflow выбирает PR; reusable workflow сериализует все jobs одного PR. Prepare
проверяет GitHub identity и Git snapshot. Analyze имеет только contents/PR/statuses read,
а DeepSeek key доступен только его шагу. Publish получает PR/statuses write без DeepSeek key.
Checkout всегда на github.workflow_sha из main; actual HEAD, workflow SHA и captured base
должны совпадать. PR ref только fetch-ится по фиксированному HTTPS URL; head никогда не
checkout/install/import/execute. Git token передаётся через env+askpass, не через argv/file.

Context/result artifacts скачиваются только из текущего trusted run, с PR и run_attempt в
имени. Проверяются repository/run/attempt/code/profile и PR identity; artifact размером
больше2МБ отклоняется. Ошибка/отсутствие artifact дают error status и summary с причиной;
для повторного анализа выбирайте **Re-run all jobs** или новый dispatch. Failed-only rerun
не использует артефакты старого attempt: он инвалидирует прежний success своего run и
просит полный повтор. Более новое поколение другого run не затрагивается.
API failure не превращается в success. Pending возможен после аварии runner/API до finalize:
нужен повтор. GitHub statuses/comments не имеют CAS; branch protection тут недоступна,
поэтому реального запрета merge на текущем тарифе по-прежнему нет.

Fork/Dependabot PR не отправляются в DeepSeek автоматически. Owner запускает `AI Review`
вручную на main с номером PR; original actor и triggering actor должны быть владельцами
из профиля. Ручной rerun старого workflow после изменения main нужно заменить новым dispatch.
Модель фиксирована: deepseek-v4-flash, api.deepseek.com, максимум4 requests на один PR run,
по96КБ messages и4000 output tokens; 30 файлов,500 corpus files/2МБ,10 минут на job.
Фактический формат провайдера измеряется до запроса: снимаются только optional fragments,
обязательный контекст не обрезается. SDK retries отключены; daily spending cap не заявлен.
Количество критериев также проверяется по лимиту схемы до платной генерации.

[Offline-замер](../.scratch/ai-pr-review/ci-context-evidence.json) на реальном PR9: прежний
packer пропускал внутренний90.7КБ bundle, но actual messages103.1КБ не помещались в96КБ.
Теперь сняты2optional chunks, actual94.9КБ; обязательные источники сохранены. Все42
сохранённых wire payloads последней калибровки побайтно совпали и помещаются в32КБ
(максимум27018bytes). Измерения выполнены без real API calls и не являются новым eval.

Настройка владельцем: Actions secret `DEEPSEEK_API_KEY` в этом репозитории. Значение не
записывается в checkout. После настройки можно сделать workflow_dispatch на main и указать PR.
До появления ключа модель не вызывается: публикуется configuration error. Доступ write к
репозиторию остаётся доверенным; этот workflow изолирует недоверенный PR content.
См. [GitHub о privileged triggers](https://docs.github.com/en/actions/reference/security/secure-use)
и [same-commit reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).

`evaluate_review.py --resume REPORT --split all` планирует и повторяет только incomplete
случаи того же case-set, split и лимитов. Прошлые запросы/токены и снимки сохраняются.
Для старого формата используйте `prepare_report(report, corpus)`; отсутствующие лимиты
можно перенести только из сохранённых входов фактического прогона, не угадывать.

`--annotations FILE` применяет проверенную разметку только к точному finding/context
при уже найденном нормативном gold. Она различает повторный диагноз и самостоятельный FP.
Case-set hash и fingerprint связывают label с конкретным результатом; другой текст, SHA
или context не получает исключение. Raw summary и каждый finding остаются в отчёте.
Разметка не создаёт TP, не очищает incomplete/clean blocking и не участвует в PR gate,
профиле или решениях владельца. Подготовка старого отчёта сохраняет его исходные counts.

### Планирование inline

`--inline-plan` принимает сохранённый ответ GitHub PR files API и показывает JSON комментариев
без сети и секретов; режим несовместим с `--publish`. Проверяются Git blob SHA, фактические
изменения относительно merge-base и изменённые строки полного patch. Primary evidence
определяет anchor: head → RIGHT, before → LEFT; rename учитывает прежний путь. Caller вне diff,
metadata, отсутствующий/truncated patch или недоказуемая строка остаются в summary.
Несколько правил на одной строке объединяются с сохранением каждого основания и сценария.
План ограничен10 комментариями; остальные findings сохраняются в полном отчёте.
CI publisher повторно fetch-ит только pinned PR objects и публикует один batch `COMMENT` на head.
Он сверяет bot ownership, head, anchor, marker и принадлежность комментария к `COMMENTED` review.
Rerun обновляет тело существующего комментария. Неизвестный POST подтверждается максимум тремя
чтениями review и комментариев с коротким backoff; нужны точные тела в том же опубликованном batch.
Неподтверждённый или pending batch требует error и полного rerun, без повторного POST в этом запуске.
Batchv2 хранит отправленные anchor keys и хеш полного плана. Rerun сначала восстанавливает
объявленные anchors, даже если набор замечаний изменился. Однажды увиденные declarations/orphan
comments не забываются при пустой следующей выборке; cached identity не заменяет текущего proof.
После complete analysis старые owned inline помечаются superseded с сохранением исходного текста;
это не означает доказанного исправления. При partial/error прежние замечания не объявляются устаревшими.
Human comments/replies не меняются, APPROVE/REQUEST_CHANGES и автоматический resolve не выполняются.
Слишком большой или изменённый вручную original остаётся нетронутым, причина попадает в summary.

Оценка относится к блокирующим корневым дефектам на маленьком размеченном наборе;
она не доказывает точность всех фраз и рекомендаций. Спорные детали и duplicates
нужно рассматривать отдельно, а не скрывать за единым процентом.
