"""The Pydantic AI agent, driven by a scripted model (no API key needed)."""

import asyncio

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from moving_agent.agent import AgentDeps, AgentReply, moving_agent, pick_model, run_without_llm, trace
from moving_agent.emailer import SMTPSender
from moving_agent.providers.catalog import SampleCatalog


def scripted():
    """Plays a realistic agent: reads a note, searches, tries a container, picks, drafts, asks to send."""

    def model(messages, info: AgentInfo) -> ModelResponse:
        returns = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        done = [r.tool_name for r in returns]
        if "update_requirements" not in done:
            return ModelResponse(parts=[ToolCallPart("update_requirements", {"email_note": "includes an upright piano", "min_movers": 2})])
        if done.count("search_offers") < 2:
            service = ["truck", "labor"][done.count("search_offers")]
            return ModelResponse(parts=[ToolCallPart("search_offers", {"service": service})])
        if "build_plans" not in done:
            return ModelResponse(parts=[ToolCallPart("build_plans", {})])
        if "what_if" not in done:
            return ModelResponse(parts=[ToolCallPart("what_if", {"container_instead_of_truck": True})])
        if "choose_plan" not in done:
            return ModelResponse(parts=[ToolCallPart("choose_plan", {"plan_index": 0})])
        if "draft_listings" not in done:
            return ModelResponse(parts=[ToolCallPart("draft_listings", {})])
        if "send_quote_requests" not in done:
            chosen = next(r for r in returns if r.tool_name == "choose_plan").content
            return ModelResponse(parts=[ToolCallPart("send_quote_requests", {"offer_ids": [e["offer_id"] for e in chosen]})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"summary": "Booked the cheapest plan."})])

    return FunctionModel(model)


def deps_for(intake, account=None):
    return AgentDeps(intake=intake, sources=[SampleCatalog()], get_sender=lambda: account)


def test_tools_and_send_needs_approval():
    tools = moving_agent._function_toolset.tools
    assert {"update_requirements", "search_offers", "build_plans", "what_if", "adopt_variant", "choose_plan",
            "draft_listings", "send_quote_requests"} <= set(tools)
    assert tools["send_quote_requests"].requires_approval
    assert not any(t.requires_approval for n, t in tools.items() if n != "send_quote_requests")


def test_agent_pauses_for_approval_then_sends(intake, smtp_server):
    controller, handler = smtp_server
    deps = deps_for(intake, (SMTPSender("127.0.0.1", controller.port, starttls=False), intake.email))
    model = scripted()

    first = asyncio.run(moving_agent.run("Plan my move.", deps=deps, model=model))
    assert isinstance(first.output, DeferredToolRequests)
    assert handler.messages == []  # nothing sent before approval
    assert deps.plans and deps.emails and all("upright piano" in e.body for e in deps.emails)
    assert "what_if" in {s.get("tool") for s in trace(first.all_messages())}

    call = first.output.approvals[0]
    second = asyncio.run(moving_agent.run(
        message_history=first.all_messages(), deps=deps, model=model,
        deferred_tool_results=DeferredToolResults(approvals={call.tool_call_id: True}),
    ))
    assert isinstance(second.output, AgentReply)
    assert len(handler.messages) == len(deps.emails)
    assert all(r.ok for r in deps.sent)


def test_denied_send_sends_nothing(intake, smtp_server):
    controller, handler = smtp_server
    deps = deps_for(intake, (SMTPSender("127.0.0.1", controller.port, starttls=False), intake.email))
    model = scripted()
    first = asyncio.run(moving_agent.run("Plan my move.", deps=deps, model=model))
    call = first.output.approvals[0]
    asyncio.run(moving_agent.run(
        message_history=first.all_messages(), deps=deps, model=model,
        deferred_tool_results=DeferredToolResults(approvals={call.tool_call_id: ToolDenied("no")}),
    ))
    assert handler.messages == [] and deps.sent == []


def test_requirements_filter_offers(intake):
    deps = deps_for(intake)
    from moving_agent.agent import _search
    from moving_agent.models import Requirements
    deps.requirements = Requirements(exclude_providers=["TaskRabbit"], min_movers=2)
    # Saturday move: without TaskRabbit, only HireAHelper is left and it works weekdays.
    assert _search(deps, intake, "labor") == []
    flexible = intake.model_copy(update={"flexible_days": 3})
    labor = _search(deps, flexible, "labor")
    assert labor and all("TaskRabbit" not in o.provider for o in labor)
    assert all(o.available_on.weekday() < 5 for o in labor)


def test_trace_is_readable(intake):
    deps = deps_for(intake)
    run = asyncio.run(moving_agent.run("Plan my move.", deps=deps, model=scripted()))
    steps = trace(run.all_messages())
    assert steps[0] == {"kind": "user", "text": "Plan my move."}
    labels = [s["label"] for s in steps if s["kind"] == "tool"]
    assert labels[:3] == ["Updated requirements", "Searched offers", "Searched offers"]
    assert any("plan(s); best $" in (s["result"] or "") for s in steps if s["kind"] == "tool")


def test_pick_model_follows_the_key(monkeypatch):
    assert pick_model() is None
    monkeypatch.setenv("MOVING_AGENT_MODEL", "anthropic:claude-sonnet-5")
    assert pick_model() is None  # a model name alone isn't enough without a key
    monkeypatch.delenv("MOVING_AGENT_MODEL")
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    assert pick_model().startswith("google:")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert pick_model().startswith("anthropic:")
    monkeypatch.setenv("MOVING_AGENT_MODEL", "openai:gpt-5.2")
    assert pick_model() == "openai:gpt-5.2"
    monkeypatch.delenv("MOVING_AGENT_MODEL")
    monkeypatch.setenv("FLATKEY_API_KEY", "sk-x")
    assert pick_model() == "flatkey:claude-sonnet-5"
    from moving_agent.agent import build_model
    m = build_model("flatkey:gemini-2.5-flash")
    assert m.model_name == "gemini-2.5-flash" and "flatkey" in str(m.client.base_url)


def test_without_llm(intake):
    deps = deps_for(intake)
    summary = run_without_llm(deps)
    assert deps.plans and deps.emails and "Recommended" in summary


def test_no_offers_explains_which_days_work(intake):
    """Saturday move, TaskRabbit excluded: the tool tells the agent HireAHelper works weekdays."""
    from moving_agent.agent import _nearby_availability
    from moving_agent.models import Requirements
    deps = deps_for(intake)
    deps.requirements = Requirements(exclude_providers=["TaskRabbit"])
    hints = _nearby_availability(deps, "labor")
    assert hints and hints[0].startswith("HireAHelper:")
    assert "Sat" not in hints[0] and "Sun" not in hints[0]
