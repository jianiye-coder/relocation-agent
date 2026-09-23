# Relocation Agent

Open-source AI agent for people relocating cities. MVP scope: housing, storage and moving, for one city pair (Los Angeles -> San Francisco).

The user fills in one intake profile. The agent (Pydantic AI) compares every way to move in one quote format, vets movers with FMCSA, checks rental listings for scam signs, adds up the true cost (move + deposit + first month + utilities + storage + vehicle), plans a timeline backward from the move date, and emails providers from the user's own account after the user approves. Every price carries its source, a timestamp and a confidence. No booking or payment.

Status: early MVP. Prices come from a sample catalog of illustrative rates until live adapters are connected.

PRD: [docs/PRD.md](docs/PRD.md)

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn moving_agent.web.app:app --port 8787
```

Open http://localhost:8787, fill in the form, review the plan, and use the chat to refine it. Nothing is sent until you review the exact drafts and click **Approve and send**.

Copy `.env.example` to `.env` to configure an LLM or email provider. With no API key, the app uses its deterministic planning path so the demo remains usable.

## How it works

| Piece | File | What it does |
| --- | --- | --- |
| Intake and models | `moving_agent/models.py`, `moving_agent/web/templates/intake.html` | Validated move profile: dates, ZIPs, home size, access, services, budget, inventory and items to sell |
| Agent orchestration | `moving_agent/agent.py` | Pydantic AI tools update constraints, search offers, compare variants, choose a plan, estimate full cost, plan a timeline, vet movers, check listings and draft emails. The send tool pauses for approval. |
| Provider adapters | `moving_agent/adapters/` | Normalized quote and vetting interface, concurrent registry, SQLite cache, typed errors and stale-result fallback. Includes sample catalog, vehicle ship-vs-drive, FMCSA and Warp LTL stub adapters. |
| Catalog and planner | `moving_agent/providers/catalog.py`, `moving_agent/planner.py` | Deterministic illustrative rates and every compatible service combination, ranked within budget first. |
| Move intelligence | `moving_agent/inventory.py`, `truecost.py`, `timeline.py`, `listings.py`, `geo.py` | Inventory volume and weight, move-in costs, backward task plan, rental-risk checks, address-to-ZIP and driving distance. |
| Drafting and delivery | `moving_agent/drafts.py`, `emailer.py`, `accounts.py` | Deterministic quote and resale drafts; local outbox, SMTP and per-user Gmail OAuth delivery with encrypted tokens. |
| Web app | `moving_agent/web/` | FastAPI intake, planning, chat, approval, sent-email and Gmail-connect flows. |
| Quality checks | `tests/`, `evals/` | Unit/integration tests plus deterministic LA-to-SF agent evals. |

The PyPI project and GitHub repository are named `relocation-agent`. The internal Python package remains `moving_agent` to preserve stable imports.

## Safety and data boundaries

- Prices, dates, distance, volume and ranking are calculated in code; the model only chooses which tools to call and writes a short summary.
- Every quote shows its source, timestamp, confidence and calculation basis. Sample rates are clearly labelled illustrative.
- Sending is approval-gated. The app renders the exact messages first, and no agent or provider-search layer can send mail on its own.
- Gmail access is limited to `gmail.send` plus `openid email`; no inbox-reading scopes are requested. Tokens are encrypted locally.

## Settings

Choose one LLM provider and set only its API key. You do not need keys for every provider. Set `MOVING_AGENT_MODEL` only when you want to override that provider's default model; otherwise the app selects a sensible default. With no LLM key, the app uses its deterministic planning path.

| Variable | Meaning |
| --- | --- |
| `FLATKEY_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or `OPENAI_API_KEY` | Set exactly one: use your preferred LLM provider. Flatkey, Anthropic, Gemini Flash and GPT-5.2 are supported. |
| `MOVING_AGENT_MODEL` | Optional model override for the provider you selected |
| `EMAIL_REDIRECT_TO` | Send every email to this address instead of the provider (for real-send tests) |
| `GOOGLE_MAPS_API_KEY` | Optional: Google geocoding and driving distance. Without it, the free Census geocoder and OSRM are used |
| `EMAIL_MODE` | `outbox` (default, saves .eml files to `outbox/`), `smtp`, or `gmail` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS` | For `smtp` mode. For Gmail SMTP: `smtp.gmail.com`, `587`, your address, a Google app password |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` | For `gmail` mode (Gmail API, `gmail.send` scope) |

Sample provider emails use the reserved `.example` domain, so even in `smtp` or `gmail` mode nothing reaches a real business until real contacts replace the sample catalog.

## Send a real email

Emails in the sample catalog go to `.example` addresses, which don't exist. To see a real email arrive, send everything to yourself:

**Fastest: Gmail SMTP (about 5 minutes)**
1. Turn on 2-Step Verification for your Google account.
2. Create an app password at https://myaccount.google.com/apppasswords.
3. `cp .env.example .env` and set:
   ```
   EMAIL_MODE=smtp
   EMAIL_REDIRECT_TO=you@gmail.com
   SMTP_USERNAME=you@gmail.com
   SMTP_PASSWORD=<the 16-character app password>
   ```
4. Restart the app, make a plan, click Send. The emails arrive in your inbox, marked `[test, would go to ...]`.

**Each user sends from their own Gmail (the real product flow)**
1. Google Cloud Console: enable the Gmail API, set up the OAuth consent screen (External, Testing), add your test users, and add the scopes `openid`, `email` and `gmail.send` only.
2. Create an OAuth client of type **Web application** with the redirect URI `http://localhost:8787/auth/google/callback`.
3. In `.env`: `GOOGLE_CLIENT_ID=...` and `GOOGLE_CLIENT_SECRET=...` (keep `EMAIL_REDIRECT_TO` set while testing).
4. Restart. On the plan page, click **Connect Gmail**, then **Approve and send** when the agent asks.

**Single-account Gmail API (scripts only)**

```bash
# Gmail API: create a "Desktop app" OAuth client in Google Cloud, save it as client_secret.json, then:
.venv/bin/python scripts/gmail_auth.py          # prints the env vars to put in .env
EMAIL_MODE=gmail GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... GMAIL_REFRESH_TOKEN=... \
  .venv/bin/python scripts/send_test_email.py you@gmail.com
```

`scripts/send_test_email.py` works with any `EMAIL_MODE` (for SMTP, set the `SMTP_*` variables instead).

## Provider adapters

Every provider sits behind one interface (`moving_agent/adapters/base.py`):

- **Input:** `MoveRequest`, built from the intake profile by `request_from_intake()` (origin, destination, date, volume, weight, access, storage, vehicles, household, pets, lease end, budget).
- **Output:** `Quote` (price, low/high range, service type, date, `source`, `fetched_at`, `confidence`, `price_kind`, how it was calculated) or `CarrierCheck` for vetting. Confidence is capped by price kind: firm quote 1.0, published rate 0.8, estimate 0.5, sample 0.3.
- **Metadata:** `AdapterMetadata` (capabilities, service types, coverage, auth + env vars, rate limit, `source_kind` official/public/sample/unofficial, cache TTL).
- **Errors:** `ErrorCode` = unavailable, blocked, no_coverage, stale, auth_missing, rate_limited, invalid_request. The `Registry` turns every failure into one of these and never raises.
- **Registry:** runs adapters concurrently with a timeout, caches results in SQLite with per-adapter expiry, serves an expired copy marked `stale` if a refresh fails, and skips unofficial/scraped adapters unless listed in `ENABLE_UNOFFICIAL_ADAPTERS`.

| Adapter | Kind | Needs |
| --- | --- | --- |
| `sample_catalog` | sample rates (truck, labor, storage, container) | nothing |
| `vehicle_estimate` | ship vs. drive estimate | nothing |
| `warp_ltl` | Warp LTL freight (stub until we have docs) | `WARP_API_KEY` |
| `fmcsa_qcmobile` | USDOT / MC carrier vetting | `FMCSA_WEB_KEY` (free) |

To add one: subclass `QuoteAdapter`, fill `metadata`, implement `fetch_quotes(req)`, raise `AdapterError` on failure, and add it to `default_registry()`. `tests/test_adapters.py` runs the contract checks on every adapter automatically once it's in `ALL_QUOTE_ADAPTERS`.

## Evals

```bash
.venv/bin/python -m evals.run                   # the model from .env
.venv/bin/python -m evals.run --model scripted  # no LLM, checks the harness
.venv/bin/python -m evals.run --case budget_cannot_be_met --repeat 3
```

Five LA -> SF cases (`evals/cases.py`) scored by deterministic checks (`evals/evaluators.py`): nothing sent without approval, every dollar amount in the summary traceable to a tool result, budget handled honestly, exclusions respected, notes carried into emails, alternatives tried, no invented plan, weekday move when needed. Reports go to `evals/reports/`.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The email tests start a real SMTP server on localhost and check that it receives the messages, including a full run through the web app (form -> plan -> confirm -> delivered).
