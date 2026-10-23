"""Orchestrator on the minimal Responses host.

action=delegate: call both Specialists directly (no model).
action=ask: let the model pick Specialists through tools; results are captured in code.
"""

import asyncio
import functools
import json
import logging
import os
from urllib.parse import quote

import httpx
from azure.ai.agentserver.core import configure_observability, get_request_context
from headers import InboundHeaderProbe, log_inbound, outbound, outbound_hook
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    TextResponse,
)

log = logging.getLogger("lab-orchestrator")
HOP_KEYS = ("agent", "testId", "trustedUserId", "callId", "payloadOid", "fact", "inboundHeaders", "sentHeaders")


def responses_url(endpoint, name):
    return (
        endpoint.rstrip("/")
        + "/agents/"
        + quote(name, safe="")
        + "/endpoint/protocols/openai/responses?api-version=v1"
    )


def output_text(payload):
    return "".join(
        part.get("text", "")
        for item in payload.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


async def call_specialist(client, token, name, platform, test_id, payload_oid, endpoint=None):
    endpoint = endpoint or os.environ["LAB_PROJECT_ENDPOINT"]
    headers = {"Authorization": "Bearer " + token}
    if platform.call_id:
        headers["x-agent-foundry-call-id"] = platform.call_id
    if platform.user_id:
        headers["x-ms-user-identity"] = platform.user_id
    hop = {k: None for k in HOP_KEYS}
    hop.update(agent=name, testId=test_id, ok=False, error=None, forwardedUser=bool(platform.user_id))
    client.event_hooks["request"] = [outbound_hook]
    try:
        response = await client.post(
            responses_url(endpoint, name),
            headers=headers,
            json={
                "input": json.dumps({"action": "echo", "testId": test_id, "payloadOid": payload_oid}),
                "stream": False,
                "store": False,
                "background": False,
            },
            timeout=120,
        )
    except httpx.HTTPError as e:
        hop["sentHeaders"] = outbound.get()
        hop["error"] = "transport: " + type(e).__name__
        log.info("specialist %s transport error %s", name, type(e).__name__)
        return hop
    hop["sentHeaders"] = outbound.get()
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code != 200:
        hop["error"] = "http_%d" % response.status_code
    elif not isinstance(payload, dict):
        hop["error"] = "unparseable_output"
    elif payload.get("status") != "completed":
        hop["error"] = (payload.get("error") or {}).get("code") or "status_" + str(payload.get("status"))
    else:
        try:
            reply = json.loads(output_text(payload))
        except ValueError:
            reply = None
        if not isinstance(reply, dict):
            hop["error"] = "unparseable_output"
        else:
            hop.update({k: reply[k] for k in HOP_KEYS if k in reply and k != "sentHeaders"})
            hop["ok"] = True
    log.info("specialist %s http=%s ok=%s error=%s", name, response.status_code, hop["ok"], hop["error"])
    return hop


async def delegate(call, names):
    return [await call(name) for name in names]


def specialist_names():
    return [os.environ["LAB_FINANCE_NAME"], os.environ["LAB_RISK_NAME"]]


# Outbound httpx spans are off by default in the SDK; enabling them propagates traceparent to the Specialists.
app = ResponsesAgentServerHost(
    configure_observability=functools.partial(
        configure_observability, instrumentation_options={"httpx": {"enabled": True}}
    )
)
app.add_middleware(InboundHeaderProbe)


@app.response_handler
async def handler(request: CreateResponse, context: ResponseContext, _cancel: asyncio.Event):
    from azure.identity.aio import DefaultAzureCredential

    command = json.loads(await context.get_input_text() or "{}")
    platform = get_request_context()
    test_id, payload_oid = command.get("testId", ""), command.get("payloadOid")
    reply = {
        "agent": "lab-orchestrator",
        "testId": test_id,
        "trustedUserId": platform.user_id or None,
        "callId": platform.call_id or None,
        "payloadOid": payload_oid,
        "hops": [],
        "answer": None,
        "inboundHeaders": log_inbound(),
        "error": None,
    }
    async with DefaultAzureCredential() as credential, httpx.AsyncClient() as client:
        token = (await credential.get_token("https://ai.azure.com/.default")).token

        async def call(name):
            return await call_specialist(client, token, name, platform, test_id, payload_oid)

        if command.get("action") == "delegate":
            reply["hops"] = await delegate(call, specialist_names())
        elif command.get("action") == "ask":
            from ask import run_ask  # Task 3

            try:
                reply["answer"], reply["hops"] = await run_ask(
                    credential, platform, call, specialist_names(), command.get("question", "")
                )
            except Exception as e:  # type name only: the text may carry prompt/model output
                reply["error"] = "ask: " + type(e).__name__
        else:
            raise ValueError("Orchestrator supports actions delegate and ask")
    return TextResponse(context, request, text=json.dumps(reply))


def main():
    logging.basicConfig(level=logging.INFO)
    app.run()


if __name__ == "__main__":
    main()
