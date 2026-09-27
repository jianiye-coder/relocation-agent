# Relocation Agent

Relocation Agent is an open-source assistant for planning a move in the United States. It turns a move into a set of reviewable decisions: find a rental home, compare trucks, movers, storage and containers, check providers and listings for scam risks, estimate the true cost, and organize the work on a practical timeline.

The app combines a conversational agent with deterministic planning code. Prices, distance calculations, budget ranking, risk checks and dates stay explainable in code, while provider adapters attach live or sample data with a source, timestamp and confidence level. Nothing is booked, paid for or sent to a business automatically.

## Repository structure

| Path | Purpose |
| --- | --- |
| `moving_agent/web/` | FastAPI routes, sessions, forms and Jinja templates for the intake, housing, plan, safety-check and approval screens. |
| `moving_agent/agent.py` | Agent orchestration, tool calls, alternatives, summaries and the deterministic no-LLM fallback. |
| `moving_agent/adapters/` | Shared provider contracts plus quote, housing-adjacent service, vehicle, storage, truck, container and FMCSA adapters. |
| `moving_agent/home.py` | Rental search provider interface, HomeHarvest/Realtor.com listings, photos, commute and explicit unavailable states. |
| `moving_agent/planner.py` and `providers/` | Explainable offer combinations, sample catalog rates and budget-aware plan selection. |
| `moving_agent/inventory.py`, `truecost.py`, `timeline.py` | Deterministic volume/weight estimates, move-in cost calculations and backward scheduling. |
| `moving_agent/listings.py` and `mover`/`listing` checks | Scam-signal checks for rental listings and FMCSA registration checks for movers. |
| `tests/` and `evals/` | Offline regression tests, adapter contracts, fixtures and deterministic agent evaluations. |
| `docs/` and `DESIGN.md` | Product requirements, UI guidance and design decisions. |

Read the standalone [PRD](docs/PRD.md) and [fixture guide](tests/fixtures/README.md) for the product scope and test data conventions.
