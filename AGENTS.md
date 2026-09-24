# AGENTS.md

Guide for AI coding agents (and humans) working on this repo.

## What this is

Relocation Agent: an open-source AI agent for people relocating cities (MVP: housing, storage, moving; LA -> SF). Built by Jenny (adapters, evals, intake/UI) and Sumit (orchestrator, per-area agents, browser layer). Repo: https://github.com/jianiye-coder/relocation-agent

A moving services platform with an AI agent at its core. The user fills in an intake form; the agent finds the best combination of truck (U-Haul, Budget, Penske), movers (TaskRabbit, HireAHelper), storage (Extra Space, Public Storage, CubeSmart) or container (PODS, U-Pack) for their dates and budget; the user reviews the drafted quote-request emails and sends them from their own account. It also drafts resale listings; housing and lease are planned.

- PRD: `docs/PRD.md`
- UI handoff: `docs/ui-handoff.md`
- Visual style: `DESIGN.md` (read it before any UI work)
- Scope: US moves only. Demo on Sunday 2026-09-27 with real users.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install "pydantic-ai-slim[anthropic,google,openai]" cryptography fastapi uvicorn jinja2 python-multipart email-validator python-dotenv httpx google-auth google-auth-oauthlib google-api-python-client pytest aiosmtpd
.venv/bin/uvicorn moving_agent.web.app:app --port 8787 --reload   # run the app
.venv/bin/python -m pytest -q                                      # run all tests (must stay green)
.venv/bin/python scripts/send_test_email.py you@example.com        # send one email with the configured sender
.venv/bin/python scripts/gmail_auth.py                             # get a Gmail refresh token
```

Settings live in `.env` (copy `.env.example`). Never commit `.env`, `.token_key`, `data/accounts.db` or `client_secret.json`.

The LLM is picked from whichever key is set: `FLATKEY_API_KEY` -> Claude Sonnet 5 through Flatkey's OpenAI-compatible gateway (`flatkey:<model>`, base URL `https://router.flatkey.ai/v1`; current default in `.env`), `ANTHROPIC_API_KEY` -> Claude Sonnet 5, `GOOGLE_API_KEY` -> Gemini Flash, `OPENAI_API_KEY` -> GPT-5.2; `MOVING_AGENT_MODEL` overrides. With no key, the app runs the same tools in a fixed order and says the agent is off.

## Layout

| Path | What it is |
| --- | --- |
| `moving_agent/models.py` | Pydantic contracts: `Intake`, `Offer`, `Plan`, `Requirements`, `EmailDraft`, `ListingDraft` and services/home-size enums |
| `moving_agent/providers/catalog.py` | Deterministic sample-rate catalog read from `data/catalog.json`; illustrative only |
| `moving_agent/planner.py` | Collects offers and combines one compatible offer per required service, ranking plans within budget first |
| `moving_agent/agent.py` | Pydantic AI orchestration: requirements, offer search, variants, plan choice, listings, vehicle comparison, true cost, timeline, mover vetting, listing checks and approval-gated quote-request delivery. `fill_derived()` refreshes computed details; `run_without_llm()` is the deterministic fallback. |
| `moving_agent/adapters/base.py` | Provider contract: normalized `MoveRequest`, `Quote`, `CarrierCheck`, metadata, source kind, price confidence caps and typed adapter errors |
| `moving_agent/adapters/registry.py` | Concurrent adapter runner with SQLite cache, per-adapter expiry, stale fallback and opt-in unofficial sources |
| `moving_agent/adapters/bridge.py` | Converts registry quotes to planner `Offer`s; used by both web app and evals |
| `moving_agent/adapters/request.py` | Maps `Intake` into the normalized provider request |
| `moving_agent/adapters/sample_catalog.py`, `vehicle.py`, `warp.py`, `fmcsa.py` | Sample-rate, ship-vs-drive, Warp LTL (stub) and FMCSA mover-vetting adapters |
| `moving_agent/accounts.py` | Per-user Gmail OAuth (`gmail.send` + `openid email`), encrypted refresh tokens in SQLite |
| `moving_agent/inventory.py`, `truecost.py`, `timeline.py`, `listings.py`, `geo.py` | Deterministic inventory, all-in cost, backward move schedule, rental-risk and geocoding/distance modules |
| `moving_agent/drafts.py`, `emailer.py` | Deterministic quote/listing drafts and Gmail API, SMTP or local-outbox delivery |
| `moving_agent/web/` | FastAPI app, session state and Jinja screens for intake, plan/chat, approval, listing check, sent view and Gmail connect callback |
| `evals/` | Pydantic Evals harness: LA -> SF cases, deterministic evaluators and reports (`python -m evals.run`) |
| `tests/` | pytest; email tests use a real local SMTP server; external APIs are faked |

## Rules that must not break

1. **Money and distance math is code, never the LLM.** Prices, totals, volumes, stairs, dates and budget ranking come from `catalog.py` / `planner.py`. The model decides which tools to call and with what (dates to try, plan to pick, constraints to add); the only text it writes is the summary (`AgentReply`).
2. **Sending always needs the user's approval.** `send_quote_requests` is declared with `requires_approval=True`: the run pauses, the app shows the exact emails, and only "Approve and send" resumes it. Never remove that flag. Any agent that reads websites (the future mover finder) must not get a send tool at all.
3. **Nothing reaches a real business by accident.** Sample contacts use the reserved `.example` domain. For real-send tests, set `EMAIL_REDIRECT_TO` to your own address.
4. **Every price is explainable.** Each `Offer` carries `price_basis` (how it was calculated) and `source`. Show both in the UI.
5. **Label sample data honestly.** Until live adapters exist, the UI says prices come from a sample catalog of illustrative rates.
6. **Only the `gmail.send` scope** (plus `openid email` to learn the address). Never request Gmail read scopes. Each user sends from their own connected Gmail; tokens stay encrypted.
7. **Every price carries source, timestamp and confidence** (`Quote`), and confidence never exceeds the cap for its price kind.
8. **Run the evals** (`python -m evals.run`) after changing the agent's instructions or tools, and keep them at 100%.
9. **Keep tests green** and add a test with every behavior change. External services (Census, OSRM, Google, Gmail) are faked in tests.

## Naming

The GitHub repository and distribution name are `relocation-agent`. Keep the internal import package as `moving_agent` unless a dedicated compatibility migration is planned; renaming it would be a breaking API change.

## Adding a live provider

Subclass `QuoteAdapter` in `moving_agent/adapters/`, fill `AdapterMetadata`, return `Quote`s with `source`, `fetched_at`, `confidence` and `price_basis`, raise `AdapterError(ErrorCode.x, ...)` on failure, register it in `default_registry()`, and add it to `ALL_QUOTE_ADAPTERS` in `tests/test_adapters.py` so the contract tests run. Scrapers and unofficial APIs use `SourceKind.unofficial_scrape` and stay off unless the user opts in. The web app and evals read quotes through `RegistrySource` (adapters/bridge.py), so a new adapter shows up in the agent automatically. Planned sources and the APIs they need are in the PRD section "APIs we need".

## UI work

- Follow `DESIGN.md`: white canvas, ink #222222, one accent #ff385c for primary actions only, 8px buttons, 14px cards, pill toggles, one shadow tier, Inter font. Don't use Airbnb's name or logo.
- Phone first (375 px), no horizontal scroll, 44 px+ tap targets, every screen has loading, empty and error states.
- UI copy: short, plain, specific. Say what happens ("Nothing is sent until you click the button").

## Open items

- Who owns what (Jenny / Sumit) is still under discussion.
- Real Gmail send not yet run: needs a Google Cloud OAuth client (Web application) with redirect URI `http://localhost:8787/auth/google/callback` (see README).
- The agent runs on `flatkey:claude-sonnet-5` (verified 2026-09-23: plan, what-if, chat, approval, send). A turn takes about 5-30 s; there is no loading indicator yet.
- Provider search (Tavily / Google Places) not wired in yet; see README.
- Hidden in the MVP behind `.env` flags (see PRD "Hidden in the MVP"): room photo scan (`ENABLE_PHOTO_INVENTORY`, planned to become photo → Facebook Marketplace listing) and voice intake (`ENABLE_VOICE_INTAKE`).
- Housing listings adapter, neighborhood fit (crime, schools), utilities lookup, orchestrator + per-area agents and the browser layer are in the PRD but not in code yet.
