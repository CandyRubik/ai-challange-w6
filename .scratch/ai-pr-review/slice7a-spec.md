# 7a — offline scoped owner policy

- Pure proposal/effects module and offline CLI; it grants no CI authority and performs
  no GitHub/model calls. Existing CI/publishing remains unchanged until authenticated journal.
- Original findings/blocking flags/policy/result remain unchanged. Effective status is
  separate; incomplete/failed/error and unexplained failure can never be overridden.
- Dismiss exact full finding fingerprint on exact base/head/profile; rule waiver only
  known non-BUGS rule with explicit repo-relative paths and <=7day UTC expiry.
- Debt waiver binds exact confirmed open entry; manual closure also requires matching
  proposed resolved entry, owner/reason and immutable fields, same snapshot.
- Explicit revocation binds an existing decision record, no revocation-of-revocation.
  Current owner, repository/PR, profile, scope and time govern applicability.
- Target/scope/reason/count bounds, deterministic fingerprints, owner explanation,
  expiry and independent remaining blockers are visible in preview. All decisions are
  untrusted data until future workflow authenticates origin and journal replay.
