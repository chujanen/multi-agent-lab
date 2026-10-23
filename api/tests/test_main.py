import json

import pytest
from fastapi.testclient import TestClient

import main
from conftest import CLIENT, TENANT


class FakeFoundry:
    def __init__(self):
        self.calls = []
        self.reply = None
        self.error = None

    async def call_orchestrator(self, auth, user_token, oid, action, test_id, question):
        self.calls.append({"auth": auth, "oid": oid, "action": action, "question": question})
        return self.reply, self.error


@pytest.fixture
def lab(tmp_path, jwks_client):
    foundry = FakeFoundry()
    settings = {
        "LAB_TENANT_ID": TENANT, "LAB_API_CLIENT_ID": CLIENT, "LAB_WEB_CLIENT_ID": "web",
        "LAB_ORCHESTRATOR_NAME": "lab-orchestrator", "LAB_FINANCE_NAME": "lab-finance",
        "LAB_RISK_NAME": "lab-risk", "LAB_RUNS_DIR": str(tmp_path),
    }
    return TestClient(main.create_app(settings, foundry=foundry, jwks_client=jwks_client)), foundry, tmp_path


def test_config_is_public(lab):
    client, _, _ = lab
    assert client.get("/config").json() == {"tenantId": TENANT, "webClientId": "web",
                                            "apiScope": "api://%s/access" % CLIENT, "traceSearchUrl": None}


def test_runs_require_valid_token(lab, make_token):
    client, _, _ = lab
    assert client.post("/runs", json={"test": "jwt"}).status_code == 401
    bad = client.post("/runs", json={"test": "jwt"}, headers={"Authorization": "Bearer " + make_token(scp=None)})
    assert bad.status_code == 401 and "scope" in bad.json()["detail"]


def test_jwt_run_is_written_and_listed(lab, make_token):
    client, foundry, runs_dir = lab
    r = client.post("/runs", json={"test": "jwt"}, headers={"Authorization": "Bearer " + make_token()})
    assert r.status_code == 200
    run = r.json()
    assert run["ok"] is True and foundry.calls == []
    assert json.loads((runs_dir / (run["id"] + ".json")).read_text()) == run
    listed = client.get("/runs").json()
    assert listed == [{"id": run["id"], "test": "jwt", "utc": run["utc"], "user": run["user"], "ok": True,
                       "traceId": run["traceId"]}]
    assert client.get("/runs/" + run["id"]).json() == run
    assert client.get("/runs/../etc/passwd").status_code in (404, 422)


def test_sp_run_calls_orchestrator_with_oid(lab, make_token):
    client, foundry, _ = lab
    foundry.reply = {"agent": "lab-orchestrator", "testId": "ignored", "trustedUserId": None,
                     "callId": True, "payloadOid": "x", "hops": [], "answer": None}
    r = client.post("/runs", json={"test": "sp"}, headers={"Authorization": "Bearer " + make_token()})
    assert r.status_code == 200
    assert foundry.calls[0]["auth"] == "sp" and foundry.calls[0]["action"] == "delegate"
    assert foundry.calls[0]["oid"] == "33333333-3333-3333-3333-333333333333"
    assert r.json()["hops"][1]["userMatch"] is False
    assert r.json()["ok"] is False and "testId" in r.json()["error"]


def test_two_runs_in_one_second_get_different_files(lab, make_token):
    client, _, runs_dir = lab
    headers = {"Authorization": "Bearer " + make_token()}
    a = client.post("/runs", json={"test": "jwt"}, headers=headers).json()["id"]
    b = client.post("/runs", json={"test": "jwt"}, headers=headers).json()["id"]
    assert a != b and len(list(runs_dir.glob("*.json"))) == 2


def test_model_run_sends_question(lab, make_token):
    client, foundry, _ = lab
    foundry.error = "AADSTS65001"
    r = client.post("/runs", json={"test": "model"}, headers={"Authorization": "Bearer " + make_token()})
    assert foundry.calls[0]["action"] == "ask" and "revenue" in foundry.calls[0]["question"]
    assert r.json()["ok"] is False and r.json()["error"] == "AADSTS65001"


def test_unknown_test_is_rejected(lab, make_token):
    client, _, _ = lab
    r = client.post("/runs", json={"test": "nope"}, headers={"Authorization": "Bearer " + make_token()})
    assert r.status_code == 422


def test_run_carries_trace_id_when_tracing_is_on(lab, make_token):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    client, _, _ = lab
    r = client.post("/runs", json={"test": "jwt"}, headers={"Authorization": "Bearer " + make_token()})
    # the agentserver SDK (imported by the agents tests) may install a global provider; either way the shape holds
    assert r.json()["traceId"] is None or len(r.json()["traceId"]) == 32
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("outer") as span:
        import telemetry

        assert telemetry.current_trace_id() == format(span.get_span_context().trace_id, "032x")
        assert len(telemetry.current_trace_id()) == 32


def test_config_trace_search_url(tmp_path, jwks_client):
    settings = {"LAB_TENANT_ID": TENANT, "LAB_API_CLIENT_ID": CLIENT, "LAB_WEB_CLIENT_ID": "web",
                "LAB_ORCHESTRATOR_NAME": "o", "LAB_FINANCE_NAME": "f", "LAB_RISK_NAME": "r",
                "LAB_RUNS_DIR": str(tmp_path), "LAB_APPINSIGHTS_RESOURCE_ID": "/subscriptions/s/x"}
    app = main.create_app(settings, foundry=FakeFoundry(), jwks_client=jwks_client)
    assert TestClient(app).get("/config").json()["traceSearchUrl"] == (
        "https://portal.azure.com/#@%s/resource/subscriptions/s/x/searchV1" % TENANT)
