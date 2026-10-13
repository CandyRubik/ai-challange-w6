# 5b — offline inline anchors

Baseb65b311; PR14, head17cf631, merge6591d02.

- Standards0open, Spec0open. Обнаружен и исправлен P2: SequenceMatcher на повторяющемся хвосте мог считать неизменённые строки заменой. Actual changed-line set теперь берётся из pinned Git diff с отключёнными extdiff/textconv.
- 15 anchor/CLI tests и73 semantic/publishing/recovery tests прошли; GitHub CI и AI Review run38465236669 success.
- 310 reviewable lines; actual provider95887bytes<96000, без реальныхAPI на замер.
- Primary evidence, blobSHA, Git diff и GitHubpatch нужны вместе. RIGHT/LEFT, rename/delete, metadata/caller/truncated/stale fallback, grouping иcap10 проверены. `--inline-plan` offline и несовместим с`--publish`.
