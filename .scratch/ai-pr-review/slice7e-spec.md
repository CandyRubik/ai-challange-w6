# 7e — zero-model expiry sweep

- Five-minute best-effort Actions schedule or explicit manual dispatch, bounded20PR.
  No model secret/calls, same perPR serialization and trustedmain/data-onlyfetch.
- Validate latest green status against canonical successful publishing job and pinned
  artifact/snapshot. Distinguish normal AI and owner application workflows.
- Reconstruct original effective success at publication and re-authenticate current
  journal; if a required acceptance expired/disappeared, set error until regular rerun.
- Guard current PR/base/generation and exact latest status before invalidation. Stale
  workers never replace a newer verdict. Unprovable owned success fails closed.
- Plain success or still-valid acceptance stays unchanged. Queue overflow fails closed.
  Schedule delays remain possible; statuses themselves have no TTL or instant guarantee.
