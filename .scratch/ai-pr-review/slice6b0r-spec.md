# 6b0r — pure artifact module

- Move RunContext/ReviewArtifact, digest and workflow_url to contracts; move
  make_artifact/preview to artifacts. Definitions and data semantics stay unchanged.
- Publishing keeps its old public imports as aliases and retains network effects,
  snapshot/generation guards, summary/inline transport. Existing callers need no edits.
- No change to JSON shape, digest, validators, status, policy or model protocol/budgets.
- Debt gate is not activated here. PR23 remains draft after a legitimate inconclusive
  verdict: required code filled the budget and the model lacked expiry dependency.
- This seam separates pure artifact checks from GitHub effects and reduces mandatory
  context for follow-up gate/owner-policy changes. Test the existing public interface;
  compare moved AST definitions and use offline context measurement, no paid eval.
