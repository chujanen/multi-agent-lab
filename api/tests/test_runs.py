import pytest

import runs

USER = {"oid": "oid-1", "upn": "u@example.com", "name": "U"}
NAMES = ["lab-finance", "lab-risk"]


def specialist(name, trusted="oid-1", ok=True, test_id="t1"):
    return {"agent": name, "testId": test_id, "trustedUserId": trusted, "callId": True,
            "payloadOid": "oid-1", "fact": "f", "ok": ok, "error": None if ok else "server_error",
            "forwardedUser": True}


def orchestrator(hops, trusted="oid-1", answer=None):
    return {"agent": "lab-orchestrator", "testId": "t1", "trustedUserId": trusted,
            "callId": True, "payloadOid": "oid-1", "hops": hops, "answer": answer}


def build(test, orch=None, error=None):
    return runs.build_run(test, "20261021T120000Z-" + test + "-abcd1234", "t1", USER, orch, error,
                          runs.AUTH[test], NAMES, "lab-orchestrator")


def test_jwt_run_has_one_green_hop():
    run = build("jwt")
    assert run["ok"] is True
    assert run["hops"] == [{"from": "web", "to": "api", "auth": "jwt", "trustedUserId": "oid-1",
                            "userMatch": True, "callId": None, "payloadOid": "oid-1", "ok": True, "error": None,
                            "sentHeaders": None, "receivedHeaders": None}]


def test_obo_run_green_when_every_hop_matches():
    run = build("obo", orchestrator([specialist(n) for n in NAMES]))
    assert [h["to"] for h in run["hops"]] == ["api", "lab-orchestrator", *NAMES]
    assert run["hops"][1]["auth"] == "obo"
    assert run["hops"][2]["auth"] == "workload+x-ms-user-identity"
    assert all(h["userMatch"] for h in run["hops"])
    assert run["ok"] is True and run["error"] is None


def test_specialist_hop_without_forwarded_user_is_labelled_workload():
    hops = [{**specialist(NAMES[0]), "forwardedUser": False}, specialist(NAMES[1])]
    run = build("obo", orchestrator(hops))
    assert run["hops"][2]["auth"] == "workload"
    assert run["hops"][3]["auth"] == "workload+x-ms-user-identity"


def test_orchestrator_reply_error_makes_run_red():
    run = build("model", {**orchestrator([]), "error": "ask: RuntimeError"})
    assert run["error"] == "ask: RuntimeError" and run["ok"] is False


def test_missing_trusted_user_on_specialist_is_red_but_hop_ok():
    run = build("sp", orchestrator([specialist(NAMES[0], trusted=None), specialist(NAMES[1])]))
    assert run["hops"][2]["userMatch"] is False and run["hops"][2]["ok"] is True
    assert run["ok"] is False


def test_other_users_id_is_a_mismatch():
    run = build("obo", orchestrator([specialist(NAMES[0], trusted="someone-else"), specialist(NAMES[1])]))
    assert run["hops"][2]["userMatch"] is False and run["ok"] is False


def test_orchestrator_error_is_recorded_on_hop_two():
    run = build("obo", None, "AADSTS65001")
    assert run["hops"][1]["ok"] is False and run["hops"][1]["error"] == "AADSTS65001"
    assert run["ok"] is False and run["error"] == "AADSTS65001"


@pytest.mark.parametrize("test", ["obo", "sp", "model"])
def test_remote_runs_require_both_specialists_once(test):
    both = orchestrator([specialist(n) for n in NAMES], answer="A")
    assert build(test, both)["ok"] is True
    assert build(test, both)["answer"] == "A"
    assert build(test, orchestrator([]))["ok"] is False
    missing = build(test, orchestrator([specialist(NAMES[0])], answer="A"))
    assert missing["ok"] is False and "lab-risk" in missing["error"]
    dup = build(test, orchestrator([specialist(NAMES[0]), specialist(NAMES[0]), specialist(NAMES[1])], answer="A"))
    assert dup["ok"] is False and "lab-finance" in dup["error"]
    wrong_id = build(test, orchestrator([specialist(NAMES[0], test_id="other"), specialist(NAMES[1])], answer="A"))
    assert wrong_id["ok"] is False and "lab-finance" in wrong_id["error"]
    failed = build(test, orchestrator([specialist(NAMES[0], ok=False), specialist(NAMES[1])]))
    assert failed["ok"] is False and "lab-finance" in failed["error"]


def test_orchestrator_identity_and_correlation_are_checked():
    hops = [specialist(n) for n in NAMES]
    wrong_name = orchestrator(hops)
    wrong_name["agent"] = "other-supervisor"
    run = build("obo", wrong_name)
    assert run["ok"] is False and "other-supervisor" in run["error"]
    stale = orchestrator(hops)
    stale["testId"] = "old"
    run = build("obo", stale)
    assert run["ok"] is False and "testId" in run["error"]


def test_run_never_contains_header_or_token_keys():
    run = build("obo", orchestrator([specialist(n) for n in NAMES]))
    assert "forwardedUser" not in run["hops"][2]
    assert set(run) == {"id", "test", "utc", "user", "ok", "hops", "answer", "error"}


def test_unexpected_specialist_hop_is_red():
    extra = orchestrator([specialist(NAMES[0]), specialist(NAMES[1]), specialist("lab-other")], answer="A")
    run = build("obo", extra)
    assert run["ok"] is False and "lab-other" in run["error"]


def test_specialist_hop_without_agent_name_is_red():
    unnamed = orchestrator([specialist(NAMES[0]), specialist(NAMES[1]), specialist(None)], answer="A")
    assert build("obo", unnamed)["ok"] is False


def test_null_hops_is_red_not_a_crash():
    run = build("obo", orchestrator(None))
    assert run["ok"] is False and "lab-finance" in run["error"]


def test_sent_and_received_header_values_land_on_hops():
    sent_api = {"names": ["authorization", "x-ms-user-identity"], "values": {"x-ms-user-identity": "oid-1", "x-agent-user-id": None}}
    orch = orchestrator([specialist(n) for n in NAMES])
    orch["inboundHeaders"] = {"names": ["x-agent-user-id"], "values": {"x-agent-user-id": "oid-1", "x-ms-user-identity": None}}
    orch["hops"][0]["sentHeaders"] = {"names": [], "values": {"x-ms-user-identity": "oid-1"}}
    orch["hops"][0]["inboundHeaders"] = {"names": [], "values": {"x-ms-user-identity": None, "x-agent-user-id": None}}
    run = runs.build_run("sp", "20261022T000000Z-sp-abcd1234", "t1", USER, orch, None, "sp", NAMES, "lab-orchestrator",
                         sent_headers=sent_api)
    assert run["hops"][1]["sentHeaders"]["x-ms-user-identity"] == "oid-1"
    assert run["hops"][1]["receivedHeaders"]["x-agent-user-id"] == "oid-1"
    assert run["hops"][2]["sentHeaders"]["x-ms-user-identity"] == "oid-1"
    assert run["hops"][2]["receivedHeaders"]["x-ms-user-identity"] is None
    assert "authorization" not in str(run)
