# 7d3a — owner context and replay module

- Typed context captures bounded owner records/hash and manual/owner source origin.
- Module captures authenticated journal, selects latest same-base/head/profile source,
  checks record IDs/digest/origin and re-authenticates current journal before reuse.
- Manual explicit source wins; fresh manual input skips automatic cache. Missing or
  changed source never triggers paid fallback; original result is preserved.
- Trusted base without canonical owner workflow rejects forged authority and preserves
  previous capture behavior. CI wiring and dispatch are separate next slices; no
  intermediate owner action is exposed.
