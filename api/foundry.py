"""Tokens and the Responses call to the Orchestrator. Never log token or header values."""

import json
import logging
from urllib.parse import quote

import httpx
import msal

log = logging.getLogger("lab-api")
SCOPE = ["https://ai.azure.com/.default"]


SHOW = ("x-ms-user-identity", "x-agent-user-id", "x-agent-foundry-call-id", "traceparent", "x-ms-client-request-id")


def summarize_headers(items):
    names = {k.lower(): v for k, v in items}
    return {"names": sorted(names), "values": {h: names.get(h) for h in SHOW}}


def output_text(payload):
    out = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                out.append(str(part.get("text", "")))
    return "".join(out)


class FoundryClient:
    def __init__(self, settings, transport=None):
        self.sent_headers = None  # shortcut: last outbound header summary, read by the API right after the call
        self.settings = settings
        self.transport = transport
        self.msal = msal.ConfidentialClientApplication(
            settings["LAB_API_CLIENT_ID"],
            client_credential=settings["LAB_API_CLIENT_SECRET"],
            authority="https://login.microsoftonline.com/" + settings["LAB_TENANT_ID"],
        )

    def token(self, auth, user_token):
        if auth == "obo":
            result = self.msal.acquire_token_on_behalf_of(user_token, SCOPE)
        else:
            result = self.msal.acquire_token_for_client(SCOPE)
        if "access_token" not in result:
            return None, (result.get("error_codes") or [result.get("error", "token_error")])[0]
        return result["access_token"], None

    async def capture_sent(self, request):
        """Record the identity/correlation headers that actually left (values on purpose; never authorization)."""
        self.sent_headers = summarize_headers(request.headers.items())
        log.info("orchestrator call sent headers %s", json.dumps(self.sent_headers))

    def url(self):
        return (
            self.settings["LAB_PROJECT_ENDPOINT"].rstrip("/")
            + "/agents/" + quote(self.settings["LAB_ORCHESTRATOR_NAME"], safe="")
            + "/endpoint/protocols/openai/responses?api-version=v1"
        )

    async def call_orchestrator(self, auth, user_token, oid, action, test_id, question):
        try:
            token, error = self.token(auth, user_token)
            if error:
                return None, "AADSTS" + str(error) if str(error).isdigit() else str(error)
            url = self.url()
        except Exception as e:
            return None, "token: " + type(e).__name__
        headers = {"Authorization": "Bearer " + token}
        if auth == "sp":
            headers["x-ms-user-identity"] = oid
        body = {
            "input": json.dumps({"action": action, "testId": test_id, "payloadOid": oid, "question": question}),
            "stream": False, "store": False, "background": False,
        }
        try:
            async with httpx.AsyncClient(timeout=180, transport=self.transport,
                                         event_hooks={"request": [self.capture_sent]}) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as e:
            log.info("orchestrator auth=%s transport error %s", auth, type(e).__name__)
            return None, "transport: " + type(e).__name__
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        malformed = not isinstance(payload, dict)
        if malformed:
            payload = {}
        log.info("orchestrator auth=%s http=%s status=%s", auth, response.status_code, payload.get("status"))
        if response.status_code != 200:
            return None, "http_%d" % response.status_code
        if malformed:
            return None, "unparseable_output"
        if payload.get("status") != "completed":
            err = payload.get("error")
            code = err.get("code") if isinstance(err, dict) else err
            return None, str(code) if code else "status_" + str(payload.get("status"))
        try:
            reply = json.loads(output_text(payload))
        except ValueError:
            return None, "unparseable_output"
        return (reply, None) if isinstance(reply, dict) else (None, "unparseable_output")
