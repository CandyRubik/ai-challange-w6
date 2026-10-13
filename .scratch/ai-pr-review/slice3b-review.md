# Срез 3b — формат генерации и provenance

Base: `472eaf977b0dee49cc9a06f27858aea6e7a1e37a`.

## Standards

Закрыты P2 с ложным output-budget превышением из-за локально развёрнутых цитат и с
необоснованным объединением независимых FP по opcode. Grounding передаёт диапазоны,
raw-byte guard проверяет фактический ответ. Метрики используют exact Evidence либо
однострочную проверяемую before/head пару; общий opcode остаётся только группировкой
представления с отдельными evidence и всеми JSON findings. Итог: **0 open**.

## Spec

Закрыты P2: прежняя сигнатура JsonModel, unknown/mixed transport counts как 0 и
независимые диагнозы одного opcode. Optional bounded capability сохраняет исходный
интерфейс; unknown usage остаётся null. Разрешение диапазона проверяет одинаковые
path/SHA/role/kind и наличие переданных строк. Deletion-gap связывается с реальными
удалёнными строками, header-only/blank/cross-revision не принимаются. Итог: **0 open**.

## Проверки и калибровка

- **343 passed**, compileall, node syntax и diff-check прошли.
- 26 случаев; старые 20 — development, 6 свежих semantic holdout. Версия 3 разметки
  отдельно учитывает явно размеченные повторы BUGS, raw artifacts не переписываются.
- [Eval v2](calibration-run-v2.json): 38 requests, 11/13 регрессий, 3 incomplete, 0 clean blocked.
- [Eval v3](calibration-run-v3.json): 37 requests; direction/format cases ещё не пройдены.
- [Eval v4](calibration-run-v4.json): 36 requests, 10/13 validated findings, 3 incomplete,
  13/13 clean pass, 2 automatic FP (повторный диагноз требует точной разметки), 2 duplicates.
- В v4 невалидный JSON в двух случаях и blank citation в одном корректно дают error.
  **Quality gate не пройден; автоматический workflow пока не включается.**
- Последние два полных прогона использовали 73 из одобренных 104 запросов. Ключ не
  сохранялся и не переносился. Любая дальнейшая обработка ограничена оставшимся бюджетом
  и теми же 26 fixtures; нового полного прогона не планируется.

630 reviewable code lines: на 30 выше цели, ниже cap1000. Дополнение — проверки обнаруженных
false-success/false-incomplete и typed provenance для диапазонов/удалений. Их отделение
оставило бы известные ошибки grounding в уже поставляемом CLI. Следующий самостоятельный
срез 3c — bounded format retry и точная adjudication результатов eval до активации CI.
