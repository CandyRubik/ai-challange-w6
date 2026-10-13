# 6b0 — trusted debt context

- Capture base debt registry and exact raw hash in RunContext. check_context rereads
  pinned base and rejects either registry or hash substitution before analysis.
- A missing registry uses an explicit empty model/hash for existing repositories.
- Preview validates the digest of original result JSON, preserving historical
  artifacts when new optional model fields get defaults. Strict schema, identity,
  policy, criteria and evidence checks are unchanged.
- This slice is context/provenance only. CI project_debt stays opt-out; validation
  of proposed registry, active debt gate, expiry write guards are6b1. Separate
  project comment is6b2. The current committed registry remains empty.
- Shared committed-Git/GitHub fixture supports these context contracts and upcoming
  gate tests. No model key, network or project code execution in tests.
