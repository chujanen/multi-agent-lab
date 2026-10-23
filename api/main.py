"""Lab API: validates the browser's token, runs one test, stores one JSON file per run, serves web/."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import auth
import runs
import telemetry

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = re.compile(r"[0-9TZ]{16}-(jwt|obo|sp|model)-[0-9a-f]{8}")
QUESTION = "What is the current revenue growth, and how concentrated is supplier risk?"


class RunRequest(BaseModel):
    test: str

    @field_validator("test")
    @classmethod
    def known(cls, v):
        if v not in runs.AUTH:
            raise ValueError("unknown test")
        return v


def trace_search_url(settings):
    """App Insights transaction search for the resource; the page appends the trace id as the search term."""
    rid = settings.get("LAB_APPINSIGHTS_RESOURCE_ID")
    if not rid:
        return None
    return "https://portal.azure.com/#@%s/resource%s/searchV1" % (settings["LAB_TENANT_ID"], rid)


def create_app(settings, foundry=None, jwks_client=None):
    app = FastAPI(title="multi-agent lab")
    runs_dir = Path(settings.get("LAB_RUNS_DIR") or ROOT / "runs")
    names = [settings["LAB_FINANCE_NAME"], settings["LAB_RISK_NAME"]]
    if jwks_client is None:  # one instance so Microsoft's key set is cached across requests
        import ssl

        import certifi

        jwks_client = jwt.PyJWKClient(
            "https://login.microsoftonline.com/%s/discovery/v2.0/keys" % settings["LAB_TENANT_ID"],
            ssl_context=ssl.create_default_context(cafile=certifi.where()),  # python.org builds lack urllib CAs
        )
    if foundry is None:
        from foundry import FoundryClient

        foundry = FoundryClient(settings)

    def current_user(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "bearer token required")
        try:
            token = authorization[7:]
            return token, auth.validate_token(
                token, tenant_id=settings["LAB_TENANT_ID"],
                client_id=settings["LAB_API_CLIENT_ID"], jwks_client=jwks_client,
            )
        except auth.AuthError as e:
            raise HTTPException(401, str(e))

    @app.get("/config")
    def config():
        return {"tenantId": settings["LAB_TENANT_ID"], "webClientId": settings["LAB_WEB_CLIENT_ID"],
                "apiScope": "api://%s/access" % settings["LAB_API_CLIENT_ID"],
                "traceSearchUrl": trace_search_url(settings)}

    @app.post("/runs")
    async def start(body: RunRequest, principal=Depends(current_user)):
        token, user = principal
        test_id = uuid4().hex
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + body.test + "-" + test_id[:8]
        telemetry.tag_run(run_id, body.test)
        reply, error = None, None
        if body.test != "jwt":
            reply, error = await foundry.call_orchestrator(
                runs.AUTH[body.test], token, user["oid"], runs.ACTION[body.test], test_id,
                QUESTION if body.test == "model" else None,
            )
        run = runs.build_run(body.test, run_id, test_id, user, reply, error, runs.AUTH[body.test], names,
                             settings["LAB_ORCHESTRATOR_NAME"], sent_headers=getattr(foundry, "sent_headers", None))
        run["traceId"] = telemetry.current_trace_id()
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / (run_id + ".json")).write_text(json.dumps(run, indent=2))
        return run

    @app.get("/runs")
    def list_runs():
        out = []
        for path in sorted(runs_dir.glob("*.json"), reverse=True):
            run = json.loads(path.read_text())
            out.append({k: run.get(k) for k in ("id", "test", "utc", "user", "ok", "traceId")})
        return out

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        path = runs_dir / (run_id + ".json")
        if not RUN_ID.fullmatch(run_id) or not path.exists():
            raise HTTPException(404, "no such run")
        return json.loads(path.read_text())

    app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
    return app


def app_from_env():
    """uvicorn factory. Importing this module never builds the real MSAL client (tests inject fakes)."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    app = create_app({k: v for k, v in os.environ.items() if k.startswith("LAB_")})
    telemetry.setup(app, os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"))
    return app
