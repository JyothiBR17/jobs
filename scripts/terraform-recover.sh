#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
import json, os, re
from pathlib import Path
data = json.loads(os.environ["TF_VARS"])
prefix = data.get("prefix", "jobsai")
if not re.fullmatch(r"jobsai[a-z0-9]{0,10}", prefix):
    raise SystemExit("Unexpected jobsai prefix")
data["workload_location"] = "centralus"
Path("infra/ci.auto.tfvars.json").write_text(json.dumps(data))
Path("/tmp/jobsai-prefix").write_text(prefix)
PY
terraform -chdir=infra init -backend-config=backend.hcl -input=false
prefix=$(cat /tmp/jobsai-prefix)
scope="/subscriptions/$ARM_SUBSCRIPTION_ID/resourceGroups/$prefix-rg"
principal="0a34ff40-76b9-4459-b5d2-6fde206db4ff"

# Reconcile the narrowly scoped deployment permission needed by Entra Blob access.
role_id=$(az role assignment list --assignee-object-id "$principal" --scope "$scope" --fill-principal-name false   --query "[?roleDefinitionName=='Storage Blob Data Contributor' && scope=='$scope'].id | [0]" -o tsv)
if [[ -z "$role_id" ]]; then
  role_id=$(az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal     --role "Storage Blob Data Contributor" --scope "$scope" --query id -o tsv)
fi
terraform -chdir=infra state list > /tmp/jobsai-state-addresses
if ! grep -Fxq azurerm_role_assignment.deployment_storage /tmp/jobsai-state-addresses; then
  terraform -chdir=infra import azurerm_role_assignment.deployment_storage "$role_id"
fi

if ! grep -Fxq azurerm_storage_account.main /tmp/jobsai-state-addresses; then
  storage_id="$scope/providers/Microsoft.Storage/storageAccounts/${prefix}docs"
  az storage account show --ids "$storage_id" --output none
  imported=false
  for attempt in 1 2 3 4 5 6; do
    if terraform -chdir=infra import azurerm_storage_account.main "$storage_id"; then
      imported=true
      break
    fi
    sleep 10
  done
  [[ "$imported" == true ]]
fi
terraform -chdir=infra plan -out=recovery.tfplan -input=false
terraform -chdir=infra show -json recovery.tfplan > /tmp/jobsai-plan.json
python - <<'PY'
import json, os
from pathlib import Path
plan=json.loads(Path("/tmp/jobsai-plan.json").read_text())
changes=[r for r in plan.get("resource_changes",[]) if r["change"]["actions"] != ["no-op"]]
deletions=[r["address"] for r in changes if "delete" in r["change"]["actions"]]
if deletions:
    raise SystemExit("Recovery plan requires review of deletions: " + ", ".join(deletions))
lines=["## jobsai Central US recovery plan", "", "| Resource | Action |", "| --- | --- |"]
lines += [f'| {r["address"]} | {", ".join(r["change"]["actions"])} |' for r in changes]
summary="\n".join(lines)+"\n"
print(summary)
with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
    f.write(summary)
PY
if [[ "${1:-plan}" == apply ]]; then
  terraform -chdir=infra apply -input=false recovery.tfplan
fi
