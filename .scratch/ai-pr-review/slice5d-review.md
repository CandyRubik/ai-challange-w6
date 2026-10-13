# 5d — causal ranges from Git

Base `4960646`; Standards 0 open, Spec 0 open на `2345ba5`.

- Воспроизведена ложная блокировка: вставка одной строки в 300 одинаковых `pass`
  заставляла SequenceMatcher считать неизменённый хвост новой ошибкой. Теперь
  Git Myers определяет интервалы, ошибочная citation отвергается до verifier.
- LF/CRLF поддерживаются. Одиночный CR и разделители Python splitlines, отличные
  от Git LF, явно дают error до модели и статических findings: координаты не угадываются.
- 67 focused checks прошли; отдельно проверены пустые файлы, insert/delete/replace,
  кириллица, CRLF, timeout и отсутствие исходника в argv/сообщении ошибки.
- Offline replay 42 сохранённых model calls: 41 wire идентичен; все 42 grounded
  outcomes идентичны. Единственное отличие — допустимый выбор перемещённой строки
  в чистой перестановке state/memory save; finding отсутствует в обеих версиях.
  Реальных API calls в replay нет. Это проверка совместимости, не свежая калибровка.
- 118 reviewable lines. CI budget измеряется baseline-кодом из `4960646`, с mock
  transport и неизменёнными лимитами 96000 bytes / 4000 tokens / 4 calls.
