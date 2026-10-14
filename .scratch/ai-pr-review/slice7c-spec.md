# 7c — provenance-verified source and journal reader

- Read-only source-inspection CLI and owner journal/data contract; no decision
  workflow or automatic check activation until application service is complete.
- Original source must be canonical AI workflow ID/path/event/repository/run/attempt,
  successful exact analyze job, strict complete artifact/digest and trusted main code
  ancestry. PR target run.head_sha is PR head; main dispatch/push is base SHA.
- For owner authorization require exact current immutable snapshot/profile/debt;
  read-only historical inspection may show older trusted source without applying it.
- Journal uses canonical owner workflow dispatch/main/root repo/current actor AND
  triggering owner, successful exact authorize job, pinned code ancestry and exact
  run-attempt envelope. Overall apply failure cannot erase authorization; inspect all
  attempts<=20 and<=100 matching-PR attempt proofs, max100 records.
- Workflow run-name PR selector is produced from the same validated canonical PR input
  by the future trusted authorizer; artifact body must match. Query only last7days
  (decision lifetime<=7d), complete bounded Actions collections. Comments ignored.
- Missing/malformed/unproven artifacts fail closed with safe diagnostics. Source
  and decision archive bounds/auth separation come from the tested Actions adapter.
- Repeat matching run/attempt snapshot and observed authorize-job proofs before
  returning; observed changes require retry. Callers re-read before application and
  serialize canonical writers perPR; Actions GET does not provide atomic snapshots.
- Decision expiry cannot exceed official run.created_at+7days. Historical source
  inspection anchors to validated main SHA from GitHub, never arbitrary local HEAD.
