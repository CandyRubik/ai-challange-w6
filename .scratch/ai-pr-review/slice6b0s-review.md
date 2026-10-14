# 6b0s — imported-function context review

- Source reviewed: `767cb859903687cc759200b273678e11f33a76b1` against main `1ee0553349c8aeb5613713b9c51424b2081d54d6`.
- Standards: 0 open; Spec: 0 open. Fixed import/export rebinding (including stars, exception/pattern bindings and deletion), and imports needed by definition-scope decorators/defaults/annotations.
- 21 dependency regressions pass; 51 combined dependency/semantic checks passed before the final two deletion regressions.
- Reviewable code: 253 added+removed lines; unchanged limits/allowlist/protocol. No imported project code is executed.
- Paired same-snapshot offline capture: all26 calibration scenarios retain identical wire messages. Dummy outputs do not measure model quality; real API calls:0.
- Trusted-base packing of this PR:94093 message bytes, limit96000, optional omitted0.
- Data-only pending-gate probe on this source:95010 bytes, optional omitted7; `reviewer/debt.py` head lines1–22 include `expiry`. Real API calls:0. Format retry overhead also fits.

CI/model results are recorded after the actual workflow; offline capture does not claim a complete semantic verdict.
