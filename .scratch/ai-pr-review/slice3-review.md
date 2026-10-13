# Срез 3 — verification и инструменты калибровки

Base: `dad9f1749aeeb3d4a0afc152d34501d918c6d364`.

## Standards

Первый проход: 2 P2. Eval штрафовал правильную before-цитату удалённого guard;
verification отвергал related-head caller для metadata-finding. Исправлено сопоставлением
primary/supporting evidence и gold alternatives; тип causal anchor больше не ограничивает
related-head code. Добавлены регрессионные тесты.

## Spec

Первый проход: 2 P2. Старый before guard мог оправдать его удаление из head;
метрики не учитывали supporting evidence. Исправлено требованием текущего head proof
для modified/renamed; before-only допускается для удаления файла. Статические checks
модель не переопределяет. Ошибки/неопределённость не дают success.

## Проверки

- Red → green для verification, eval counts, old-before false success и empty-module caller.
- Focused: 38 passed; полный suite после исправлений: **325 passed**; compileall и node syntax прошли.
- Targeted Standards rereview: **0 open**, обе reproductions закрыты.
- Реальный eval: 20 случаев, 27 calls, 85 507 total tokens, 110.375 s суммарно;
  clean_blocked=0, incomplete=11. После исправления только метрик offline:
  TP4/FP3/FN6; precision 4/7, recall 4/10. Дубликаты BUGS и must считаются шумом (FP).
- [Исходный результат](calibration-run.json) относится к загруженному review-v2 до
  self-review исправлений; [пересчитанные counts](calibration-counts.json) не являются новым API-прогоном.
- Quality gate **не пройден**. Автоматический облачный workflow пока не включается.

Бюджет кода: 494 reviewable lines; большие входные fixtures и результаты eval исключены
как data. Следующий самостоятельный срез 3b улучшает формат structured output и проверяет
качество до автоматических публикаций.
