# Срез 4b — transport-aware input budget

Base: `6d7e66b04ffb205cd337324604a3f80b8599ced6`.

Самостоятельный срез выделен из reviewed CI diff до открытия PR:4c с workflow остаётся
следующим PR. Только81 reviewable code line, документация/запись offline measurement исключены.

## Standards

В полном diff обе оси проверили packing/helper/guards; Standards **0 open**, smell calls0.
Wire builder извлечён без изменения вывода; provider preflight и реальный запрос используют
одну функцию. Generic model не обязан поддерживать новый optional measurement port.

## Spec

Spec **0 open** в полном reviewed diff. Обязательный code/normative context не обрезается;
снимается только optional retrieval. >maxItems критериев отклоняются до paid generation.
Новое поведение имеет самостоятельную ценность в локальном CLI и не активирует CI/cloud.

## Evidence

- 409 full tests прошли в исходном объединённом рабочем diff; последующие schema-capacity
  checks прошли отдельно (2focused). Workflow-regressions сохранены в следующем срезе.
- Реальный PR9: internal90735/actual103101 прежде не помещался в96КБ. Теперь сняты2optional
  chunks, actual94949bytes; обязательные источники сохранены. Нет real API calls.
- Все42 сохранённых wire inputs последней калибровки побайтно идентичны; max27018bytes.
  Успешный eval не повторялся, бюджет79/104 не изменился; ключи не читались/переносились.
- Compileall/node/diff-check прошли. Current-slice CI проверит полный набор396 tests.

Измерение mock не проверяет качество заключений. Cached calibration применима к тем же
fitting inputs: template/schema/параметры/bytes не менялись. Подключение workflow и actual
smoke остаются4c, inline5, debt6, owner7, onboarding8.
