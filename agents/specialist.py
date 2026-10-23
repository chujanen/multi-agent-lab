"""Deterministic Specialist on the minimal Responses host: echoes identity context and one fixed fact."""

import asyncio
import json
import os
import re

from azure.ai.agentserver.core import get_request_context
from headers import InboundHeaderProbe, log_inbound
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    TextResponse,
)

FACTS = {
    "lab-finance": "revenue 100 -> 120 (+20%)",
    "lab-risk": "supplier concentration 80%: high risk",
}
TEST_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def build_reply(command, platform):
    name = os.environ["LAB_SPECIALIST_NAME"]
    if command.get("action") != "echo" or not TEST_ID.fullmatch(str(command.get("testId", ""))):
        raise ValueError("Specialist supports only {action: echo, testId}")
    return {
        "agent": name,
        "testId": command["testId"],
        "trustedUserId": platform.user_id or None,
        "callId": platform.call_id or None,
        "payloadOid": command.get("payloadOid"),
        "fact": FACTS[name],
    }


app = ResponsesAgentServerHost()
app.add_middleware(InboundHeaderProbe)


@app.response_handler
async def handler(request: CreateResponse, context: ResponseContext, _cancel: asyncio.Event):
    command = json.loads(await context.get_input_text() or "{}")
    reply = build_reply(command, get_request_context())
    reply["inboundHeaders"] = log_inbound()
    return TextResponse(context, request, text=json.dumps(reply))


def main():
    app.run()


if __name__ == "__main__":
    main()
