# Relocation Agent — MVP PRD

## Purpose

Help a user relocating from Los Angeles to San Francisco compare a moving plan and up to two candidate homes. Prices, inventory math, dates and ranking come from deterministic code. Models choose tools and summarize their results.

## Scope and acceptance

1. Candidate-home intake: collect up to two addresses, commute destination, mode and optional departure time; preserve existing move, household, pet, vehicle, lease, inventory and access fields.
2. Photo inventory (**hidden in the MVP**, see below): accept 1–10 JPEG/PNG/WebP images, up to 8 MB and 20 megapixels each. Extract visible items with the selected image-capable model. Show editable items before applying them to planning. Calculate volume/weight in code. Keep manual inventory available on any model failure.
3. Warp: request quotes only; normalized source, timestamp, confidence, price basis and typed failures. Clearly distinguish sandbox data from live results. No fabricated fallback prices.
4. Candidate decision data: source commute data where configured; show honest school/internet/electricity availability, with no guessed providers. Actual GreatSchools/FCC/NREL adapters are deferred until their contracts and access are verified.
5. Quality: credential-free fixtures and CI, six deterministic eval cases, readable configuration and module documentation.

6. Errands: the timeline carries a prepared document for each errand around the move (landlord notice, utilities start/stop, internet, USPS forwarding, DMV and car insurance when there's a vehicle, vet records when there are pets, school enrollment when children move, memberships, prescriptions and records, banks, who to tell, furniture, first 48 hours, commute). Documents are built in code from the intake (`moving_agent/errands.py`); unknown details show as `<placeholders>`. Each errand is `needs_user_action` (names the missing intake fields), `prepared_for_user`, or `done` (only when the user marks it). Nothing is ever contacted, filed or submitted for the user. Roster and status model adapted from [relocate-ai](https://github.com/vnmoorthy/relocate-ai) (MIT).
7. Arrival pack: one email with every open errand's document, sent only to the intake email address after the user previews it and clicks "Email it to me". Sent once per plan; failures are shown, not hidden. Delivery uses the server sender (`EMAIL_MODE`: outbox by default, smtp or gmail) and `EMAIL_REDIRECT_TO` for test deployments. Also downloadable as text.

Quote-request email drafting, approval and delivery to businesses stay removed from the MVP UI and agent; the arrival pack is the only email, and it goes only to the user. Preserve the delivery modules for a future reviewed feature. Booking, payments, crime scoring, automated scraping and utilities activation are outside scope.

## User flow

Fill intake (addresses, date, home size, optional item list) → generate move options → review sample/live labels, true cost, vehicle comparison, timeline and candidate homes → refine planning with chat when a model is configured.

Uploaded files may spool to OS temporary storage while the request runs. Close files on success and failure; never retain user-named paths. Photos are sent to the selected model provider, whose retention policy applies independently. Deployments need an upload request-body limit at the reverse proxy.

## Hidden in the MVP, kept for later

These are built and tested but not shown in the MVP intake form. Each comes back by setting its flag in `.env`; the code, endpoint and tests stay in the repo.

| Feature | Flag | Why it's hidden | Plan for later |
| --- | --- | --- | --- |
| Room photo scan (`moving_agent/photo_inventory.py`, `/api/inventory/photos`) | `ENABLE_PHOTO_INVENTORY=1` | Estimating volume from photos adds little over typing rooms or picking a home size | Turn it into "photograph an item → draft a Facebook Marketplace listing": the same image-capable model names the item, and the existing listing drafts (`listings.py`) fill in title, description and price range for the user to post |
| Voice intake (`moving_agent/voice_intake.py`, `/api/intake/voice-autofill`) | `ENABLE_VOICE_INTAKE=1` | Not needed to plan a move in the MVP | Revisit once the form is stable |

Verified before hiding: photo scan ran end to end on `flatkey:claude-sonnet-5` (2026-09-24; a drawn room returned sofa ×1, box ×3). Known gap: the same item in two photos is counted twice.

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
