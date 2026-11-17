# Azure pilot deployment plan

## Proposed resources and planning estimate

East US, one user, scale-to-zero web, one job every 30 minutes, 32 GiB PostgreSQL storage, LRS Blob, low log ingestion and no high availability standby. All amounts are monthly USD planning allowances. Region, taxes, usage, agreements and backup overage can change the bill. No Azure resources have been created.

| Resource | Pilot monthly allowance | Role |
| --- | ---: | --- |
| PostgreSQL Flexible Server B1ms, storage and backups | $16–26 | Private database and 14-day backup retention |
| Container Apps web and recurring worker | $0–10 | Scale-to-zero dashboard and scheduled execution |
| Blob, Key Vault, network and private DNS | $1–5 | Private résumé container, secret references and database connectivity |
| Service Bus Basic | $0–2 | Defined queues for a future pipeline migration |
| Log Analytics and Application Insights | $1–8 | Platform logs and monitoring resources |
| Image registry and remote state | $0–4 | Assumes public source-only image or separately budgeted registry |
| **Azure pilot estimate, rounded** | **$25–55** | Before tax, model and optional providers |

Microsoft lists B1ms compute at about $12.41 per month on the pricing page. Container Apps has monthly consumption grants per subscription. At 1,440 executions taking 60 seconds each at 0.25 vCPU and 0.5 GiB, the job uses approximately 21,600 vCPU-seconds and 43,200 GiB-seconds before web traffic. Compare actual shared-subscription usage against the grant. Using 1,000 scored vacancies at 3,000 input and 1,000 output tokens per vacancy with GPT-5 mini at $0.25/$2 per million gives about $2.75. Reasoning tokens, retries and larger inputs can raise the cost. Use provider project limits and Azure Budget alerts. Budget alerts do not stop spending.

- [Azure PostgreSQL pricing](https://azure.microsoft.com/en-us/pricing/details/postgresql/flexible-server/)
- [Azure Container Apps pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)

## Access and integrations

| Area | Setup required |
| --- | --- |
| Azure | Confirm target subscription and budget. Register Microsoft.App, Microsoft.DBforPostgreSQL, Microsoft.OperationalInsights, Microsoft.Insights, Microsoft.Storage, Microsoft.KeyVault, Microsoft.ManagedIdentity, Microsoft.Network and Microsoft.ServiceBus. A deployment identity needs resource creation rights and scoped role-assignment rights, plus Storage Blob Data Contributor on the remote state container. |
| GitHub | Use the designated repository for source only and configure environments for production-plan and production. Require reviewers on production. Create Azure federated identity credentials matching the repository and each environment subject. Use separate plan/apply identities where practical. |
| Entra | Create a single-tenant application registration, configure v2 access tokens, expose delegated access_as_user, and allow the SPA origin under redirect URIs. Set the owner object ID, ensure the SPA can request its delegated API scope, and enforce MFA through an applicable tenant policy. Verify via live login. |
| PostgreSQL | Generate the bootstrap password securely. Keep the versionless DATABASE_URL secret in Key Vault. Use sslmode=verify-full, a trusted CA chain and the Azure private server FQDN. Rotate and use a dedicated database role before opening production usage. |
| OpenAI | Create a project-scoped API credential with project budgets and available structured-output model. Add the key directly to Key Vault. |
| Job boards | Configure named permitted Greenhouse, Lever and Ashby employer boards. For LinkedIn, Indeed, Google Jobs, commercial feeds and live ATS applications, review the account/provider permissions before enabling any connector. |
| Email and Telegram | Set a controlled SMTP relay or Telegram bot/chat. The worker accepts secure environment values. Set notification_secret_uris to Key Vault URI references and validate delivery before production. |
| Immigration evidence | UK GOV.UK and Netherlands IND provide employer registers. Review vacancy wording and the official route separately. Record the source, date and uncertainties. |

## Deployment after approval

1. Create or identify a GitHub repo and provision a protected Terraform state container using Azure RBAC. Upload a non-sensitive application image and obtain its digest. Keep candidate files and credentials out of the build context.
2. Authenticate using an authorised Azure CLI session, select the approved subscription, copy the examples and initialise the backend. Set TF_VAR_postgres_bootstrap_password in a secure shell or the GitHub environment. Use Terraform 1.13.5.
3. Start with enable_runtime=false. Run terraform fmt, validate and plan. Review the resources and regional quote. Apply only after approval.
4. Create the versionless DSN and optional model secret in Key Vault using a secure operator workflow. Allow time for Azure RBAC propagation. Verify DSN access from the app network and verify the PostgreSQL certificate chain.
5. Set enable_runtime=true, plan and apply after reviewing the change. Read the web output, register the exact SPA redirect URI, and verify HTTPS health and Entra sign-in with MFA.
6. Upload the PDF and load the reviewed candidate facts from the private onboarding file. Configure a small set of permitted boards, run the scheduled worker, and verify results, sponsorship review and a full manual-approval workflow with synthetic data before handling actual jobs.
7. Verify monitoring, backups, job schedule and budget alerts. Test restore and secret rotation before depending on the application. Use an Azure budget and model project settings to manage cost. Check failed or stale executions and notification dead letters during operation.

## Release status

The pilot implements a local, single-owner review workflow. Registry coverage beyond UK and Netherlands and jurisdiction-specific work-authorisation rules, broader market feeds, form extraction, automatic recruiter email ingestion, live ATS submission, independent submission confirmation, enforced cost ceilings and production security and monitoring validation remain open. The sandbox sender is deliberately isolated from employer sites. Application Insights is provisioned by Terraform, while app telemetry instrumentation still needs implementation. The initial deployment uses shared Terraform inputs for separate environments rather than reusable resource modules. GitHub Actions checks Terraform formatting and provider schema validation. A subscription-specific Terraform plan and apply still require authorised Azure access.

- [UK licensed sponsors register](https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers)
- [Netherlands IND recognised sponsors register](https://ind.nl/en/public-register-recognised-sponsors/public-register-work)
- [Lever posting and application API documentation](https://github.com/lever/postings-api)
