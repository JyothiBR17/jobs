"""Read-only Azure deployment diagnostics. Prints selected operational metadata only."""
import json
import subprocess


def query(label, arguments):
    result = subprocess.run(["az", *arguments, "--output", "json"], capture_output=True, text=True)
    print(f"--- {label} ---", flush=True)
    if result.returncode:
        print(f"Azure CLI failed with code {result.returncode}", flush=True)
        # Diagnostic commands contain no credentials. Keep any unexpected response private.
        return None
    value = json.loads(result.stdout)
    print(json.dumps(value, indent=2), flush=True)
    return value


query("Container Apps environments", [
    "containerapp", "env", "list", "--query",
    "[].{name:name,resourceGroup:resourceGroup,location:location,subnet:properties.vnetConfiguration.infrastructureSubnetId,provisioningState:properties.provisioningState}",
])
for region in ("eastus", "eastus2", "centralus"):
    query(f"PostgreSQL capabilities in {region}", [
        "postgres", "flexible-server", "list-skus", "--location", region,
        "--query", "[].{reason:reason,versions:supportedServerVersions[].name,editions:supportedServerEditions[].name}",
    ])
query("jobsai resources", [
    "resource", "list", "--resource-group", "jobsai-rg",
    "--query", "[].{name:name,type:type,location:location,id:id}",
])
query("Deployment identity Blob role assignments", [
    "role", "assignment", "list",
    "--assignee-object-id", "0a34ff40-76b9-4459-b5d2-6fde206db4ff",
    "--scope", "/subscriptions/ed8f4826-ca81-4919-a8e9-79d5b2781b98/resourceGroups/jobsai-rg",
    "--include-inherited", "--fill-principal-name", "false",
    "--query", "[].{role:roleDefinitionName,scope:scope}",
])
query("Terraform state blob metadata", [
    "storage", "blob", "show", "--account-name", "stgaccpocsoc",
    "--container-name", "jobs", "--name", "jobsai-production.tfstate", "--auth-mode", "login",
    "--query", "{name:name,lastModified:properties.lastModified,lease:properties.lease.status}",
])
query("jobsai network", [
    "network", "vnet", "list", "--resource-group", "jobsai-rg",
    "--query", "[].{name:name,location:location,subnets:subnets[].{name:name,prefix:addressPrefix}}",
])

# Collect the exact, region-specific PostgreSQL SKU capabilities; edition labels
# alone are insufficient to predict whether Azure accepts B_Standard_B1ms.
result = subprocess.run(
    ["az", "postgres", "flexible-server", "list-skus", "--location", "centralus",
     "--output", "json"],
    capture_output=True, text=True,
)
print("--- Central US PostgreSQL SKU detailed capabilities ---", flush=True)
if result.returncode:
    print(f"SKU metadata command failed with exit code {result.returncode}", flush=True)
else:
    capabilities = json.loads(result.stdout)
    print(json.dumps(capabilities, indent=2)[:25000], flush=True)

query("Existing Container Apps environment details", [
    "containerapp", "env", "show", "--resource-group", "rg-aegissoc-app-dev",
    "--name", "cae-aegissoc-dev",
    "--query", "{id:id,name:name,location:location,subnet:properties.vnetConfiguration.infrastructureSubnetId,workloadProfiles:properties.workloadProfiles,provisioningState:properties.provisioningState}",
])
query("Failed or partially created PostgreSQL server", [
    "postgres", "flexible-server", "show", "--resource-group", "jobsai-rg",
    "--name", "jobsai-pg", "--query",
    "{name:name,location:location,sku:sku.name,state:state,publicAccess:network.publicNetworkAccess,version:version}",
])
