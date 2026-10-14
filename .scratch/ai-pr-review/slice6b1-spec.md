# 6b1 — trusted project debt in CI

- Enable project_debt for normal CI review, including the no-model error path.
- Validate proposed head registry/profile integrity, without adopting head policy.
- Pure artifact preview requires exact trusted registry hash/entry coverage, flags,
  owner, lifecycle/UTC expiry, scope and evidence. Incomplete debt is error; active
  blocking debt is failure, independently of newly introduced PR findings.
- Publisher recomputes debt from actual pinned Git objects before any write.
  Nonempty registry requires Repository; forged fixed/empty scans never pass.
- Recheck snapshot/generation and debt lifecycle/expiry around writes, including
  summary/status/inline. Expiry during publication invalidates the owned status.
- Separate debt assessments in summary; no old-debt inline and no general debt
  comment yet (6b2). Current base registry remains empty; synthetic active-debt
  contracts are exercised in temporary committed Git fixtures, with mock transport.
- Preserve all original validation, wire limits, API-call caps and trusted-base code.
