from types import SimpleNamespace

import pytest

import specialist


def platform(user_id=None, call_id="call-1"):
    return SimpleNamespace(user_id=user_id, call_id=call_id)


def test_reports_trusted_user_value_and_call_id(monkeypatch):
    monkeypatch.setenv("LAB_SPECIALIST_NAME", "lab-finance")
    reply = specialist.build_reply(
        {"action": "echo", "testId": "t1", "payloadOid": "oid-1"},
        platform(user_id="user-9"),
    )
    assert reply == {
        "agent": "lab-finance",
        "testId": "t1",
        "trustedUserId": "user-9",
        "callId": "call-1",
        "payloadOid": "oid-1",
        "fact": specialist.FACTS["lab-finance"],
    }


def test_missing_identity_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("LAB_SPECIALIST_NAME", "lab-risk")
    reply = specialist.build_reply({"action": "echo", "testId": "t2"}, platform(call_id=None))
    assert reply["trustedUserId"] is None
    assert reply["callId"] is None
    assert reply["payloadOid"] is None
    assert reply["fact"] == specialist.FACTS["lab-risk"]


def test_rejects_unknown_action(monkeypatch):
    monkeypatch.setenv("LAB_SPECIALIST_NAME", "lab-risk")
    with pytest.raises(ValueError):
        specialist.build_reply({"action": "remember", "testId": "t3"}, platform())
