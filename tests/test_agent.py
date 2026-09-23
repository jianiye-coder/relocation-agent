import asyncio

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from moving_agent.agent import AgentDeps, AgentReply, moving_agent, run_without_llm
from moving_agent.providers.catalog import SampleCatalog


def test_agent_has_no_email_delivery_tool():
    tools = moving_agent._function_toolset.tools
    assert "send_quote_requests" not in tools
    assert not any(tool.requires_approval for tool in tools.values())


def test_no_llm_plan_does_not_draft_or_send(intake):
    deps = AgentDeps(intake=intake, sources=[SampleCatalog()])
    assert "Recommended" in run_without_llm(deps)
    assert deps.plans


def test_agent_completes_without_email(intake):
    def model(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"summary": "Plan ready."})])
    result = asyncio.run(moving_agent.run("Plan my move.", deps=AgentDeps(intake=intake, sources=[SampleCatalog()]), model=FunctionModel(model)))
    assert isinstance(result.output, AgentReply)
