# Relocation Agent

An open-source planning assistant for US moves, starting with Los Angeles → San Francisco. Compare illustrative moving-service prices, vehicle shipping versus driving, inventory estimates, rental-listing risks, true cost, and a backward timeline. Email delivery is disabled in the web app and agent. No booking or payment is available.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
.venv/bin/uvicorn moving_agent.web.app:app --port 8787
```

Open http://localhost:8787. Choose one LLM provider, or leave all keys empty for deterministic planning. The distribution is `relocation-agent`; imports remain `moving_agent`.

## Feature branches

These features are reviewed separately and are not available on master until their PRs merge:

| Issue | PR | Scope |
| --- | --- | --- |
| [#1](https://github.com/jianiye-coder/relocation-agent/issues/1) | [#8](https://github.com/jianiye-coder/relocation-agent/pull/8) | Up to two candidate addresses, commute destination/mode/time |
| [#2](https://github.com/jianiye-coder/relocation-agent/issues/2) | [#10](https://github.com/jianiye-coder/relocation-agent/pull/10) | Photo extraction, editable item review, deterministic volume/weight |
| [#3](https://github.com/jianiye-coder/relocation-agent/issues/3) | [#7](https://github.com/jianiye-coder/relocation-agent/pull/7) | Warp quote adapter |
| [#4](https://github.com/jianiye-coder/relocation-agent/issues/4) | [#9](https://github.com/jianiye-coder/relocation-agent/pull/9) | Candidate commute and unavailable utilities states |

Photo and candidate-decision PRs currently target the intake branch. Merge/rebase intake first. Review limitations in [MVP acceptance](docs/mvp-acceptance.md) before treating the demo as production-ready.

## Settings

Set only your preferred LLM key. If several are set, selection priority is Flatkey → Anthropic → Google/Gemini → OpenAI. `MOVING_AGENT_MODEL` overrides the model name; select a model compatible with your key. Photos require image support from that model.

| Variable | Use / fallback |
| --- | --- |
| `FLATKEY_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `OPENAI_API_KEY` | Choose one. With no key, use text inventory and deterministic planning. |
| `MOVING_AGENT_MODEL` | Optional provider-prefixed model override. |
| `GOOGLE_MAPS_API_KEY` | Google geocoding/distance and candidate commute. Move geocoding/distance can fall back to Census/OSRM; candidate commute reports unavailable. |
| None | The Find a home page retrieves active rentals and photos from Realtor.com through [HomeHarvest](https://github.com/ZacharyHampton/HomeHarvest). It is an unofficial scrape, so availability can change and searches may be rate-limited; never treat its results as an MLS feed. |
| `FMCSA_WEB_KEY` | Carrier lookup. Missing credentials produce a typed unavailable/auth state. |
| `WARP_MODE` | `sandbox` (default) or `production`; production must be selected explicitly. No booking is called. |
| `WARP_API_KEY` | Sandbox credential used when `WARP_MODE=sandbox`; `wak_test_` quotes are mock data. |
| `WARP_PRODUCTION_API_KEY` | Live credential used only when `WARP_MODE=production`; quote assumptions remain labeled. |
| `QUOTE_CACHE=off` | Disable quote cache, useful for isolated verification. |

There is no implemented FCC or NREL/OpenEI address-level adapter. Do not add a `BROADBAND_API_KEY` or assume that an NREL key enables utility lookup. Internet, electricity and schools currently report unavailable on each candidate home (PR #9). Crime scoring is not implemented.

Delivery configuration and standalone scripts remain in the repository for future work, but the MVP web app and agent cannot draft, approve, or send quote-request emails. Do not run standalone delivery scripts during demo verification.

## Modules

| Module | Responsibility |
| --- | --- |
| `models.py`, `web/` | Intake validation, session, forms, plan and chat |
| `agent.py` | Tool selection, constraints, alternatives, derived costs/timeline |
| `adapters/` | Normalized quotes, source/confidence metadata, cache and errors |
| `providers/catalog.py`, `planner.py` | Illustrative rates and deterministic combinations |
| `inventory.py` | Item/room volume, weight and special items |
| `photo_inventory.py` (PR #10) | Model item extraction, bounded temporary uploads, editable review |
| `home.py` (PR #9) | Google Routes and explicit unavailable utilities states |
| `truecost.py`, `timeline.py`, `listings.py`, `geo.py` | Deterministic decision support |
| `accounts.py`, `emailer.py` | Preserved delivery modules, disconnected from MVP |
| `tests/`, `evals/` | Fixtures, offline regression tests, scripted harness and optional live model evals |

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m evals.run --model scripted
```

Tests fake external HTTP and fail accidental real HTTP. Standalone email-module tests use local SMTP only. Scripted evals use deterministic sources even if API keys exist in the shell. Both commands run in CI without secrets.

Six LA→SF scenarios check budget honesty, exclusions, alternatives, valid dates, no email delivery, grounded amounts, vehicle costs, and timeline. The scripted score verifies tool plumbing and the deterministic policy, not live model intelligence. Optional `python -m evals.run --model <provider:model>` evaluates your configured LLM and may incur provider charges; provider quote sources remain deterministic. Failed assertions or crashed cases exit nonzero. Reports are ignored under `evals/reports/`.

Read the standalone [PRD](docs/PRD.md) and [fixture guide](tests/fixtures/README.md).
