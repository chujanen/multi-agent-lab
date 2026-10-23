"""Header capture for the lab: inbound (ASGI middleware) and outbound (httpx hook).

This is a test lab, so the identity and correlation header VALUES are recorded on purpose; only
`authorization` is never captured. Values go into the agent replies (hence the run file) and one log
line per request, which lands in App Insights under the request's trace.
"""

import json
import logging
from contextvars import ContextVar

log = logging.getLogger("inbound-headers")
SHOW = ("x-ms-user-identity", "x-agent-user-id", "x-agent-foundry-call-id", "traceparent",
        "x-agent-session-id", "x-agent-response-id", "x-ms-client-request-id")
inbound: ContextVar = ContextVar("inbound_headers", default=None)
outbound: ContextVar = ContextVar("outbound_headers", default=None)


def summarize(headers):
    """headers: iterable of (name, value) pairs, bytes or str. Values only for SHOW; never authorization."""
    names = {(k.decode() if isinstance(k, bytes) else k).lower(): (v.decode() if isinstance(v, bytes) else v)
             for k, v in headers}
    return {"names": sorted(names), "values": {h: names.get(h) for h in SHOW}}


class InboundHeaderProbe:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            inbound.set(summarize(scope.get("headers", [])))
        await self.app(scope, receive, send)


def log_inbound():
    """Call from the handler: the SDK's trace context is active there, so the line joins the operation."""
    summary = inbound.get()
    log.info("inbound headers %s", json.dumps(summary))
    return summary


async def outbound_hook(request):
    """httpx request event hook: records what actually left, including the injected traceparent."""
    summary = summarize(request.headers.items())
    outbound.set(summary)
    log.info("outbound headers to %s %s", request.url.path.rsplit("/endpoint", 1)[0].rsplit("/", 1)[-1], json.dumps(summary))
