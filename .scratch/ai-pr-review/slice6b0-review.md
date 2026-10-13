# 6b0 — trusted debt context

Base26366e1; Standards0open, Spec0open на91d7b44.

- 85 reviewable lines;55 focused checks прошли. Четыре новые проверки охватывают
  registry/hash substitution, invalid owner и legacy defaults/raw digest.
- Capture читает registry/hash только из base; check_context сверяет их с Git.
  Invalid trusted configuration отклоняется до модели. Proposed head не принимается.
- Digest проверяется по исходному result JSON до новых optional defaults. Strict
  schema, snapshot, profile, criteria и evidence validation сохранены; extra field
  с пересчитанным digest тоже отвергнут независимым Spec review.
- CI debt analysis остаётся opt-out. Общий test fixture используется здесь и6b1.
- Baseline wire91135bytes, optional2 сняты, обязательные сохранены; realAPI0 на замер.
- Полный gate сохранён e2b45fe и /tmp/ai-review-debt-gate-final-files.json. Обе оси
  подтвердили закрытие expiry race: guard до/после writes и invalidateownstatus.
