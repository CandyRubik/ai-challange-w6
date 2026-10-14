# Interview frontier

Use existing answers first. Ask only decisions the repository cannot establish.

- Ownership and data: who may authorize rules/exceptions; may selected code/docs leave
  CI for the chosen API? Recommend existing approved owners/provider and limits.
- Architecture: responsibility and dependency boundaries; which current patterns are
  intended, legacy or exceptions? Ask for a valid alternative to each proposed ban.
- Forbidden constructs: exact scope and reachable failure example. Prefer a deterministic
  Python import-boundary check where applicable; mark language/checker gaps explicitly.
- Best practices: concrete criteria and evidence instead of taste or a single score.
  Recommend advisory defaults until the owner confirms a mandatory requirement.
- Invariants: state transitions, approvals, atomicity, isolation, retries and stale writes
  where the project actually has them. Assess introduced/worsened impact; untouched
  mechanisms pass. Existing debt blocks only through confirmed active registry entries.
- Workflow: diff/base/head evidence, report/inline/check, incomplete behavior, forks,
  quotas, source freshness, exception reason/scope/expiry and revocation. Explain that
  private Free checks currently do not guarantee merge prevention and schedules have delays.
- Counterexamples: a clean change that looks suspicious, an old defect, a rename/deletion,
  a missing caller, an unavailable provider and conflicting norms. Resolve decisions
  explicitly; do not turn a model hypothesis or existing implementation into a new must.

Record each answer next to its recommendation, evidence and unresolved alternatives.
Use `.ai-review/profile.json` as the schema example; helpers validate it with `Profile`.
