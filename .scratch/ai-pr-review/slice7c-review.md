# 7c — source/journal provenance review

- Source49cb0a03bebfe4a244fc87f3483ff196568dd0bd on maind47f15e74a7290df60ea1f87265abcddabbb7c06.
- Standards0open; Spec0open. Historical inspection now anchors to GitHub main instead of local HEAD. Reader fences run/attempt snapshot and job proofs; observed concurrent authorization requires retry. Expiry stays inside official run-created seven-day query window.
-28focused tests pass;85combined policy/Actions/journal tests passed before last four fence/anchor cases. Covers complete source/job/profile proof, actor+trigger, same snapshot, prior auth after failedrerun/apply, malformed/copied metadata and bounds.
- Live read-only source smoke verified canonical PR27 run38491021152 with differing PR/code SHAs, complete/success, actual profileGit proof; modelcalls0.
-411reviewable code lines. CLI only inspects source; actualownerworkflow/check activation follows application service, avoiding an unfinished action in main.
