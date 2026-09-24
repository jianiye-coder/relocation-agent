# MVP acceptance

This document distinguishes implemented behavior from remaining review gaps. Feature PRs are not automatically merged.

| Scope | Automated coverage | Manual / integration follow-up |
| --- | --- | --- |
| No email | Disabled routes/tools and planning regression; nothing is delivered even with credentials set | Keep standalone send scripts out of demo |
| Intake (#8) | Candidate fields persist; readable errors for 3+ homes, unknown commute mode and malformed times (`8:30am`, `25:00`); blank lines ignored | — |
| Photos (#10) | Count/size/corruption limits, no key, actual multimodal message with fake model, edited inventory math | Live image quality depends on selected model; visual mobile QA remains |
| Warp (#7) | MockTransport success and typed failures; sandbox (`wak_test…`) quotes labeled as mock sample data (confidence 0.2); production quotes firm but capped at 0.6 with assumptions in `price_basis` | Confirm with Warp that household goods and residential pickup/delivery are accepted |
| Homes (#9) | Missing key, response normalization, empty routes (`{}` and `[]`), HTTP errors, departure time sent as `departureTime` (plus `TRAFFIC_AWARE` for driving), past times omitted, explicit school/internet/electricity unavailable states, card shows departure and checked time | Combined ranking of the two homes |
| Evals | All six cases; no real quote HTTP | Scripted results are not evidence of live-model quality |

## Fixed in the feature PRs

- **#7:** sandbox responses were labeled as live firm quotes at 0.95 confidence. Sandbox quotes are now sample data, titled "(mock data)"; production quotes stay firm at 0.6 confidence and state the pallet assumptions (60 cu ft per 48 × 40 × 48 in pallet; household goods and residential access not confirmed).
- **#8:** too many homes, an unknown mode or a malformed departure time showed raw validator text. They now show plain-language messages, with boundary tests.
- **#9:** an empty routes list raised `IndexError` and failed the plan request; it now shows the commute as unavailable.
- **#9:** the departure time was collected but not sent to Google Routes; returned timestamps weren't shown; there was no school row. All three are done.

## Still open

- Warp's pallet conversion is an assumption, disclosed in every quote. It does not establish that a real household shipment fits, or that residential access is included, until Warp confirms.
- LTL quote display and planner service selection need end-to-end verification: receiving a `Quote` alone does not prove it is a selectable move option.
- Candidate homes are shown as separate cards. Do not describe this as a combined school/commute ranking; schools stay unavailable until a verified official source is added.
- Photo inventory still needs a visual check on a phone and a run with a live visual model.

## Review order

1. Merge this PR (#11) first, so CI (pytest + scripted evals, no credentials) runs on every later PR.
2. Merge intake (#8).
3. Retarget photo (#10) and homes (#9) to master. #9 already includes #8's latest commit; bring #10 up to date with master before merging. Both touch `intake.html`, `app.py` and `tests/test_web.py`, so merge one, then update the other.
4. Merge Warp (#7), which targets master independently.
5. On master, run `python -m pytest evals/test_feature_integration.py`, then close #5.

Never auto-merge. Run pytest and scripted evals on each branch. No credentials are required. Local `.env` files are not publishing sources.
