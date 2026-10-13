# 5e1 — bounded response failure diagnostics

Base `6394b78`; Standards 0 open, Spec 0 open на `056fbc9`.

- PR №19: run `38477604709`, attempts1/2, оба error/partial после 2 calls;
  wire94551/94887 bytes, token budget не исчерпан. Raw ответы не сохранялись,
  точная причина прежних ошибок неизвестна. Новые коды не дают ретроспективного доказательства.
- 109 reviewable lines; 26 focused tests прошли. Sentinel в model values,
  unknown fragment IDs и unknown JSON field names не попадает в trace.
- Закрыты два P2 self-review: post-ground/core schema validation тоже получает
  code на обеих попытках; null message при stop отличается от length completion.
- Санитизация общая для provider и generic core path. Максимум первые10 validation
  errors; только фиксированные коды и allowlisted type/field names. Raw values нет.
- Prompt, output schema, retries, verdict и бюджеты не изменены. Offline baseline
  wire86896 bytes, все обязательные/optional источники помещаются, realAPI0 на замер.
