# 5c — recoverable inline publication

Base0110f36; Standards0open, Spec0open на436d9c2 после проверки переноса с306c4fa.

- 20 writer cases прошли. Ранее83focused legacy/anchor checks прошли; полная проверка финального head выполняется в CI.
- Закрыты P2 self-review: error/complete retirement, сохранение длинного original после mentions normalization, дубли при partial visibility/изменении плана и потеря уже увиденной identity при пустом следующем readback.
- Batchv2 хранит sent keys и full-plan keys/hash. `observed` сохраняет неопределённость в течение run; она не заменяет текущий COMMENTED+comment proof. Новый POST разрешён лишь для ранее не объявленных anchors после reconciliation.
- Bot ownership, head/anchor, review membership, exact new-POST bodies; PENDING/error, force-push, human/reply/foreign comments, large/edited originals и rerun проверены.
- 434 reviewable lines; actual provider92843bytes, optional3 сняты, обязательные окна сохранены, realAPI0 на замер. CIлимит96000/4000/4не менялся.
- GitHub CAS отсутствует: per-PR serialization и guards уменьшают гонку, не обещают атомарной записи относительно внешних изменений. Старые comments сохраняют SHA/оригинал; API failure fail closed.

Новая write-точка только trusted Actions publisher с Bot token; локальный CLI предоставляет offline inline-plan. Неприменимый к обычному personal token флаг локальной inline-публикации исключён из среза.

После переноса95focused checks прошли; после HTML framing20writer cases прошли. Metadata скрыто в HTML comment; parser проверяет wrapper и прежние identities.
