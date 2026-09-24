# Fixture provenance

Fixtures contain no credentials or user data. Do not claim synthetic fixtures are recordings of live requests.

- fmcsa/: existing sanitized carrier response examples.
- routes/: synthetic Google Routes responses for deterministic response-contract tests and future commute regressions.
- warp/: synthetic responses matching the documented quote fields. These do not establish live availability or sandbox labeling correctness.
- inventory/: synthetic extracted objects; model tests use generated 2×2 JPEG bytes, never personal room photos.
- intake/: synthetic complete LA→SF move for planning regression.
- scrapers/: trimmed from live pages captured 2026-09-24 (Public Storage San Francisco city page JSON-LD, U-Haul 90012→94110 rate page, Budget truck list JSON). Request tokens and session values removed; prices are from that day and will go stale.

Photo request and fallback tests travel with PR #10. Warp behavior tests travel with PR #7. This PR adds shared fixture files, offline enforcement, full-suite eval assertions and CI. For integration verification, combine the feature branches locally, not by merging remote PRs.
