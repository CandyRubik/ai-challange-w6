# 6b2a — common owned-comment upsert

- Generalize existing recoverable summary upsert for a caller-provided stable marker.
- Preserve old summary entrypoint and uncertain-summary subclass used by CI finalizer.
- Generic uncertain comment POST permits independent error summary; no blind retry.
- Same owned-bot marker selection, before_write guard and three readback attempts.
- No general project debt comment yet. This is a standalone prerequisite to keep
  the full next slice within96KB; no changes to gate, policy, budget or AI protocol.
