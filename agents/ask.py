"""Model-routed delegation: gpt picks tools; application code records every Specialist call."""

import os

from agent_framework import Agent, tool

DESCRIPTIONS = {
    "financial_snapshot": "Current revenue figures and growth from the finance specialist.",
    "risk_snapshot": "Supplier concentration and risk classification from the risk specialist.",
}


def make_tools(call, names, captured):
    tools = []
    for tool_name, agent_name in zip(DESCRIPTIONS, names):

        def make(agent_name):
            async def snapshot() -> str:
                hop = await call(agent_name)
                captured.append(hop)
                if not hop.get("ok"):
                    return "specialist %s failed: %s" % (agent_name, hop.get("error"))
                return hop["fact"]

            return snapshot

        tools.append(tool(name=tool_name, description=DESCRIPTIONS[tool_name])(make(agent_name)))
    return tools


async def run_ask(credential, platform, call, names, question):
    from agent_framework.foundry import FoundryChatClient

    captured = []
    client = FoundryChatClient(
        project_endpoint=os.environ["LAB_PROJECT_ENDPOINT"],
        model=os.environ["LAB_MODEL_DEPLOYMENT"],
        credential=credential,
        default_headers=platform.platform_headers(),
    )
    try:
        agent = Agent(
            client=client,
            name="lab-orchestrator",
            instructions=(
                "Answer using the tools when the question concerns revenue or supplier risk. "
                "Report tool results plainly. Never invent figures."
            ),
            tools=make_tools(call, names, captured),
            default_options={"max_output_tokens": 600},
        )
        response = await agent.run(question)
        return response.text, captured
    finally:
        await client.client.close()
        await client.project_client.close()
