# 5e2 — contiguous mandatory source spans

Base `ea656db`; Standards 0 open, Spec 0 open на `d3ab043`.

- Offline repro: change61 в90-line module даёт windows1..60/61..90. Citation59..62
  ранее отклонялась при полностью переданных строках; теперь grounded корректно.
- Только соседние mandatory code windows одного вызова, точные bytes/path/SHA/role.
  Gaps не заполняются, normative и optional ranking не меняются. Обработка линейна.
- 56 focused checks прошли, затем5 final cases; Spec дополнительно проверил150
  комбинаций LF/CRLF/EOF/spans. Full functions и decorators остаются в контексте.
- Изолированный replay объединения:38/42 wire идентичны; в4 больших memory examples
  exact source identity remapping сохранённых citations даёт те же42 normalized
  grounded outcomes. Это не новые ответы модели и не свежая проверка precision.
  Original calibration/raw counts не переписывались; paid calls0.
- Baseline wire79364bytes; обязательные и optional источники помещаются целиком.
  Контекст96КБ/output4000/requests4, output schema и prompt сохранены.
