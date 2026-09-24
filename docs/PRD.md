# Relocation Agent — MVP PRD

## Purpose

Help a user relocating from Los Angeles to San Francisco compare a moving plan and up to two candidate homes. Prices, inventory math, dates and ranking come from deterministic code. Models choose tools, summarize their results, and optionally identify items in photos.

## Scope and acceptance

1. Candidate-home intake: collect up to two addresses, commute destination, mode and optional departure time; preserve existing move, household, pet, vehicle, lease, inventory and access fields.
2. Photo inventory: accept 1–10 JPEG/PNG/WebP images, up to 8 MB and 20 megapixels each. Extract visible items with the selected image-capable model. Show editable items before applying them to planning. Calculate volume/weight in code. Keep manual inventory available on any model failure.
3. Warp: request quotes only; normalized source, timestamp, confidence, price basis and typed failures. Clearly distinguish sandbox data from live results. No fabricated fallback prices.
4. Candidate decision data: source commute data where configured; show honest school/internet/electricity availability, with no guessed providers. Actual GreatSchools/FCC/NREL adapters are deferred until their contracts and access are verified.
5. Quality: credential-free fixtures and CI, six deterministic eval cases, readable configuration and module documentation.

Email drafting, approval and delivery are removed from the MVP UI and agent. Preserve underlying delivery modules for a future reviewed feature. Booking, payments, crime scoring, automated scraping and utilities activation are outside scope.

## User flow

Fill intake → optionally scan photos → edit and apply item list → generate move options → review sample/live labels, true cost, vehicle comparison, timeline and candidate homes → refine planning with chat when a model is configured.

Uploaded files may spool to OS temporary storage while the request runs. Close files on success and failure; never retain user-named paths. Photos are sent to the selected model provider, whose retention policy applies independently. Deployments need an upload request-body limit at the reverse proxy.

## Sources and failure behavior

- Catalog: illustrative sample prices; never presented as confirmed availability.
- Warp: official quote adapter under review in PR #7; sandbox results are mock quotes. No booking capability.
- Google Routes: configured commute API in PR #9; failure or no key is unavailable.
- Schools, FCC broadband and NREL/OpenEI: deferred; no functional key configuration is advertised.
- FMCSA: registration lookup, with typed credential and provider errors.

Move-level geocoding uses Google when configured or Census; distance uses Google/OSRM with existing labeled estimates. These are distinct from candidate-home commute lookup.

## Verification and delivery

Every Issue has a separate draft PR. Review and merge manually. The local test suite blocks external HTTP; fixtures are synthetic or explicitly documented recordings. Scripted evals exercise tools and enforce all assertions, with nonzero exit on failure. Live model evals remain a separate opt-in check.

See [MVP acceptance and known gaps](mvp-acceptance.md) for current PR status and issues preventing complete demo acceptance.
