# MVP acceptance

This document distinguishes implemented behavior from remaining review gaps. Feature PRs are not automatically merged.

| Scope | Automated coverage | Manual / integration follow-up |
| --- | --- | --- |
| No email | Disabled routes/tools and planning regression | Keep standalone send scripts out of demo |
| Intake (#8) | Candidate fields persist | Max-count and invalid mode/time boundary cases should be expanded |
| Photos (#10) | Count/size/corruption limits, no key, actual multimodal message with fake model, edited inventory math | Live image quality depends on selected model; visual mobile QA remains |
| Warp (#7) | MockTransport success and typed failure responses | See data-label and pallet assumptions below |
| Homes (#9) | Existing planning regression | See routing and incomplete display below |
| Evals | All six cases, eight assertions each; no real quote HTTP | Scripted 100% is not evidence of live-model quality |

## Known gaps in other feature PRs

- PR #7 currently labels sandbox responses as firm/live quotes. Correct this before using a sandbox key in a user-facing demo. The sample payloads in tests are synthetic, not captured live quotes.
- Warp's pallet conversion assumes 60 cubic feet per pallet and fixed 48 × 40 × 48-inch dimensions. This does not establish that a real household shipment fits or that residential access is included.
- LTL quote display/integration with planner service selection needs end-to-end verification; receiving a Quote alone does not prove it is a selectable move option.
- PR #9 indexes an empty routes list without a guard. Empty provider results can fail the plan request rather than show unavailable.
- Preferred departure time is collected but not sent to Google Routes. Returned timestamps are not displayed, and the screen has no school-unavailable row or commute ranking yet.
- Candidate comparisons currently show independent cards. Do not describe this as a combined school/commute ranking.

## Review order

Merge intake (#8), then retarget photo (#10) and homes (#9) to master. Warp (#7) and the fixtures/docs PR independently target master. Resolve the above issues in their owning feature PRs. Never auto-merge.

Run pytest and scripted evals on each branch and on a local integration worktree containing all proposed changes. No credentials are required. The original mixed worktree and local .env are not publishing sources.
