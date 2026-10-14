# 6b0s — direct imported function context

- Inspect module-level Python ImportFrom aliases and calls in affected functions;
  changed imports may select existing callers. Never import or execute project code.
- Resolve only existing unambiguous repository module/package paths. Shadowed names,
  stars, unsupported syntax and unresolved paths fall back to existing lexical RAG.
- Read optional definitions through the existing allowlist/corpus scan and budgets.
  Functions/decorators are complete; needed imports or a cheap exact prefix/small module
  preserve their context. Source path/SHA/line/text remain pinned and exact.
- Rank changed runtime calls before unchanged calls and test helpers; within each
  rank prefer smaller function bodies. Bound by the same8optional fragments. Remove
  only fully covered duplicates; preserve partially overlapping lexical windows.
  Packing may drop optional fragments; mandatory sources, policy and limits unchanged.
- Tests: aliases/relative/ambiguous/shadowed/star/unaffected imports, full decorators,
  no execution and no read outside allowlist. Offline paired26same-snapshot capture
  preserves previous wire inputs; dummy outputs do not measure model quality.
- Prove expiry definition fits the pending gate before another paid CI execution.
