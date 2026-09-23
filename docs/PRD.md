# Relocation Agent — Product Requirements Document

## 1. Summary

Relocation Agent is an open-source AI assistant for people moving within the United States. It turns one relocation intake into an explainable plan for moving services, temporary storage, vehicle transport and move-in logistics.

The initial demo focuses on a Los Angeles-to-San Francisco move. It is intentionally a planning and quote-request product: it does not book services, take payment or send anything without the user's review and approval.

## 2. Problem

Planning a move means comparing services that use incompatible inputs and pricing: truck rental, moving labor, storage, containers and vehicle transport. People must also coordinate move dates, stair access, lease timing, budgets, rental risks and belongings they want to sell. Existing comparison tools usually handle one service in isolation and do not show the total relocation cost.

## 3. Product goals

1. Collect a move profile once and use it across every planning step.
2. Compare compatible moving-service combinations and show a recommendation that fits the user's date and budget where possible.
3. Make all money, distance, volume and date calculations traceable to deterministic code and provider data.
4. Give users a practical move timeline and a fuller view of costs beyond the mover quote.
5. Let users review exact quote-request emails and send only after an explicit approval step.
6. Provide an open, adapter-based foundation for replacing illustrative rates with live provider quotes.

## 4. Non-goals for the MVP

- Booking, payments, deposits or contracts.
- Automatic outreach to a provider or any email sent without explicit approval.
- A nationwide live-provider marketplace or guaranteed availability.
- Legal, financial, housing or insurance advice.
- Gmail inbox access, email reading or contact harvesting.
- A full housing search, neighborhood ranking, utilities activation or room-photo scan.

## 5. Users and primary jobs

| User | Job to be done |
| --- | --- |
| Renter or homeowner relocating | Build an affordable, date-compatible move plan without manually reconciling multiple providers. |
| Budget-sensitive mover | See the least-cost viable combinations and what changes could reduce cost. |
| User moving a vehicle | Compare an estimated carrier shipment with driving the vehicle. |
| User evaluating a mover or rental | Check a mover's USDOT/MC record or flag obvious rental-listing risks. |

## 6. MVP experience

1. The user completes the intake: origin/destination, move date, home size, access details, services needed, budget, contact details, inventory, vehicle and optional lease/rent details.
2. The agent reads the profile and any chat constraints, such as unavailable providers, a piano, crew size or date flexibility.
3. Deterministic provider sources return normalized offers. The planner combines compatible offers for the same move date and ranks plans, putting plans within budget first.
4. The app shows offers, plans, source, calculation basis and the fact that catalog prices are illustrative sample data.
5. The agent can test alternatives, select a lower-cost valid variant, estimate all-in move cost, build a backwards schedule, draft resale listings, compare vehicle options, vet a mover and assess a rental listing.
6. If a selected plan fits the user's requirements, the agent prepares quote-request emails. The run pauses and renders the exact messages.
7. The user either declines or clicks **Approve and send**. Only that explicit action can resume delivery through their connected Gmail, configured SMTP sender or local test outbox.

## 7. Functional requirements

### 7.1 Intake and planning

- Validate required move dates, ZIPs, home size, services and budget before planning.
- Estimate inventory volume, weight and special items from a room or item list.
- Price moving, labor, storage and container services from normalized data.
- Build combinations with one compatible offer for each requested service and one move date.
- Rank the cheapest plan within budget first; show an honest shortfall if none fits.
- Let chat updates amend requirements and recalculate the plan.
- Test reasonable alternatives such as nearby dates, container instead of truck or a smaller crew when the chosen plan is expensive or over budget.

### 7.2 Decision support

- Calculate true cost from a chosen move plan plus applicable deposit, first month, applications, utilities, storage and vehicle costs.
- Generate a task plan backwards from move date and lease end.
- Compare shipping a vehicle with driving it.
- Check rental-listing text for rule-based risks including suspicious language, below-market pricing, duplicate content and missing basics.
- Vet a mover through FMCSA when the user supplies a USDOT or MC number.
- Draft resale listings for belongings the user plans to sell.

### 7.3 Quotes, providers and explainability

- Use a common adapter contract for provider requests, quotes, sources, confidence, timestamps and price basis.
- Cap quote confidence by price kind: firm quote, published rate, estimate or sample rate.
- Cache adapter responses with expiry and use a clearly marked stale result only when a refresh fails.
- Keep unofficial or scraped providers disabled unless explicitly opted in.
- Treat the built-in catalog as illustrative sample data until a live adapter is enabled.

### 7.4 Email and identity

- Generate quote-request emails deterministically from the selected offers and intake.
- Require explicit user approval for every send operation. This requirement applies to all providers and delivery modes.
- Support a local outbox for safe development, SMTP for tests and per-user Gmail delivery for the product flow.
- Limit Google OAuth scopes to `gmail.send`, `openid` and `email`; never request Gmail read scopes.
- Encrypt per-user refresh tokens at rest.
- Route test mail to `EMAIL_REDIRECT_TO` when configured. Sample contacts must use the reserved `.example` domain.

## 8. Trust, safety and quality requirements

- Money, distance, dates, inventory volume, access adjustments and ranking must be calculated in code, never invented by the language model.
- The model may select tools and write a short plain-language summary, but cannot invent providers, quotes, dates or contact information.
- Every displayed price must retain a source, timestamp, confidence and calculation basis.
- The UI must state when rates are sample or illustrative.
- Sending must remain approval-gated by a `requires_approval=True` tool declaration.
- External integrations are faked in automated tests. The test suite must remain green.
- Changes to agent instructions or tools require the evaluation suite to remain at 100%.

## 9. Technical approach

| Layer | Responsibility |
| --- | --- |
| `moving_agent/models.py` | Validated intake, offer, plan, requirement and draft data contracts. |
| `moving_agent/adapters/` | Normalized provider and vetting adapters, caching registry, errors and planner bridge. |
| `moving_agent/providers/catalog.py` | Deterministic illustrative sample rates from `data/catalog.json`. |
| `moving_agent/planner.py` | Offer collection and deterministic plan combinations/ranking. |
| `moving_agent/agent.py` | Pydantic AI orchestration, chat tools, approval pause and no-LLM fallback. |
| `moving_agent/inventory.py`, `truecost.py`, `timeline.py`, `listings.py`, `geo.py` | Deterministic move intelligence. |
| `moving_agent/drafts.py`, `emailer.py`, `accounts.py` | Drafting, delivery and per-user Gmail OAuth. |
| `moving_agent/web/` | FastAPI and Jinja intake, plan, chat, approval and Gmail-connect flows. |
| `tests/`, `evals/` | Unit/integration tests and deterministic scenario evaluation. |

The GitHub repository and Python distribution are named `relocation-agent`. The import package remains `moving_agent` for compatibility.

## 10. Success criteria for the demo

- A user can complete the LA-to-SF intake and receive an understandable plan.
- The app shows at least one sample-backed combination with price source and basis, or clearly explains why no plan fits.
- A user can request a cheaper alternative or change a constraint in chat and see a recomputed plan.
- The app can present exact quote-request drafts, and no message is delivered before approval.
- The application test suite passes, and deterministic eval cases pass at 100%.

## 11. Roadmap after MVP

- Official live adapters for truck, labor, storage and container providers.
- Provider discovery through opted-in search sources.
- Housing listings adapter, neighborhood fit signals and lease planning.
- Utilities lookup, room-photo inventory scan and resale publishing workflow.
- Orchestrator and per-area agents with a browser research layer that cannot send mail.

## 12. Configuration

Users choose one preferred LLM provider by setting one of `FLATKEY_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` or `OPENAI_API_KEY`. `MOVING_AGENT_MODEL` optionally selects a compatible model. No LLM key is required for deterministic planning.

Email delivery and map enhancement are optional. See the [README settings](../README.md#settings) for the full environment-variable reference.
