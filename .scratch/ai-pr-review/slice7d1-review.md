# 7d1 — source reuse review

- Base9fb41d5058115510b106c8e45a1d0431138c45c1; reviewed runtime4674f08, test-only7473121. Final delta passes source inputs explicitly through workflow_call.
- Standards0open; Spec0open;11reuse tests and79CI/publishing/journal tests passed. Source read occurs only after owner validation; changed/missing source fails before model construction.
- Offline wire92821/96000, no real API calls; complete original data unchanged, actual debt rechecked, flattened original usage retained.
- 194reviewable lines; manual source selection only. Automatic owner cache/application remainsnext.
