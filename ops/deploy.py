"""Package one agent role from agents/ and create the hosted agent (or a new version) in Foundry.

Without --apply: builds the ZIP and prints the request. With --apply: uploads it. User-run.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = {"PYTHON_DOTENV_DISABLED": "1", "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false"}
SOURCES = {
    "orchestrator": ["orchestrator.py", "ask.py", "headers.py"],
    "finance": ["specialist.py", "headers.py"],
    "risk": ["specialist.py", "headers.py"],
}


def agent_name(role, env):
    return env["LAB_" + role.upper() + "_NAME"]


AI_KEY = "APPLICATIONINSIGHTS_CONNECTION_STRING"


def agent_env(role, env):
    out = dict(BASE_ENV)
    if env.get(AI_KEY):  # the agentserver SDK exports traces to Azure Monitor when this is set
        out[AI_KEY] = env[AI_KEY]
        out["OTEL_SERVICE_NAME"] = agent_name(role, env)  # distinct cloud_RoleName per agent in App Insights
    if role == "orchestrator":
        out.update(
            LAB_ROLE="orchestrator",
            LAB_PROJECT_ENDPOINT=env["LAB_PROJECT_ENDPOINT"],
            LAB_MODEL_DEPLOYMENT=env["LAB_MODEL_DEPLOYMENT"],
            LAB_FINANCE_NAME=env["LAB_FINANCE_NAME"],
            LAB_RISK_NAME=env["LAB_RISK_NAME"],
        )
    else:
        out.update(LAB_ROLE="specialist", LAB_SPECIALIST_NAME=agent_name(role, env))
    return out


def build(role, env):
    dest = ROOT / "dist" / role
    app = dest / "app"
    # keep created.json/status.json from a previous deploy
    shutil.rmtree(app, ignore_errors=True)
    for stale in ("agent.zip", "request.json"):
        (dest / stale).unlink(missing_ok=True)
    app.mkdir(parents=True)
    for name in SOURCES[role] + ["zip_main.py", "requirements.txt", "constraints.txt"]:
        shutil.copy2(ROOT / "agents" / name, app / name)
    archive = dest / "agent.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(app))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    body = {
        "name": agent_name(role, env),
        "description": "multi-agent lab: " + role,
        "definition": {
            "kind": "hosted",
            "cpu": "2",
            "memory": "4Gi",
            "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}],
            "environment_variables": agent_env(role, env),
            "code_configuration": {
                "runtime": "python_3_13",
                "entry_point": ["python", "zip_main.py"],
                "dependency_resolution": "remote_build",
            },
        },
        "metadata": {"deploymentMethod": "zip", "zipPackageMode": "remote", "zip_sha256": digest},
    }
    shown = json.loads(json.dumps(body))
    if AI_KEY in shown["definition"]["environment_variables"]:
        shown["definition"]["environment_variables"][AI_KEY] = "<from .env>"
    (dest / "request.json").write_text(json.dumps(shown, indent=2))
    return archive, digest, body


async def apply(role, env, archive, digest, body):
    from azure.identity.aio import AzureCliCredential

    url = env["LAB_PROJECT_ENDPOINT"].rstrip("/") + "/agents/" + agent_name(role, env)
    async with AzureCliCredential() as cred, httpx.AsyncClient(timeout=300) as client:
        token = (await cred.get_token("https://ai.azure.com/.default")).token
        headers = {"Authorization": "Bearer " + token}
        # New agent: POST /agents with name in the body. Existing agent: POST /agents/{name} adds a version.
        exists = (await client.get(url + "?api-version=v1", headers=headers)).status_code == 200
        r = await client.post(
            (url if exists else url.rsplit("/", 1)[0]) + "?api-version=v1",
            headers={**headers, "x-ms-code-zip-sha256": digest, "x-ms-agent-name": agent_name(role, env)},
            files={"metadata": (None, json.dumps(body), "application/json"),
                   "code": ("agent.zip", archive.read_bytes(), "application/zip")},
        )
        print("POST", url, "HTTP", r.status_code)
        if r.status_code >= 400:
            print(r.text[:3000])
        r.raise_for_status()
        created = r.json()
        (archive.parent / "created.json").write_text(json.dumps(created, indent=2))
        version = created.get("version") or created.get("versions", {}).get("latest", {}).get("version")
        for _ in range(60):
            s = await client.get(url + "/versions/" + str(version) + "?api-version=v1", headers=headers)
            s.raise_for_status()
            status = s.json()
            (archive.parent / "status.json").write_text(json.dumps(status, indent=2))
            print("version", version, "status", status.get("status"), "identity",
                  (status.get("instance_identity") or {}).get("principal_id"))
            if status.get("status") in ("active", "failed"):
                break
            await asyncio.sleep(10)
        if status.get("status") != "active":
            raise SystemExit("version %s ended in status %r; see %s" % (version, status.get("status"), archive.parent / "status.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=sorted(SOURCES))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    env = {k: v for k, v in os.environ.items() if k.startswith("LAB_") or k == AI_KEY}
    archive, digest, body = build(args.role, env)
    print("built", archive, "sha256", digest)
    if args.apply:
        asyncio.run(apply(args.role, env, archive, digest, body))
    else:
        print((archive.parent / "request.json").read_text())


if __name__ == "__main__":
    main()
