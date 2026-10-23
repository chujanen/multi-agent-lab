#!/usr/bin/env bash
# Azure setup for the multi-agent lab. User-run. Prints every command; runs them only with --apply.
#   ops/setup_azure.sh app  [--apply]   app registration (v2 tokens, scope "access", OBO permission), web redirect
#   ops/setup_azure.sh rbac [--apply]   roles for API SP + you on the orchestrator; orchestrator identity on specialists
# Both are idempotent: existing scope IDs, service principals and assignments are reused.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source ./.env; set +a   # ./ : bare "source .env" searches PATH first
STAGE=${1:-}; APPLY=${2:-}
[ "$STAGE" = app ] || [ "$STAGE" = rbac ] || { echo "usage: $0 app|rbac [--apply]"; exit 2; }
run() { echo "+ $*"; if [ "$APPLY" = "--apply" ]; then "$@"; fi; }
set_env() {  # KEY VALUE: update or append the line in .env (also exported for the rest of this run)
  if /usr/bin/grep -q "^$1=" .env; then sed -i '' "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
  export "$1=$2"; echo "= $1 written to .env"
}
assign() {  # principal type role scope
  if [ -z "$(az role assignment list --assignee "$1" --role "$3" --scope "$4" --query '[0].id' -o tsv 2>/dev/null)" ]; then
    run az role assignment create --assignee-object-id "$1" --assignee-principal-type "$2" --role "$3" --scope "$4"
  else echo "= $3 already assigned to $1 at $4"; fi
}

SUB=$(az account show --query id -o tsv)
ACCOUNT="/subscriptions/$SUB/resourceGroups/$LAB_RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/$LAB_FOUNDRY_ACCOUNT"
PROJECT="$ACCOUNT/projects/$LAB_FOUNDRY_PROJECT"

if [ "$STAGE" = app ]; then
  AI_SP=$(az ad sp list --filter "servicePrincipalNames/any(c:c eq 'https://ai.azure.com')" --query '[0].appId' -o tsv)
  [ -n "$AI_SP" ] || { echo "No service principal for https://ai.azure.com in this tenant; OBO cannot be set up"; exit 1; }
  AI_SCOPE_ID=$(az ad sp show --id "$AI_SP" --query "oauth2PermissionScopes[?value=='user_impersonation'].id | [0]" -o tsv)
  if [ -z "${LAB_API_CLIENT_ID:-}" ]; then
    LAB_API_CLIENT_ID=$(az ad app list --display-name lab-api --query '[0].appId' -o tsv)
    if [ -z "$LAB_API_CLIENT_ID" ]; then
      echo "+ az ad app create --display-name lab-api --sign-in-audience AzureADMyOrg"
      [ "$APPLY" = "--apply" ] || { echo "dry-run: would create lab-api and continue"; exit 0; }
      LAB_API_CLIENT_ID=$(az ad app create --display-name lab-api --sign-in-audience AzureADMyOrg --query appId -o tsv)
    fi
    set_env LAB_API_CLIENT_ID "$LAB_API_CLIENT_ID"
  fi
  APP_OBJ=$(az ad app show --id "$LAB_API_CLIENT_ID" --query id -o tsv)
  SCOPE_ID=$(az ad app show --id "$LAB_API_CLIENT_ID" --query "api.oauth2PermissionScopes[?value=='access'].id | [0]" -o tsv)
  if [ -z "$SCOPE_ID" ]; then
    SCOPE_ID=$(uuidgen | tr A-Z a-z)
    run az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJ" --body "{\"identifierUris\":[\"api://$LAB_API_CLIENT_ID\"],\"api\":{\"requestedAccessTokenVersion\":2,\"oauth2PermissionScopes\":[{\"id\":\"$SCOPE_ID\",\"value\":\"access\",\"type\":\"User\",\"isEnabled\":true,\"adminConsentDisplayName\":\"Access lab-api\",\"adminConsentDescription\":\"Run lab tests as the signed-in user\",\"userConsentDisplayName\":\"Access lab-api\",\"userConsentDescription\":\"Run lab tests as you\"}]}}"
  else echo "= scope access exists ($SCOPE_ID)"; fi
  if ! az ad app permission list --id "$LAB_API_CLIENT_ID" --query "[?resourceAppId=='$AI_SP']" -o tsv | /usr/bin/grep -q .; then
    run az ad app permission add --id "$LAB_API_CLIENT_ID" --api "$AI_SP" --api-permissions "$AI_SCOPE_ID=Scope"
  fi
  if [ -z "$(az ad sp list --filter "appId eq '$LAB_API_CLIENT_ID'" --query '[0].id' -o tsv)" ]; then
    run az ad sp create --id "$LAB_API_CLIENT_ID"
  fi
  run az ad app permission admin-consent --id "$LAB_API_CLIENT_ID"
  if [ -z "${LAB_API_CLIENT_SECRET:-}" ]; then
    echo "+ az ad app credential reset --id $LAB_API_CLIENT_ID --years 1   (secret goes to .env, never printed)"
    if [ "$APPLY" = "--apply" ]; then
      set_env LAB_API_CLIENT_SECRET "$(az ad app credential reset --id "$LAB_API_CLIENT_ID" --years 1 --query password -o tsv)"
    fi
  fi
  WEB_OBJ=$(az ad app show --id "$LAB_WEB_CLIENT_ID" --query id -o tsv)
  URIS=$(az ad app show --id "$LAB_WEB_CLIENT_ID" --query "spa.redirectUris" -o json | python3 -c 'import json,sys;u=json.load(sys.stdin);u+=["http://localhost:5400/"]*("http://localhost:5400/" not in u);print(json.dumps(u))')
  run az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$WEB_OBJ" --body "{\"spa\":{\"redirectUris\":$URIS}}"
  run az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJ" --body "{\"api\":{\"preAuthorizedApplications\":[{\"appId\":\"$LAB_WEB_CLIENT_ID\",\"delegatedPermissionIds\":[\"$SCOPE_ID\"]}]}}"
  echo "app stage done ($APPLY)"
  exit 0
fi

# rbac stage
[ -f dist/orchestrator/status.json ] || { echo "Deploy the orchestrator first (ops/deploy.py orchestrator --apply); dist/orchestrator/status.json is missing"; exit 1; }
ORCH_ID=$(python3 -c 'import json;print(json.load(open("dist/orchestrator/status.json"))["instance_identity"]["principal_id"])')
ME=$(az ad signed-in-user show --query id -o tsv)
API_SP=$(az ad sp list --filter "appId eq '$LAB_API_CLIENT_ID'" --query '[0].id' -o tsv)
[ -n "$API_SP" ] || { echo "API service principal missing; run: $0 app --apply"; exit 1; }
INVOKE_ROLE="Lab Agent Invoke"
if [ -z "$(az role definition list --name "$INVOKE_ROLE" --query '[0].name' -o tsv)" ]; then
  run az role definition create --role-definition "{\"Name\":\"$INVOKE_ROLE\",\"AssignableScopes\":[\"$ACCOUNT\"],\"Actions\":[],\"DataActions\":[\"Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action\",\"Microsoft.CognitiveServices/accounts/AIServices/agents/write\"]}"
fi
DELEGATION_ROLE="Lab User Delegation"
if [ -z "$(az role definition list --name "$DELEGATION_ROLE" --query '[0].name' -o tsv)" ]; then
  run az role definition create --role-definition "{\"Name\":\"$DELEGATION_ROLE\",\"AssignableScopes\":[\"$ACCOUNT\"],\"Actions\":[],\"DataActions\":[\"Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action\"]}"
fi
ORCH="$PROJECT/agents/$LAB_ORCHESTRATOR_NAME"
assign "$ME" User "Foundry Agent Consumer" "$ORCH"
assign "$ME" User "$INVOKE_ROLE" "$ORCH"
assign "$API_SP" ServicePrincipal "Foundry Agent Consumer" "$ORCH"
assign "$API_SP" ServicePrincipal "$INVOKE_ROLE" "$ORCH"
assign "$API_SP" ServicePrincipal "$DELEGATION_ROLE" "$ORCH"
for S in "$LAB_FINANCE_NAME" "$LAB_RISK_NAME"; do
  assign "$ORCH_ID" ServicePrincipal "Foundry Agent Consumer" "$PROJECT/agents/$S"
  assign "$ORCH_ID" ServicePrincipal "$INVOKE_ROLE" "$PROJECT/agents/$S"
  assign "$ORCH_ID" ServicePrincipal "$DELEGATION_ROLE" "$PROJECT/agents/$S"
done
assign "$ORCH_ID" ServicePrincipal "Cognitive Services OpenAI User" "$ACCOUNT"
echo "rbac stage done ($APPLY)"
