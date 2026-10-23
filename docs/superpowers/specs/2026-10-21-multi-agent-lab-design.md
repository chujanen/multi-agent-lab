# Multi-agent lab: design

Date: 2026-10-21. Folder: repo root.

## Purpose

A small, repeatable proof of concept that follows one signed-in user's identity
through every hop of a multi-agent chain on Microsoft Foundry:

```
browser (MSAL sign-in)
  │ JWT for the lab API
  ▼
lab API (FastAPI, validates JWT)
  │ (a) on-behalf-of token for https://ai.azure.com   (b) API service principal + x-ms-user-identity
  ▼
lab-orchestrator   (Foundry hosted agent)
  │ workload token + x-agent-foundry-call-id + x-ms-user-identity
  ├─▶ lab-finance   (Foundry hosted agent, echo)
  └─▶ lab-risk      (Foundry hosted agent, echo)
```

Every hop records the **trusted user ID** Foundry delivered and whether it
equals the `oid` the API validated from the browser's token, so the results
table shows the day the hosted-agent-to-hosted-agent hop starts delivering the
*same* user. Presence alone is not success: a different user's ID or the
workload principal's ID must show as a mismatch. Today that hop is expected red: the
Foundry ingress drops `x-ms-user-identity` on hosted-to-hosted calls. The user's `oid`
is also carried in the request payload as a fallback column so the rest of the
chain is exercised even while hop 2 is red.

Not in scope: the earlier connectivity-lab audit machinery, session
continuity, recovery/retry scenarios, OBO inside the Orchestrator, Graph tokens,
local docker profiles, a database.

## Reused from the existing setup

| Item | Reused how |
|---|---|
| Existing Foundry project and model deployment (`LAB_PROJECT_ENDPOINT`, `LAB_MODEL_DEPLOYMENT`) | Same project; three new agents named `lab-*`. Old agents from earlier labs are untouched. |
| Custom role `Lab User Delegation` (`UserIdentityImpersonation/action`) | Created by `ops/setup_azure.sh rbac` if missing, then assigned to the new principals. |
| `compatibility/protocol_specialist.py` | Basis of the specialists: `ResponsesAgentServerHost` echo that completes even without a trusted user ID. |
| `lab.py` `make_supervisor`, `specialist_headers`, `raw_specialist_probe` | Basis of the Orchestrator's direct specialist call and header handling. |
| `ops/deploy_supervisor.py`, `ops/specialist_compatibility.py`, `zip_main.py`, `requirements.txt`/`constraints.txt` | Basis of one generic `ops/deploy.py` (source ZIP, remote build, REST `POST /agents/{name}?api-version=v1`). |
| Existing SPA app registration (`LAB_WEB_CLIENT_ID`) | Browser sign-in client; gets one more redirect URI for the lab page. |

## Layout

```
multi-agent-lab/
  README.md                 setup, what to run, what each test proves
  .env.example              LAB_* and API settings; .env is gitignored
  agents/
    orchestrator.py         lab-orchestrator
    specialist.py           lab-finance and lab-risk (LAB_SPECIALIST_NAME picks the facts)
    zip_main.py             Foundry entry point (same as existing lab)
    requirements.txt, constraints.txt
  api/
    main.py                 FastAPI: JWT check, run tests, read runs, serve web/
    foundry.py              token acquisition (OBO, client credentials) and Responses calls
    tests/test_api.py
  web/
    index.html              MSAL.js (CDN) sign-in, test buttons, runs list, hop table
  ops/
    setup_azure.sh          az commands: API app registration, permissions, role assignments
    deploy.py               package one agent folder and create a new hosted version
  runs/                     one JSON file per run (gitignored except .gitkeep)
  docs/superpowers/specs/   this file
```

## Components

### Specialists (`agents/specialist.py`)

`ResponsesAgentServerHost` with one `response_handler`. Input is JSON
`{"action":"echo","testId":"…","payloadOid":"…"}`. Output is JSON:

```json
{"agent":"lab-finance","testId":"…","trustedUserId":null,"callId":true,
 "payloadOid":"…","fact":"revenue 100 -> 120 (+20%)"}
```

`trustedUserId` is the value of `get_request_context().user_id` (`null` when
absent) and `callId` its presence. The previous lab only ever logged presence,
so the ID's format is unverified; recording the value is what lets the API
compare it with the originating `oid`. `LAB_SPECIALIST_NAME` selects the agent
name and the synthetic fact (finance or risk). The handler never raises on
missing identity; it reports.

### Orchestrator (`agents/orchestrator.py`)

Same host type. Input JSON with `action`:

- `delegate`: call both specialists directly, no model. Returns
  `{"agent":"lab-orchestrator","trustedUserId":…,"callId":…,"payloadOid":…,"hops":[<specialist outputs>]}`.
- `ask`: build an `agent_framework` `Agent` on `FoundryChatClient` (`LAB_MODEL_DEPLOYMENT`)
  with two tools, `financial_snapshot` and `risk_snapshot`, each of which makes the
  same direct specialist call and **appends the parsed specialist output to a
  per-request list in application code**. Returns the same shape plus `answer`;
  `hops` is that captured list, never derived from model text. Copied from
  `compatibility/model_orchestrator.py` with the audit parts removed.

Direct specialist call (from `raw_specialist_probe`): `POST
{LAB_PROJECT_ENDPOINT}/agents/{name}/endpoint/protocols/openai/responses?api-version=v1`
with a `DefaultAzureCredential` token for `https://ai.azure.com/.default`,
`x-agent-foundry-call-id` from the request context, and `x-ms-user-identity`
set to the context user ID **only when present** (otherwise the hop records
`forwardedUser:false` rather than failing). `payloadOid` is passed through in the
body. A specialist HTTP or `status: failed` result becomes a hop with `ok:false`
and the error code; the Orchestrator still returns 200 so the API can record it.

### API (`api/main.py`, `api/foundry.py`)

FastAPI on `:5400`, also serving `web/` at `/`. Settings from `.env`:
`LAB_TENANT_ID`, `LAB_API_CLIENT_ID`, `LAB_API_CLIENT_SECRET`, `LAB_API_AUDIENCE`
(`api://lab-api`), `LAB_WEB_CLIENT_ID` (browser SPA app),
`LAB_PROJECT_ENDPOINT`, `LAB_ORCHESTRATOR_NAME`.

- `GET /config` → `{tenantId, webClientId, apiScope}` for the page.
- `POST /runs {"test": "jwt"|"obo"|"sp"|"model"}` → runs one test, writes
  `runs/<utc>-<test>.json`, returns it.
- `GET /runs` → list of `{id, test, utc, user, ok}`; `GET /runs/{id}` → the file.

Tokens are Entra **v2** throughout: the API app registration sets
`requestedAccessTokenVersion: 2`, its identifier URI is `api://<LAB_API_CLIENT_ID>`
(the GUID form, allowed under identifier-URI restrictions), the page requests
scope `api://<LAB_API_CLIENT_ID>/access`, and the token's `aud` is the GUID.
`LAB_API_AUDIENCE` is therefore the same value as `LAB_API_CLIENT_ID`.

JWT validation with PyJWT's `PyJWKClient` against
`https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys`: signature,
`aud == LAB_API_CLIENT_ID`, `iss == https://login.microsoftonline.com/<tenant>/v2.0`,
`exp`, **and `scp` contains `access`**. The `scp` check is what rejects app-only
tokens, which carry an `oid` but no delegated scope; `tid` plus `oid` alone is
not an authorization check. Claims kept: `oid`, `preferred_username`, `name`.

Tests:

| test | API → Orchestrator auth | Orchestrator action |
|---|---|---|
| `jwt` | none (stops after validation) | – |
| `obo` | MSAL `acquire_token_on_behalf_of` for `https://ai.azure.com/.default` | `delegate` |
| `sp` | client credentials + `x-ms-user-identity: <oid>` | `delegate` |
| `model` | OBO | `ask` with a fixed question that needs both specialists |

The `model` test passes only when the captured `hops` contain exactly one
completed result from `lab-finance` and one from `lab-risk`, each with
the run's `testId` and the expected `agent` value. A run where the model skips a
tool, calls one twice, or a specialist answers with another test's ID is
`ok:false` with `error` naming the missing or mismatched specialist.

Run file shape:

```json
{"id":"20261021T120000Z-obo","test":"obo","utc":"…","user":{"oid":"…","upn":"…"},
 "ok":false,
 "hops":[
  {"from":"web","to":"api","auth":"jwt","trustedUserId":"<oid>","userMatch":true,"callId":null,"payloadOid":"…","ok":true},
  {"from":"api","to":"lab-orchestrator","auth":"obo","trustedUserId":"<oid>","userMatch":true,"callId":true,"payloadOid":"…","ok":true},
  {"from":"lab-orchestrator","to":"lab-finance","auth":"workload+x-ms-user-identity","trustedUserId":null,"userMatch":false,"callId":true,"payloadOid":"…","ok":true},
  {"from":"lab-orchestrator","to":"lab-risk","auth":"workload+x-ms-user-identity","trustedUserId":null,"userMatch":false,"callId":true,"payloadOid":"…","ok":true}
 ],
 "answer":null,"error":null}
```

The API computes `userMatch = (trustedUserId == user.oid)` for every hop; the
agents only report values. `ok` at run level is true only when every hop has
`ok` and `userMatch` true. If the first live run shows Foundry's trusted user ID
in a format other than the bare `oid`, the comparison is adjusted from that
evidence and the rule is recorded in the README.
Tokens, header values and model text other than `answer` are never written.

### Web (`web/index.html`)

One static page: `@azure/msal-browser` from cdnjs, `loginPopup`, then
`acquireTokenSilent` for `api://<LAB_API_CLIENT_ID>/access`. Four buttons, a
runs list, and a hop table for the selected run with green/red cells for
`userMatch`, `callId`, `payloadOid`, `ok`; `trustedUserId` is shown as text.
No build step.

### Ops

`ops/setup_azure.sh` (idempotent `az` commands, user-run) creates:

1. App registration `lab-api` with identifier URI `api://<client-id>`,
   `requestedAccessTokenVersion: 2`, scope `access`, a client secret (printed
   once, pasted into `.env`), and the Azure AI (`https://ai.azure.com`)
   `user_impersonation` delegated permission with admin consent (needed for OBO).
2. Redirect URI `http://localhost:5400/` on the SPA app and pre-authorizes
   it for the `access` scope.
3. Role assignments: the API service principal and the signed-in user get the
   invoke permissions used by the previous lab (Azure AI User at the project plus
   the existing invoke custom role at `lab-orchestrator` scope); the API
   service principal additionally gets the delegation role at the Orchestrator
   scope (for the `sp` test); the Orchestrator instance identity gets invoke and
   delegation at both specialist scopes and Cognitive Services OpenAI User at the
   account (for the `model` test).

`ops/deploy.py <orchestrator|finance|risk> [--apply]` zips `agents/` with the
right `LAB_*` env, prints the definition, and with `--apply` creates the agent or
a new version. Agent instance identities are read back after the first deploy so
step 3 above can run.

## Error handling

- Invalid JWT: 401 with a reason; nothing written.
- OBO or client-credential failure: run written with the `api → orchestrator` hop
  `ok:false` and the AADSTS error code.
- Orchestrator or specialist failure: recorded as above; the run file is always
  written so the UI never loses a result.

## Testing

Offline, run with `pytest`:

- `api/tests/test_api.py`: JWT accepted/rejected using a locally generated RSA key
  and a patched JWKS, including rejection of an app-only token (no `scp`), wrong
  `aud`, and wrong `iss`; `/runs` writes a file and returns the hop list with the
  Foundry client replaced by a fake; `userMatch` false when a hop reports another
  user's ID; `model` run fails when a specialist is missing, duplicated, or
  returns a different `testId`.
- `agents/tests/test_agents.py`: specialist handler reports the trusted user ID
  value from a fake request context; orchestrator `delegate` builds hops from
  fake specialist responses (httpx `MockTransport`), including the
  `status: failed` case; orchestrator `ask` captures tool results in application
  code.

User-run only: `ops/setup_azure.sh`, `ops/deploy.py --apply`, and any run from the
page. `README.md` lists the order.
