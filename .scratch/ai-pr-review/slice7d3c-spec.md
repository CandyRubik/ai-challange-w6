# 7d3c — trusted owner dispatcher

- Main-only explicit dispatch with8structured inputs, canonical PR number and same
  perPR concurrency as normal review. Both official actor/trigger must be owners.
- Validate canonical own run and current snapshot; non-revoke requires canonical complete
  source analyze attempt from last7days. All expiry<=official owner run creation+7days.
- Persist envelope only after full proposal validation; mark pending then upload. Exact
  successful Authorize job remains authority even if apply or later rerun fails.
- Apply reads authenticated current journal, uses complete same-snapshot source and
  owner publisher with no model/secret. Original data remains unchanged in artifact.
- Revoke needs authenticated prior ID/reason, no source/expiry. If no current analysis,
  preserve decision journal and produce error until fresh review; never waive partial.
