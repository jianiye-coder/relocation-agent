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
