# 7d0 — reviewable owner presentation

- Offline CLI --markdown previews the effective review and remaining blockers,
  explicitly without CI authority. Original analysis/findings/flags remain intact.
- Pure shared renderers for summary, finding/debt owner notes and one owned journal
  comment show record ID, actor, action, targets/scope/reason/expiry and run links.
- An expired/revoked/out-of-scope decision never appears as an active acceptance;
  incomplete original analysis remains error. Raw original status is distinguished
  from the effective check. No claim that accepted debt was fixed.
- Escape user text/mentions, retain head/current-run metadata outside bounded content;
  oversized journal metadata fails explicitly. No network, authorization or publication.
- Application service and trusted workflow reuse these pure renderers in next cuts.
