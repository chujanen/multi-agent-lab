import json

import httpx
import pytest

import foundry

SETTINGS = {
    "LAB_TENANT_ID": "t", "LAB_API_CLIENT_ID": "c", "LAB_API_CLIENT_SECRET": "s",
    "LAB_PROJECT_ENDPOINT": "https://example.test/api/projects/p", "LAB_ORCHESTRATOR_NAME": "orch",
}


class FakeMsal:
    result = {"access_token": "tok"}

    def __init__(self, *a, **k):
        pass

    def acquire_token_on_behalf_of(self, token, scope):
        return self.result

    def acquire_token_for_client(self, scope):
        return self.result


@pytest.fixture(autouse=True)
def fake_msal(monkeypatch):
    FakeMsal.result = {"access_token": "tok"}
    monkeypatch.setattr(foundry.msal, "ConfidentialClientApplication", FakeMsal)


def client(handler):
    return foundry.FoundryClient(SETTINGS, transport=httpx.MockTransport(handler))


def respond(body, status=200):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=body)

    return handler, seen


async def call(c, auth="obo"):
    return await c.call_orchestrator(auth, "u", "oid-1", "delegate", "tid", None)


def completed(text):
    return {"status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


async def test_completed_reply():
    handler, _ = respond(completed(json.dumps({"agent": "orch"})))
    assert await call(client(handler)) == ({"agent": "orch"}, None)


async def test_non_dict_body_is_unparseable():
    handler, _ = respond([])
    assert await call(client(handler)) == (None, "unparseable_output")


async def test_non_dict_reply_is_unparseable():
    handler, _ = respond(completed("[1]"))
    assert await call(client(handler)) == (None, "unparseable_output")


async def test_odd_output_shapes_do_not_raise():
    handler, _ = respond({"status": "completed", "output": ["x", {"type": "message", "content": "s"},
                                                            {"type": "message", "content": [3]}]})
    assert await call(client(handler)) == (None, "unparseable_output")


async def test_failed_with_string_error():
    handler, _ = respond({"status": "failed", "error": "boom"})
    assert await call(client(handler)) == (None, "boom")


async def test_msal_error_with_empty_codes():
    FakeMsal.result = {"error": "invalid_grant", "error_codes": []}
    assert await call(client(respond({})[0])) == (None, "invalid_grant")


async def test_msal_numeric_code():
    FakeMsal.result = {"error_codes": [65001]}
    assert await call(client(respond({})[0])) == (None, "AADSTS65001")


async def test_msal_exception_is_caught(monkeypatch):
    class Boom(FakeMsal):
        def acquire_token_for_client(self, scope):
            raise RuntimeError("x")

    monkeypatch.setattr(foundry.msal, "ConfidentialClientApplication", Boom)
    assert await call(client(respond({})[0]), "sp") == (None, "token: RuntimeError")


async def test_transport_timeout():
    def handler(request):
        raise httpx.ConnectTimeout("t")

    assert await call(client(handler)) == (None, "transport: ConnectTimeout")


async def test_sp_sends_user_identity_header():
    handler, seen = respond(completed("{}"))
    await call(client(handler), "sp")
    assert seen[0].headers["x-ms-user-identity"] == "oid-1"
    handler, seen = respond(completed("{}"))
    await call(client(handler), "obo")
    assert "x-ms-user-identity" not in seen[0].headers
