import json
from types import SimpleNamespace

import httpx

import orchestrator

ENDPOINT = "https://example.services.ai.azure.com/api/projects/p"


def platform(user_id="user-9", call_id="call-1"):
    return SimpleNamespace(user_id=user_id, call_id=call_id)


def responses_payload(status, text=None, error=None):
    body = {"id": "resp_1", "status": status}
    if text is not None:
        body["output"] = [{"type": "message", "content": [{"type": "output_text", "text": text}]}]
    if error:
        body["error"] = error
    return body


def client_with(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_responses_url():
    assert orchestrator.responses_url(ENDPOINT, "lab-risk") == (
        ENDPOINT + "/agents/lab-risk/endpoint/protocols/openai/responses?api-version=v1"
    )


async def test_call_specialist_forwards_headers_and_parses_reply():
    seen = {}

    def handler(request):
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        reply = {"agent": "lab-finance", "testId": "t1", "trustedUserId": None,
                 "callId": True, "payloadOid": "oid-1", "fact": "f"}
        return httpx.Response(200, json=responses_payload("completed", json.dumps(reply)))

    async with client_with(handler) as client:
        hop = await orchestrator.call_specialist(
            client, "tok", "lab-finance", platform(), "t1", "oid-1", endpoint=ENDPOINT
        )
    assert seen["headers"]["authorization"] == "Bearer tok"
    assert seen["headers"]["x-agent-foundry-call-id"] == "call-1"
    assert seen["headers"]["x-ms-user-identity"] == "user-9"
    assert json.loads(seen["body"]["input"]) == {"action": "echo", "testId": "t1", "payloadOid": "oid-1"}
    assert hop["ok"] is True and hop["forwardedUser"] is True
    assert hop["agent"] == "lab-finance" and hop["trustedUserId"] is None


async def test_call_specialist_omits_user_header_when_absent():
    seen = {}

    def handler(request):
        seen["headers"] = request.headers
        return httpx.Response(200, json=responses_payload("completed", "{}"))

    async with client_with(handler) as client:
        hop = await orchestrator.call_specialist(
            client, "tok", "lab-risk", platform(user_id=None), "t1", None, endpoint=ENDPOINT
        )
    assert "x-ms-user-identity" not in seen["headers"]
    assert hop["forwardedUser"] is False
    assert hop["agent"] == "lab-risk" and hop["testId"] == "t1"


async def test_call_specialist_rejects_non_object_output():
    def handler(request):
        return httpx.Response(200, json=responses_payload("completed", "[]"))

    async with client_with(handler) as client:
        hop = await orchestrator.call_specialist(client, "tok", "lab-risk", platform(), "t1", None, endpoint=ENDPOINT)
    assert hop["ok"] is False and hop["error"] == "unparseable_output"
    assert hop["agent"] == "lab-risk"


async def test_call_specialist_records_failed_status():
    def handler(request):
        return httpx.Response(200, json=responses_payload("failed", error={"code": "server_error"}))

    async with client_with(handler) as client:
        hop = await orchestrator.call_specialist(client, "tok", "lab-risk", platform(), "t1", None, endpoint=ENDPOINT)
    assert hop["ok"] is False and hop["error"] == "server_error" and hop["agent"] == "lab-risk"


async def test_call_specialist_records_http_error():
    def handler(request):
        return httpx.Response(403, json={"error": {"code": "Forbidden"}})

    async with client_with(handler) as client:
        hop = await orchestrator.call_specialist(client, "tok", "lab-risk", platform(), "t1", None, endpoint=ENDPOINT)
    assert hop["ok"] is False and hop["error"] == "http_403"


async def test_call_specialist_records_transport_error():
    def handler(request):
        raise httpx.ConnectTimeout("slow")

    async with client_with(handler) as client:
        hop = await orchestrator.call_specialist(client, "tok", "lab-risk", platform(), "t1", None, endpoint=ENDPOINT)
    assert hop["ok"] is False and hop["error"] == "transport: ConnectTimeout"


async def test_delegate_calls_every_name_in_order():
    calls = []

    async def call(name):
        calls.append(name)
        return {"agent": name, "ok": True}

    hops = await orchestrator.delegate(call, ["lab-finance", "lab-risk"])
    assert calls == ["lab-finance", "lab-risk"]
    assert [h["agent"] for h in hops] == calls
