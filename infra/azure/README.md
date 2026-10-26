# Deploy Assay to Azure

Terraform that creates everything Assay needs in **your own** Azure subscription, a
script for the sign-in, and a pipeline template that deploys it from your own private
GitHub repository. You pay for what it creates; nothing here runs in or for the Assay
project.

```
infra/azure/
├── modules/assay/                    # the reusable module: every resource below
│   └── tests/                        # terraform test, against a mocked provider
├── example/environments/             # staging and production: your deployment's layout
├── scripts/entra-app.sh              # the sign-in's app registration, per environment
├── pipelines/github-actions/         # deploy.yml, a template for that repository
└── AZURE-INFO.md                     # every resource it creates, why, and what it costs
```

## How it fits together

```
internet ──► FE (Entra ID sign-in in front) ──► /api/* ──► API (internal only) ──► PostgreSQL (private)
             nginx: the FE's files                          worker, Beat ──────► Redis (private)
```

- **The FE is the only thing reachable from the internet**, and only after signing in
  with Entra ID: a page goes to the Microsoft sign-in, a call from a page gets a 403.
  Only the people assigned to the environment's app registration get in.
- **The API has no address outside the private network**, for anyone. The browser calls
  `/api/...` on the FE's address; the FE's nginx removes the prefix and passes the
  request on. The API's docs are at `<FE address>/api/docs`, after signing in.
- **The database and the broker are private**, the database with TLS required.

A browser app's API is reachable by its signed-in users (the FE runs in their browser),
so a signed-in person can call the API with their session. What's enforced is that
**nobody reaches it directly, and nobody without signing in**. Assay itself has no users
or roles yet: everyone who signs in can do everything.

## What it creates

| Assay part | Azure resource |
|---|---|
| FE | Container App, public HTTPS, Entra ID sign-in in front (Container Apps' built-in authentication) |
| API | Container App, reachable only inside the environment, served under `/api` |
| Worker | Container App, no ingress; can be switched off |
| Beat | Container App, exactly one replica |
| Migrations | Container Apps job running `alembic upgrade head` before each release |
| Broker | Redis in a Container App, reachable only by the other apps, with a password |
| Database | PostgreSQL Flexible Server, private, TLS required; optional geo-redundant backups and auto-grow |
| Images | Container Registry (Basic): `assay-api`, `assay-worker`, `assay-fe`; the apps pull with their managed identity |
| Secrets | Key Vault (Azure RBAC): the database URL, the broker URL, the judge's API key, the sign-in's client secret |
| Logs | Log Analytics: Assay's JSON logs, one field per key |
| Alerts | Optional emails: the worker isn't running, server errors, errors in the logs |
| Network | A virtual network: one subnet for the apps, one for the database |

Every app uses one managed identity: no password is stored in the apps' settings or in
the pipeline. [AZURE-INFO.md](AZURE-INFO.md) lists every resource one by one (35 per
environment, counting their parts), why each is there, what lives outside the module, and
the timings and quirks to expect. Terraform's state holds every secret in plain text: keep it in a storage
account only you and the pipeline can read, never in git.

## Cost

The resources run all the time. Most of the cost is the worker (1 vCPU and 2 GiB,
enough for the NLP models), then the database. Rough monthly list prices in North Europe,
to check with the [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/):

| | Sizes | About |
|---|---|---|
| Staging | API scaling to zero, one worker, PostgreSQL B1ms | $65–150 a month; $25–80 less with the worker switched off between tests |
| Production | API 1–2 replicas, one worker, PostgreSQL B2s with geo-redundant backups | $125–220 a month |

The ranges are wide because an idle container is billed far less than a busy one.
`worker_enabled = false` removes the worker (runs wait as `Pending` and are sent again
once it's back); `api.min_replicas = 0` lets the API sleep when idle. `terraform destroy`
removes everything when you're done. Set a budget (below) before the first apply.

## Before you start

**You need:**

- an Azure subscription, and an account that can create resources **and role
  assignments** in it (Owner, or Contributor plus Role Based Access Control
  Administrator);
- to create the sign-in's app registration: a role that may create app registrations
  in the directory (Application Administrator, or Global Administrator);
- the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), signed in
  (`az login`, then `az account show` to check the subscription);
- [Terraform](https://developer.hashicorp.com/terraform/install) 1.9 or later. On macOS,
  from HashiCorp's tap: `brew tap hashicorp/tap && brew install hashicorp/tap/terraform`
  (Homebrew's own `terraform` stays at 1.5.7).

**Pick a region that takes your subscription.** Some regions refuse new resources for
newer subscriptions: the first apply then fails with `RequestDisallowedByAzure` ("the
selected region is currently not accepting new customers"); West Europe is one that may.
The example uses North Europe. Whatever the region, it needs Container Apps,
PostgreSQL's Burstable sizes and, for production, a paired region for geo-redundant
backups (Italy North, for one, has none).

**Register the resource providers**, once per subscription. Each Azure service is
switched on per subscription: free, safe to repeat, and until it's done a deployment
fails with `MissingSubscriptionRegistration`.

```bash
PROVIDERS="Microsoft.App Microsoft.DBforPostgreSQL Microsoft.ContainerRegistry
  Microsoft.KeyVault Microsoft.OperationalInsights Microsoft.Network
  Microsoft.ManagedIdentity Microsoft.Storage Microsoft.Insights"
for p in $PROVIDERS; do az provider register --namespace "$p"; done
# a few minutes later, every line should say Registered:
for p in $PROVIDERS; do
  printf "%-32s %s\n" "$p" "$(az provider show --namespace "$p" --query registrationState -o tsv)"
done
```

In the portal: Subscriptions → your subscription → Settings → **Resource providers**;
search for each name, select it and click **Register**.

**Set a budget**, so a forgotten deployment can't surprise you. The amount is in the
subscription's billing currency; this one emails at 50, 80 and 100% of the month's cost:

```bash
SUB=$(az account show --query id -o tsv)
cat > budget.json <<'EOF'
{"properties": {"category": "Cost", "amount": 300, "timeGrain": "Monthly",
  "timePeriod": {"startDate": "2026-10-01T00:00:00Z"},
  "notifications": {
    "at50":  {"enabled": true, "operator": "GreaterThanOrEqualTo", "threshold": 50,  "thresholdType": "Actual", "contactEmails": ["you@example.com"]},
    "at80":  {"enabled": true, "operator": "GreaterThanOrEqualTo", "threshold": 80,  "thresholdType": "Actual", "contactEmails": ["you@example.com"]},
    "at100": {"enabled": true, "operator": "GreaterThanOrEqualTo", "threshold": 100, "thresholdType": "Actual", "contactEmails": ["you@example.com"]}}}}
EOF
az rest --method put --body @budget.json \
  --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.Consumption/budgets/assay-monthly?api-version=2023-05-01"
```

The start date must be the first day of a month, this one or a later one. In the portal:
Cost Management → Budgets.

## The sign-in

Each environment has its own Entra ID app registration: the "app" people sign in to.
`scripts/entra-app.sh` creates it with the Azure CLI, once per environment, so the
pipeline never needs rights in your directory:

```bash
infra/azure/scripts/entra-app.sh create "Assay (staging)"
```

It prints the **client ID** (a setting) and a **client secret** (a secret, shown only
then, valid one year). Only the people assigned to the registration can sign in, and
you're assigned. Then:

| When | Command |
|---|---|
| After the first apply, once the FE has an address | `entra-app.sh redirect <client id> "$(terraform output -raw fe_url)"`: until then the sign-in refuses, so nothing opens. Again if you destroy the environment and create it anew: the FE gets a new address |
| To let someone else in | `entra-app.sh allow <client id> <their sign-in name>` |
| Before the secret expires (a year) | `entra-app.sh rotate <client id>`: a new secret beside the old one; apply it, then delete the old one with the command it prints |

Sign out (`/.auth/logout`) ends the session with Assay; the Microsoft session stays, so
coming back signs you in at once.

## What you change

Everything else works as it is. Each value goes in one place when you deploy from your
machine, and in another when the pipeline deploys.

| | What | From your machine | With the pipeline |
|---|---|---|---|
| **Must** | Your subscription | `subscription_id` in `terraform.tfvars` | `AZURE_SUBSCRIPTION_ID` |
| **Must** | The state storage account's name: replace `tfstateyourname` (storage account names are unique across all of Azure) | `backend.hcl` | `TF_STATE_STORAGE_ACCOUNT` (and the other `TF_STATE_*`) |
| **Must** | The sign-in's client ID and secret, from `scripts/entra-app.sh create` | `entra_client_id` in `terraform.tfvars`; `export TF_VAR_entra_client_secret=...` | `ENTRA_CLIENT_ID`; the `ENTRA_CLIENT_SECRET` secret |
| **Must** | The sign-in's return address, once, after the first apply | `scripts/entra-app.sh redirect <client id> <fe url>` | the same |
| **Must** (pipeline) | The pipeline's Azure identity | | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` |
| **Must** (your deployment repository) | The module's `source`: the commented `git::` line, with the BE release in `?ref=vX.Y.Z` | each environment's `main.tf` | the same |
| **Must** (your deployment repository) | The FE release, `fe_version` | each environment's `main.tf` | the same |
| *Recommended* | Who gets the alert emails | `alert_emails` in `main.tf` | the same |
| *Recommended* | Your application under test, if your tests ask it for answers | `extra_environment` in `main.tf` (`ASSAY_TARGET_*`), or later from Assay's Settings | the same |
| *Optional* | The LLM judge: provider and model, and its key | `judge` in `main.tf`; `export TF_VAR_judge_api_key=...` | `judge` in `main.tf`; the `JUDGE_API_KEY` secret |
| *Optional* | How many workers: `min_replicas` and `max_replicas` in `worker`, both set to that number (nothing scales the worker by itself) | `main.tf` | the same |
| *Optional* | Region, resource group name, sizes, the worker's off switch, the upload limit | `main.tf`; the module's inputs are in `modules/assay/variables.tf` | the same |

The images to run aren't a setting: from your machine you pass them when applying
(`-var image_tag=... -var fe_image_tag=...`), and the pipeline reads them from the
versions in `main.tf`.

## Deploy from your machine

Each folder in `example/environments/` (`staging`, `production`) can be applied with your
own `az login`: for a first look, or before the pipeline is set up. It's the same folder the
pipeline uses afterwards, and with the same state storage and key, the pipeline carries on
from where you left off. The steps below use `staging`.

**1. A storage account for Terraform's state**, once per subscription. The state holds
every secret of the deployment, so the account allows only Microsoft sign-in (no access
keys), and keeps old versions and deleted files for 30 days so a damaged state can be
recovered. Its name must be unique across all of Azure: replace `tfstateyourname`.

```bash
az group create --name rg-terraform-state --location northeurope
az storage account create --name tfstateyourname --resource-group rg-terraform-state \
  --sku Standard_LRS --https-only true --min-tls-version TLS1_2 \
  --allow-blob-public-access false --allow-shared-key-access false
az storage account blob-service-properties update --account-name tfstateyourname \
  --resource-group rg-terraform-state --enable-versioning true \
  --enable-delete-retention true --delete-retention-days 30 \
  --enable-container-delete-retention true --container-delete-retention-days 30
# let yourself read and write the state with your Azure login (before creating the
# container: with no access keys, creating it needs this role)
az role assignment create --role "Storage Blob Data Contributor" \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --scope "$(az storage account show --name tfstateyourname --query id -o tsv)"
# the role takes a minute to apply; if this says you're not authorised, wait and retry
az storage container create --name tfstate --account-name tfstateyourname --auth-mode login
```

**2. Your settings**, in `example/environments/staging/`:

```bash
cd infra/azure/example/environments/staging
cp backend.hcl.example backend.hcl            # the storage account above
cp terraform.tfvars.example terraform.tfvars  # your subscription and entra_client_id
export TF_VAR_entra_client_secret=...         # from entra-app.sh create; never in a file
export TF_VAR_judge_api_key=...               # optional; never in a file
```

Both copies are git-ignored. The environment's other choices (its region, sizes, alert
emails, the judge, other Assay settings) are in its `main.tf`.

**3. The infrastructure**, with placeholder images (Assay's own images need the
registry this creates), then the sign-in's return address:

```bash
terraform init -backend-config=backend.hcl
terraform apply
../../../scripts/entra-app.sh redirect <client id> "$(terraform output -raw fe_url)"
```

**4. The images**, built in your registry (no Docker needed locally; the worker's image
is about 3.5 GB and takes a while). Each is tagged with its version: Assay's from
`pyproject.toml`, the FE's from its `package.json`.

```bash
REGISTRY=$(terraform output -raw registry_name)
cd ../../../../..   # Assay's repository root
BE=$(sed -nE 's/^version = "(.*)"/\1/p' pyproject.toml)
az acr build --registry "$REGISTRY" --image "assay-api:$BE" --file Dockerfile .
az acr build --registry "$REGISTRY" --image "assay-worker:$BE" --file Dockerfile.worker .
cd /path/to/assayfe   # the FE's repository root
FE=$(node -p 'require("./package.json").version')
az acr build --registry "$REGISTRY" --image "assay-fe:$FE" --file Dockerfile \
  --build-arg "VERSION=$FE" --build-arg "REVISION=$(git rev-parse HEAD)" .
cd -   # back to example/environments/staging
```

**5. The migrations, then the release:**

```bash
RG=$(terraform output -raw resource_group_name)
JOB=$(terraform output -raw migrate_job_name)
SERVER=$(terraform output -raw registry_login_server)
# the new image first: start --image would drop the job's settings and secrets
az containerapp job update --name "$JOB" --resource-group "$RG" \
  --image "$SERVER/assay-api:$BE" --container-name migrate
az containerapp job start --name "$JOB" --resource-group "$RG"
# wait until it succeeds:
az containerapp job execution list --name "$JOB" --resource-group "$RG" -o table

terraform apply -var "image_tag=$BE" -var "fe_image_tag=$FE"
```

**6. Check it.** Open `terraform output -raw fe_url` and sign in. `/api/docs` shows the
API, and `/api/health/worker` should answer `ready`.

## Deploy with the pipeline

### Two repositories

- **The code** lives in public repositories: `cupelworks/assay` (the BE, and this
  module) and `cupelworks/assayfe` (the FE). They know nothing about your Azure.
- **Your deployment** lives in a **private repository of your own**, not a fork (a fork
  of a public repository is always public). It holds which releases run where, the
  pipeline, its runs and their logs, your variables and secrets. A public repository's
  workflow logs are public, so nothing about your Azure ever runs in one.

The deployment repository holds no Assay code. Its layout is `example/`'s:

```
your-deployment-repo/
├── environments/
│   ├── staging/      # main.tf: the module at a release (?ref=vX.Y.Z), fe_version, sizes
│   └── production/   # the same, its own releases and sizes
└── .github/workflows/deploy.yml   # pipelines/github-actions/deploy.yml
```

Copy `example/environments/` with its two folders, and in each `main.tf` replace the
module's `source` with the line in its comment: the module at a release. That release is
also the BE version the environment runs, and `fe_version` is the FE's. The pipeline
reads both, checks out the two repositories at those tags (they're public: no key),
checks that the FE works with that API, builds the three images, runs the migrations and
releases.

### Releasing

1. A new release is tagged in `cupelworks/assay` (`vX.Y.Z`) or `cupelworks/assayfe`.
2. In your deployment repository, change the version in `environments/staging/main.tf`
   (the `?ref=` or `fe_version`) and push to `main`: **staging deploys**.
3. Once staging looks right, make the same change in `environments/production/main.tf`,
   push, then **Run workflow → production**: production deploys only when started by
   hand, and only from `main`.

The pipeline stops before deploying when the FE doesn't work with that API (the FE's
`package.json` names the API versions it needs), or when a tag doesn't hold the version
it names. A version already built is reused, never built again over.

It also stops, before applying anything, when Terraform's plan would **delete or replace
what holds the data**: the resource group, the database server, its database or the Key
Vault. That only happens for a setting Azure can't change in place, such as the region or
the database's geo-redundant backups, and it would lose the data. Make such a change
deliberately, from your machine, after a backup.

### Setting it up

**1. GitHub environments.** In the deployment repository's Settings → Environments,
create `staging` and `production`; on `production`, limit deployment branches to `main`.
Environments in a **private** repository need a paid GitHub plan (Pro, Team or
Enterprise); required reviewers there need Enterprise, so the gate on production is that
only people who can write to the repository can start the run. On GitHub Free, a private
repository has no environments: you'd need one workflow file per environment and a
federated credential per file, which this template doesn't cover.

**2. An Azure identity per environment**, which GitHub logs in as with OIDC (no stored
password). Azure trusts it only from that GitHub environment, so a staging run can't
touch production:

```bash
REPO=YOUR_ORG/YOUR_DEPLOYMENT_REPO
ENV=staging   # then again with production
# how GitHub names the repository to Azure: usually with ID numbers
# (repo:org@123/repo@456), else plainly (repo:org/repo)
PREFIX=$(gh api "repos/$REPO/actions/oidc/customization/sub" --jq '.sub_claim_prefix // empty')
PREFIX=${PREFIX:-repo:$REPO}
APP_ID=$(az ad app create --display-name "assay-deploy-$ENV" --query appId -o tsv)
az ad sp create --id "$APP_ID"
az ad app federated-credential create --id "$APP_ID" --parameters "{
  \"name\": \"github-$ENV\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"$PREFIX:environment:$ENV\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"
# it creates resources and role assignments...
az role assignment create --assignee "$APP_ID" --role Owner \
  --scope "/subscriptions/$(az account show --query id -o tsv)"
# ...and writes Terraform's state
az role assignment create --assignee "$APP_ID" --role "Storage Blob Data Contributor" \
  --scope "$(az storage account show --name tfstateyourname --query id -o tsv)"
```

Owner on the subscription is the simplest; scope it to the environment's resource group
if your organisation requires it (the group must then exist before the first run).

**3. The sign-in**, per environment: `scripts/entra-app.sh create "Assay (staging)"`.

**4. Each GitHub environment's variables and secrets:**

| Kind | Name | Value |
|---|---|---|
| Variable | `AZURE_CLIENT_ID` | the environment's `$APP_ID` |
| Variable | `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| Variable | `AZURE_SUBSCRIPTION_ID` | your subscription's ID |
| Variable | `TF_STATE_RESOURCE_GROUP` | `rg-terraform-state` |
| Variable | `TF_STATE_STORAGE_ACCOUNT` | `tfstateyourname` |
| Variable | `TF_STATE_CONTAINER` | `tfstate` |
| Variable | `TF_STATE_KEY` | `assay-staging.tfstate` (production: `assay-production.tfstate`) |
| Variable | `ENTRA_CLIENT_ID` | the client ID from `entra-app.sh create` |
| Secret | `ENTRA_CLIENT_SECRET` | its client secret |
| Secret | `JUDGE_API_KEY` | optional: the judge's API key |

Optional repository variables `ASSAY_REPOSITORY` and `ASSAY_FE_REPOSITORY` point the
pipeline at other copies of the code.

**5. The first run** deploys with placeholder images first, then the real ones. When it
ends, it prints the FE's address: run `entra-app.sh redirect <client id> <that address>`
once, and the sign-in works.

### Keep Azure out of public repositories

The pipeline template skips itself in `cupelworks/assay`. In any public repository
holding Assay's code: no Azure variables or secrets, never a `pull_request_target`
workflow, and Settings → Actions → "Require approval for all outside collaborators".

## Using the module from your own configuration

Pin the module to a release, so an update to Assay never changes your infrastructure
unannounced:

```hcl
module "assay" {
  source = "git::https://github.com/cupelworks/assay.git//infra/azure/modules/assay?ref=vX.Y.Z"

  environment         = "prod"
  location            = "northeurope"
  resource_group_name = azurerm_resource_group.assay.name

  entra               = { client_id = var.entra_client_id }
  entra_client_secret = var.entra_client_secret
  image_tag           = var.image_tag
  fe_image_tag        = var.fe_image_tag
}
```

Its inputs are in `modules/assay/variables.tf`:

- sizes: `api`, `fe`, `worker`, `worker_enabled`, `postgres` (with `geo_redundant_backup`,
  which can only be chosen when the server is created, and `auto_grow`);
- `alert_emails`;
- the network range;
- the upload limit: `dataset_upload_max_mb`, which sets the API's limit and the FE's
  nginx alike;
- the judge;
- other browser origins: `cors_allowed_origins`, empty by default;
- any other `ASSAY_*` setting: `extra_environment`.

The root that calls it configures the `azurerm` and `azapi` providers (`azapi` sets up
the sign-in, which `azurerm` doesn't cover).

## Running it

- **Logs:** in Log Analytics, `ContainerAppConsoleLogs_CL`; every Assay log line is JSON
  (`Log_s`), with the request ID and the run ID as fields. A 500's body carries the same
  request ID.
- **Alerts** (with `alert_emails`): the worker hasn't run for 15 minutes; the FE answered
  server errors (the API's, or the API unreachable); the API, the worker or Beat logged an
  error. The worker's alert is off while `worker_enabled` is false.
- **Settings:** the application under test and the judge can also be changed from the UI
  (Settings); saved settings win over the environment. Settings › Operations shows how
  the deployment is set up.
- **Secrets that expire:** the sign-in's client secret after a year (`entra-app.sh
  rotate`); the judge's key whenever its provider says.
- **Images pile up** in the registry, one per release and app. Clean up old ones with
  `az acr run --registry "$REGISTRY" --cmd "acr purge --filter 'assay-.*:.*' --ago 30d --keep 5" /dev/null`.
- **The broker keeps nothing on disk.** If Redis restarts, queued tasks are lost, and
  Beat's reconciliation scan sends the waiting runs again. Point
  `ASSAY_CELERY_BROKER_URL` at a managed Redis through `extra_environment` if you prefer.

## When something goes wrong

- **`RequestDisallowedByAzure` … "not accepting new customers"** on the first apply: the
  region refuses new resources for your subscription. Destroy what was created and pick
  another region (see Before you start).
- **The pipeline stops at "Log in to Azure" with `AADSTS700213` … "No matching federated
  identity record"**: the identity's subject isn't the one GitHub sends, which the error
  prints (`subject claim - repo:…`). Set that one, with `az ad app federated-credential
  update --id <identity's app ID> --federated-credential-id github-<environment>
  --parameters '{"subject": "<the subject>"}'`, and re-run the workflow.
- **"Provider produced inconsistent result after apply … Root object was present, but now
  absent"**, or a 404 for something just created: Azure was slow to show a new resource.
  It does exist. Wait a minute and apply again. If Terraform then says the resource
  already exists, import it (`terraform import <address> <Azure ID>`); if it marks one
  as tainted although it's fine, `terraform untaint <address>` keeps it from being
  recreated. A plan can fail the same way: run it again.
- **`az acr build` fails at "Scanning for dependencies"**: Azure reads any Dockerfile line
  starting with `from`, in any case, as a `FROM` instruction, a Python line inside a
  `RUN` included. Write such lines so they don't start with `from`.
- **The migration job ends `Failed` at once, its logs saying it can't connect or using
  SQLite**: it was started with `az containerapp job start --image`, which replaces the
  job's container and drops its settings. Switch its image with `az containerapp job
  update --image`, then start it plainly.
- **A signed-in page shows the app at `/.auth/me`**: without a token store, Container
  Apps doesn't serve `/.auth/me`. The signed-in user's identity reaches the FE in the
  `X-MS-CLIENT-PRINCIPAL*` headers instead. `/.auth/logout` works either way.

## Removing it

```bash
terraform destroy
az ad app delete --id <client id>   # the environment's sign-in, if you're done with it
```

This deletes every resource it created, the database included. The state storage
account stays; delete it yourself if you no longer need it. Key Vault is kept for 7
days after deletion (soft delete) before its name can be reused.

Azure may also have created a **Network Watcher** for the region, usually in a resource
group named `NetworkWatcherRG`: it does so for the first virtual network in a region,
unless the subscription has one there already. It's Azure's own, free, and shared by
every virtual network in that region, so leave it in place.

Deleting the Container Apps environment is slow: Azure releases its share of the
virtual network last, which can take 40 minutes. `terraform destroy` waits; it isn't
stuck.

## Limits

- Terraform's Azure provider is pinned to `~> 4.81` (version 5 isn't supported yet), and
  `azapi` to `~> 2.5`.
- The module, the example and the script are checked with `terraform test` against a
  mocked provider, `terraform validate` and `bash -n` (see `.github/workflows/ci.yml`),
  not by deploying to Azure in this repository.
