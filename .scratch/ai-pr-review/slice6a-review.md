# 6a — local confirmed project debt

Base `12bd84b`; Standards 0 open, Spec 0 open на `b4868b1` до механического rebase.
Все7 source blobs совпадают с ранее проверенными `62c2beb`/`ae4b67e`.

- 422 reviewable lines. 231 reviewer checks прошли при первой подготовке; после
  symlink fix28 debt checks; после новых prerequisites42 combined debt/spans/diagnostic
  checks. Отдельно Spec подтвердил success/failure seam с fake semantic model.
- Active debt блокирует unrelated doc change отдельно от PR findings. Candidate,
  owner-resolved и действующий UTC waiver не блокируют; expired waiver проверяется.
- Full rule scope на pinned head проверяется до500 файлов / 2МБ / 30секунд.
  Moving imports, rename, dynamic/syntax/binary errors, historical evidence,
  owner/reason/version и proposed registry проверены.
- Закрыт P2: directory symlink/gitlink мог скрывать scoped descendants. Mode/kind
  проверяются до glob без разыменования; посторонняя ссылка не мешает фиксировать fix.
- `review_debt.py` предлагает candidates или проверяет local audit/debt. Реестр пуст;
  на текущем проекте candidates пусты, ничего не активировалось.
- PR19 до prerequisites: два error/partial run после2calls каждый, raw не сохранён;
  точная причина неизвестна. После исправлений baseline mock wire91201bytes,
  все mandatory сохранены, optional8 сняты. Это не свежий paid quality eval.
- Автоматическое включение долга и отдельный GitHub comment остаются в6b.
