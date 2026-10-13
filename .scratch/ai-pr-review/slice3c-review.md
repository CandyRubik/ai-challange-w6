# Срез 3c — bounded recovery и завершение калибровки

Base: `c8a044422d5e3bb0695f2f214762ed96881eb6f2`.

## Standards

Закрыты два P2: некорректные HTTP200 envelopes давали необработанный TypeError/
AttributeError, legacy v4 не содержал полей новой adjudication. Envelope теперь
валидируется и допускает один bounded format retry; старый отчёт явно подготавливается
по совпадающему corpus, исходные counts сохраняются. Итог независимой проверки: **0 open**.

## Spec

Закрыты три P2: invalid JSON generic JsonModel не повторялся, repair instruction могла
превысить его context budget, BUGS допускался как нормативный gold для annotations.
Валидация включена в bounded helper, каждый prompt заново измеряется, аннотация требует
уже найденного нормативного правила. Итог независимой проверки: **0 open**.

Обе оси отдельно проверили точные annotations INV-ATOMIC/INV-SESSION по сохранённым
исходникам и диагнозам. Это повторы корневого дефекта; непроверенная фраза BUGS о внешней
транзакции не использована как доказательство. Аннотации не участвуют в продуктовой policy.

## Проверки

- Полный локальный набор: **360 passed**, compileall, node syntax, diff-check.
- После уточнения unknown transport metadata: **10 focused passed**; неизвестные поля
  дают null API calls, а не ноль или crash.
- [Исходный v4](calibration-run-v4.json) и [подготовленный отчёт](calibration-v4-prepared.json)
  сохранены. 23 complete случая повторно не вызывали API; только три incomplete завершены.
- [Raw aggregate](calibration-v4-resumed-raw.json): TP13/FP2/FN0/incomplete0.
- [Итог с exact annotations](calibration-final.json): TP13/FP0/FN0/incomplete0,
  13/13 clean pass, 0 clean blocked, 5 duplicates. Quality gate пройден.
- Продолжение: 6 requests, 32КБ/2000 tokens каждый. Последние два полных прогона вместе
  с продолжением использовали **79/104** разрешённых запросов. Новых полных прогонов нет;
  после успеха дополнительные запросы не выполняются. Ключ только в памяти процесса.

Завершённые случаи повторно использованы потому, что initial prompt/schema/transport
параметры не менялись; новый протокол применяется только при structural retry. В отчётах
сохранены source/code/context hashes, prior attempts, старый и новый расход.
Результат относится к блокирующим корневым дефектам на 26 случаях; holdout уже просмотрен,
точность всех advisory-фраз и промышленная precision этим не доказаны.
