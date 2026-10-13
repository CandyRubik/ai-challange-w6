# Срез 4c — рабочие решения

Base `2cf713608276ff5fe7dfd338c8247e4059ea702e`, branch codex/ai-review-ci.
PR10 merged, CI green,396tests, обе оси subset self-review0open. PR4–10 attached.
Packing отдельно поставлен в4b; reviewed workflow patch применён на свежий main.

## Workflow

- Root ai-review.yml: pull_request_target(main, opened/synchronize/reopened/ready_for_review),
  push main, workflow_dispatch(pr number). Select invalidates head statuses immediately
  (pending), emits numeric PR IDs; main push includes all open PRs against main. Capacity20,
  no silent truncation: overflow errors all selected heads, no paid analysis. max-parallel2.
  Retarget edited включён; title/body edits без API/cloud. Draft deferred до ready или owner
  manual dispatch. Failed-only rerun с отсутствующими current-attempt artifacts инвалидирует
  прежний same-run success и просит Re-run all jobs; newer other run не затрагивается.
- Reusable ai-review-pr.yml at same caller commit; whole workflow concurrency per repo/PR,
  cancel-in-progress false. Three jobs: prepare (contents/PR read,statuses write), analyze
  (contents/PR/statuses read, DeepSeek only), publish(always;contents read,PR/statuses write,
  no DeepSeek key). Root no concurrency group shared with child.
- All checkouts at trusted github.workflow_sha, fetch-depth0,persist-credentials false.
  Actual checkout HEAD and github.workflow_sha must equal captured current API base SHA.
  Stale workflows fail without model calls; main push refreshes policy for open PRs.
  Current main должен быть предком head; иначе rebase до paid analysis.
- Prepare fetches only numeric refs/pull/N/head as Git objects before capture (which pins
  complete diff and expected path/rule/rename checks). Never checkout/install/import/run head.
  Fixed HTTPS GitHub URL, git hooks/global attrs/submodules disabled, timeout; askpass helper
  reads GITHUB_TOKEN from env, never argv or file. Fetched commit must equal captured head.
- Artifacts context-N and result-N are current-run immutable upload/download artifact v4.
  validate env repository/run_id/run_attempt, executing code SHA, pinned base raw-profile hash
  and parsed profile; result equality/hash checks through publishing.preview. No arbitrary
  run-id/name/URL from model. Publisher errors on missing/invalid artifact and invalidates own
  pending/status even when prepare fails. GitHub outage cannot guarantee error write; workflow
  still red, no claims of atomic CAS. Profile already validates all source quotes in base.
- Proposed head profile/source quote referential integrity should also be validated without
  using head policy as normative, so a PR cannot silently break next main configuration.
- Fork/Dependabot: explicit configuration error without paid analysis; owner manual dispatch
  on main allowed. Check both original actor and triggering actor against captured profile
  owners (case-insensitive), so rerun by another writer cannot reuse owner authorization.
- Model fixed deepseek-v4-flash + https://api.deepseek.com, no tool execution. Suggested cap
  96KB actual messages,4000output,4requests/PR run,30files,500corpusfiles,2MB,HTTP60s,job10min.
  No day-level spending cap promised. No hidden SDK/network retries. Document cancellations
  may leave pending if runner/API cannot finalize; rerun is required, never a false success.

## Observed packing issue before workflow

Offline HTTP MockTransport on actual PR9 (06afa6b→3f78168), no real API calls:
- 48KB: required generic bundle56917 even after dropping8optional, zero mock calls.
- 96KB: generic90735, actual DeepSeek messages103101, zero mock calls (wire guard rejects).
- Add optional pure measure_input to model port; packer must consider max(generic,actual)
  while dropping only optional fragments. Extract identical display/schema message builder
  used by DeepSeek.measure_input and real generate. No changes to initial prompt/schema/
  temperature for inputs already fitting, no need for another full paid calibration.
- Check saved v4 and continuation inputs offline to prove unchanged first-call wire layout
  and fitting32KB. Keep original calibration reports; never run another paid eval after pass.
- /tmp/preview_ci_context.py + /tmp/ai-review-ci-context-preview.json reproduce current
  offline measurement. After fix regenerate and save compact evidence in this slice.

## Local verification tools / permissions

- /tmp/ai-review-actionlint/actionlint v1.7.12 downloaded from official rhysd/actionlint;
  tar checksum verified aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f.
- Verified official actions tags (commit refs): checkout v4 11d5960a326750d5838078e36cf38b85af677262;
  setup-python v5 a26af69be951a213d495a4c3e4e4022e16d87065;
  upload-artifact v4 ea165f8d65b6e75b540449e92b4886f43607fa02;
  download-artifact v4 d3f86a106a0bac45b974a628896c90dbdf5c8093.
- User approved merge subsequent map PRs after green CI+self-review. **NO permission yet to
  transfer W5 key to W6/GitHub or recurring paid CI.** Existing last eval authorization consumed
  79/104 calls and successful: no more calibration calls. Ask for specific secret transfer
  and recurring CI limits only after concrete workflow is ready/reviewable; continue unrelated
  local work while pending. Keep key only in memory and encrypted GitHub secret if approved.
- Original user option personal computer as CI is recorded; no self-host runner installed.
  Symbols/text RAG on ordinary runner enough; embeddings/E5 optional future work.

Next: finish4b, then5 inline/idempotence/recovery,6 active debt,7 durable owner decisions,
8 local onboarding skill. Do not stop at a partial pipeline or activate credentials implicitly.
