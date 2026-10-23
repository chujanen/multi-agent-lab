# Foundry multi-agent identity lab

One signed-in user, one JWT-protected API, one Foundry hosted Orchestrator, two
Foundry hosted Specialists. Every hop records which user identity arrived.
Design: `docs/superpowers/specs/2026-10-21-multi-agent-lab-design.md`.

## Setup (once)

```sh
python3.12 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
az login
ops/setup_azure.sh app --apply      # creates/finds the app and writes LAB_API_CLIENT_ID and LAB_API_CLIENT_SECRET into .env
.venv/bin/python ops/deploy.py finance --apply
.venv/bin/python ops/deploy.py risk --apply
.venv/bin/python ops/deploy.py orchestrator --apply
ops/setup_azure.sh rbac --apply     # assigns roles; needs the orchestrator identity from the deploy
```

Role assignments take a few minutes to propagate. If admin-consent or a role assignment fails right after creation, wait ~5 minutes and rerun; both stages are idempotent. If you redeploy the orchestrator and its identity changes, rerun `ops/setup_azure.sh rbac --apply`.

## Run

```sh
.venv/bin/uvicorn --factory --app-dir api main:app_from_env --port 5400
```

Open http://localhost:5400/, sign in, click a test:

| test | proves |
|---|---|
| `jwt` | the browser token is a delegated v2 token for this API |
| `obo` | API → Orchestrator on-behalf-of the user, Orchestrator → both Specialists |
| `sp` | API as its own service principal with `x-ms-user-identity`, then the same |
| `model` | `LAB_MODEL_DEPLOYMENT` picks both Specialists through tools; costs tokens |

A run is green only when every hop reports the signed-in user's `oid` as the
trusted user ID. As of 2026-10-21 the Orchestrator → Specialist hops are
expected red: the Foundry ingress drops `x-ms-user-identity` on hosted-to-hosted calls, so no trusted user ID reaches a Specialist.
Rerun `obo` now and then to see whether that changed.

Run files: `runs/<utc>-<test>-<8hex>.json`. Tokens and header values are never stored.
The API binds to localhost only and `GET /runs` needs no sign-in, so do not expose the port.

## Trusted user ID format

The previous lab logged only whether a trusted user ID was present. This lab
stores the value. Confirmed 2026-10-22 on the first live probe: the Orchestrator's
`trustedUserId` is the caller's bare Entra `oid`, so `userMatch` compares equal
strings. The Specialists received a call ID but no trusted user ID with the
header forwarded (the known hosted-to-hosted gap).

## Tracing (Application Insights)

Set `APPLICATIONINSIGHTS_CONNECTION_STRING` in `.env` (an App Insights resource
in the same resource group as the Foundry account) and
redeploy the agents; `ops/deploy.py` passes it to every agent and the API picks it
up at start. One run is one distributed trace: API request → Orchestrator →
both Specialists, linked by `traceparent` (the Orchestrator enables outbound
httpx instrumentation for this). Confirmed 2026-10-22. The API stamps its
`POST /runs` span with `lab.run_id` and `lab.test`.

```sh
az monitor app-insights query --app <app-insights-name> \
  -g "$LAB_RESOURCE_GROUP" --analytics-query "
union requests, dependencies
| where timestamp > ago(1h)
| project timestamp, itemType, cloud_RoleName, name, operation_Id, resultCode, customDimensions
| order by timestamp asc"
```

Filter on `operation_Id` to see one run. Each agent's own logs and outbound
calls carry its name as `cloud_RoleName` (`OTEL_SERVICE_NAME`, set by
`ops/deploy.py`). The `invoke_agent` request rows under role `agentsv2` are
emitted by the Foundry platform, not the containers; the agent they belong to
is in `customDimensions["agent.name"]`, and `traces` from the same operation
show the container's log lines per agent.

What the platform spans say about identity (seen 2026-10-22, run
`20261022T040744Z-sp`): `user.id` on each `invoke_agent` span is the
*authenticated caller's principal*, not the delegated user. For an `sp` run it is
the API service principal on the Orchestrator span and the Orchestrator's own
identity on both Specialist spans; the container's `has_user_id=True/False` log
line shows whether a trusted user was delivered. The Specialist spans' parent
IDs point at a platform hop that is not exported, but every span shares the
run's `operation_Id`.

## Inbound headers per hop

Every agent runs `agents/headers.py`: an ASGI middleware that captures the
inbound headers and an httpx hook that captures what each outbound call carried.
Because this is a test lab, the *values* of the identity and correlation headers
(`x-ms-user-identity`, `x-agent-user-id`, `x-agent-foundry-call-id`, `traceparent`,
session/response/client-request ids) are recorded on purpose; `authorization` never
is. The API does the same for its call to the Orchestrator. Each run file hop has
`sentHeaders` and `receivedHeaders`, the page shows them as two columns, and each
agent logs one `inbound headers …` and one `outbound headers …` line per request
into App Insights under the run's trace:

```sh
az monitor app-insights query --app <app-insights-name> \
  -g "$LAB_RESOURCE_GROUP" --analytics-query "
traces | where message startswith 'inbound headers'
| project timestamp, cloud_RoleName, operation_Id, message | order by timestamp desc"
```

Seen 2026-10-22 (agents v6/v7), now with values on both sides of each hop:
- `sp` run, API → Orchestrator: the API sent `x-ms-user-identity=<oid>` with an
  app-only token; the Orchestrator's container received no `x-ms-user-identity`
  but `x-agent-user-id=<same oid>`. The ingress converted the header.
- Orchestrator → each Specialist: the Orchestrator sent `x-ms-user-identity=<oid>`;
  the Specialist received neither header. Same grant, no conversion. This is the
  hosted-to-hosted gap.
- The ingress also rewrites `x-agent-foundry-call-id`: the value the Orchestrator
  sends (its own incoming call id) is not what the Specialist receives; each hop
  gets a fresh `proxy_…` id. `traceparent` is passed through unchanged (the sent
  side shows `–` because the OpenTelemetry httpx instrumentation injects it after
  the hook runs).

## Tests (no Azure calls)

```sh
.venv/bin/pytest
```

## Known gaps

- Hop 2 identity delivery depends on Microsoft; nothing here changes that.
- OBO needs the `https://ai.azure.com` resource app's `user_impersonation`
  permission with admin consent; `ops/setup_azure.sh` looks the app up.
