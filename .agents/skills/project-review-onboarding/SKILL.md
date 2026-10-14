---
name: project-review-onboarding
description: Configure project-specific AI PR review by interviewing its engineer, drafting evidence-backed rules, and previewing the review prompt. Use for a new project or changes to its review policy.
---

Produce a reviewable project profile and offline prompt template. Reuse decisions
already approved in this conversation or project; interview only the remaining gaps.

1. Locate [the helper](../../../scripts/onboard_review.py) in the reviewer checkout and
   use its Python environment. Run the helper by absolute path with the target repo as
   cwd; commands below use the path relative to this reviewer repository. Read any
   existing `.ai-review/profile.json`, standards and current approval records. Run
   `python scripts/onboard_review.py facts --revision HEAD`.
   Facts come from bounded committed Git data, not execution. Read the architecture
   docs behind the fact paths. Distinguish observed code, inferred conventions and
   requirements explicitly confirmed by the engineer. Existing patterns are examples,
   not automatically mandatory norms.
2. Use the available interview flow: when the user explicitly invokes ask-matt, read
   its router and choose an installed interview flow. Prefer grilling for question
   rounds; if absent, use the same frontier mechanics directly. Ask prerequisite-free
   questions together (up to3), with a recommended answer grounded in project facts.
   Wait for answers before dependent questions; find code facts yourself. Do not
   install skills or contact other people as part of onboarding.
3. Cover each branch in [interview.md](references/interview.md). Recommend the current
   supported stack before proposing replacements. For every candidate rule record
   requirement, rationale, scope, normative source/quote, allowed example and realistic
   counterexample; resolve conflicts and accepted alternatives with the engineer.
   Use should for recommendations, must for explicitly confirmed blocking rules.
4. Save the answer record and schema-valid draft profile under `.ai-review/drafts/`.
   Compile `python scripts/onboard_review.py prompt --revision HEAD --profile DRAFT`.
   Review architecture/forbidden/best_practice/invariant coverage, source warnings and
   an example clean/violating diff. This is a policy/schema template: runtime adds PR
   snapshots, criteria, code/docs fragments and candidates. It sends no API request.
5. Present the draft, disagreements, scope/budget limits and compiled template for
   engineer approval. Apply new canonical rules only after that approval; previously
   approved unchanged policy needs no new interview or approval. The helper writes
   drafts only. After approval, commit normative docs/profile through normal PR review;
   CI continues to use trusted base policy, never an unapproved head draft.

Done: facts and answers are traceable, every proposed norm has an owner/source/example,
the deterministic template is reviewable, and adoption status is explicit. Unsupported
paths/languages/static checks remain visible; adapt the reviewer allowlist/checker
before claiming CI support. See `docs/ai-review.md` for runtime limits and authority.
