# 6b1 — CI debt gate review

- Source `1b6e4dc675063c2c8edd4528f1fb21fddf7f0e8c` against RAG prerequisite `92c8d864fc4bf00146f3e99d98140a8d76cfb136`; rebased source `058905658f98a9b52c478fcef46039bf4c9e7bef` on merged main `b372fc9f1f8bfb28dc108b59dcef1022e6385da3`, same source blobs.
- Standards0open; Spec0open. The earlier gate's expiry guards survive extraction of pure artifacts. `ci/debt_publishing` source and validation/publication functions preserve reviewed behavior.
- 76 combined debt/context/CI/publication/recovery checks pass, including forged fixed, missing/duplicate coverage, active/fixed/incomplete lifecycle, no-model error, expiry during summary and success status writes.
- Reviewable code181lines. Offline trusted packing95075/96000 bytes; data-only probe includes full expiry helper head lines1–22. Real API calls0 in probes.
- Main remains empty registry. General project comment follows in6b2; current gate uses separate summary assessments and never old-debt inline.
