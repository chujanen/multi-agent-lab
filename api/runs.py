"""Pure run-record construction and pass rules. The API only reports values; this decides."""

from collections import Counter
from datetime import datetime, timezone

AUTH = {"jwt": None, "obo": "obo", "sp": "sp", "model": "obo"}
ACTION = {"obo": "delegate", "sp": "delegate", "model": "ask"}
HOP_AUTH_DOWNSTREAM = "workload+x-ms-user-identity"


def hop(src, dst, auth, trusted, oid, call_id, payload_oid, ok, error=None, sent=None, received=None):
    return {
        "from": src, "to": dst, "auth": auth, "trustedUserId": trusted,
        "userMatch": trusted is not None and trusted == oid,
        "callId": call_id, "payloadOid": payload_oid, "ok": ok, "error": error,
        "sentHeaders": (sent or {}).get("values"), "receivedHeaders": (received or {}).get("values"),
    }


def specialist_error(orchestrator, names, test_id, orchestrator_name):
    if orchestrator.get("agent") != orchestrator_name:
        return "orchestrator reply from %r, expected %r" % (orchestrator.get("agent"), orchestrator_name)
    if orchestrator.get("testId") != test_id:
        return "orchestrator testId %r does not match this run" % orchestrator.get("testId")
    hops = orchestrator.get("hops") or []
    counts = Counter(h.get("agent") for h in hops)
    for name in names:
        if counts[name] != 1:
            return "%s called %d times, expected 1" % (name, counts[name])
    extras = [a for a in counts if a not in names]
    if extras:
        return "unexpected specialist %r in hops" % extras[0]
    for h in hops:
        if h.get("testId") != test_id or not h.get("ok"):
            return "%s returned testId %r ok=%s error=%s" % (h.get("agent"), h.get("testId"), h.get("ok"), h.get("error"))
    return None


def build_run(test, run_id, test_id, user, orchestrator, orchestrator_error, auth, names, orchestrator_name,
              sent_headers=None):
    oid = user["oid"]
    hops = [hop("web", "api", "jwt", oid, oid, None, oid, True)]
    answer, error = None, None
    if test != "jwt":
        if orchestrator is None:
            hops.append(hop("api", orchestrator_name, auth, None, oid, None, oid, False, orchestrator_error,
                            sent=sent_headers))
            error = orchestrator_error
        else:
            hops.append(hop("api", orchestrator_name, auth, orchestrator.get("trustedUserId"), oid,
                            orchestrator.get("callId"), orchestrator.get("payloadOid"), True,
                            sent=sent_headers, received=orchestrator.get("inboundHeaders")))
            for h in orchestrator.get("hops") or []:
                hop_auth = HOP_AUTH_DOWNSTREAM if h.get("forwardedUser") else "workload"
                hops.append(hop(orchestrator_name, h.get("agent"), hop_auth, h.get("trustedUserId"), oid,
                                h.get("callId"), h.get("payloadOid"), bool(h.get("ok")), h.get("error"),
                                sent=h.get("sentHeaders"), received=h.get("inboundHeaders")))
            answer = orchestrator.get("answer")
            error = orchestrator.get("error") or specialist_error(orchestrator, names, test_id, orchestrator_name)
    ok = error is None and all(h["ok"] and h["userMatch"] for h in hops)
    return {
        "id": run_id, "test": test, "utc": datetime.now(timezone.utc).isoformat(),
        "user": user, "ok": ok, "hops": hops, "answer": answer, "error": error,
    }
