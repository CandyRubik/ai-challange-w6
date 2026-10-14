# 6b2 — one project-level debt comment

- Publish/update one owned bot comment with a separate stable debt marker. It shows
  pinned head, registry, verdicts/evidence, owner/reason and workflow link; no old-debt inline.
- Reuse shared debt formatting in summary and general comment. Empty registry does
  not create noise; an existing own comment is updated after completed removal.
- Incomplete/error review retains the previous completed assessment with original
  head/run and explicit current incomplete banner; reruns do not nest banners.
- Bot ownership plus marker only; human comments/replies/foreign markers untouched.
- Share bounded upsert/recovery with summary, preserving the legacy summary API.
  Unknown POST has three readback attempts, no blind POST retry. A generic unknown
  debt POST still permits the CI error summary; summary unknown remains status-only.
- Same snapshot/generation and debt lifecycle guards around all writes, including
  force push and expiry during general-comment publication. No success on failure.
- Unit contracts use committed temporary Git and mocked GitHub, no cloud calls.

- Large complete details are capped once at54KB, reserving metadata/banner space.
  Head/registry/original run link remain outside truncation. A retained oversized
  legacy block fails before PATCH and remains untouched, with an error check.
