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
├── pipelines/github-actions/         # deploy.yml and destroy.yml, templates for that repository
└── AZURE-INFO.md                     # every resource it creates, why, and what it costs
```

Read the first four sections once, then follow **one** of the two ways of deploying from
its first step to its last: everything it needs is in it, in order.

## Contents

- [How it fits together](#how-it-fits-together)
- [What it creates](#what-it-creates)
- [Cost](#cost)
- [Before you start](#before-you-start)
- [Deploy from your machine](#deploy-from-your-machine)
- [Deploy with GitHub Actions](#deploy-with-github-actions)
  - [Releasing](#releasing)
  - [Production](#production)
  - [Keep Azure out of public repositories](#keep-azure-out-of-public-repositories)
- [Running it](#running-it)
- [When something goes wrong](#when-something-goes-wrong)
- [Removing it](#removing-it)
  - [With GitHub Actions (staging)](#with-github-actions-staging)
  - [From your machine (any environment)](#from-your-machine-any-environment)
  - [What's left afterwards](#whats-left-afterwards)
  - [Creating it again](#creating-it-again)
- [Limits](#limits)
- [Using the module from your own configuration](#using-the-module-from-your-own-configuration)

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
the timings and quirks to expect. Terraform's state holds every secret in plain text: keep
it in a storage account only you and the pipeline can read, never in git.

## Cost

The resources run all the time. Most of the cost is the worker (1 vCPU and 2 GiB,
enough for the NLP models), then the database. Rough monthly list prices in North Europe
as of October 2026, to check with the [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/):

| | Sizes | About |
|---|---|---|
| Staging | API scaling to zero, one worker, PostgreSQL B1ms | $65–150 a month; $25–80 less with the worker switched off between tests |
| Production | API 1–2 replicas, one worker, PostgreSQL B2s with geo-redundant backups | $125–220 a month |

The ranges are wide because an idle container is billed far less than a busy one.
`worker_enabled = false` removes the worker (runs wait as `Pending` and are sent again
once it's back); `api.min_replicas = 0` lets the API sleep when idle.

## Before you start

**You need:**

- an Azure subscription, and an account that can create resources **and role
  assignments** in it (Owner, or Contributor plus Role Based Access Control
  Administrator);
- to create the sign-in's app registration: a role that may create app registrations
  in the directory (Application Administrator, or Global Administrator);
- the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli);
- [Terraform](https://developer.hashicorp.com/terraform/install) 1.9 or later. On macOS,
  from HashiCorp's tap: `brew tap hashicorp/tap && brew install hashicorp/tap/terraform`
  (Homebrew's own `terraform` stays at 1.5.7);
- git;
- from your machine: [Node.js](https://nodejs.org), which reads the FE's version and
  checks that it works with the API;
- with GitHub Actions: the [GitHub CLI](https://cli.github.com);
- **bash**: every command here is written for it. On a Mac, whose terminal runs zsh, type
  `bash` first (and `exit` when you're done): zsh reads some of these lines differently,
  and they'd create things with the wrong names.

**Sign in**, and check that the subscription shown is the one to deploy to:

```bash
az login
az account show
gh auth login   # only for GitHub Actions
```

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

**Set a budget**, so a forgotten deployment can't surprise you. It emails you at 50, 80
and 100% of the month's cost, starting this month. Set your address and your own monthly
ceiling, in the subscription's billing currency (300 is only an example):

```bash
EMAIL=
AMOUNT=300
SUB=$(az account show --query id -o tsv)
START=$(date -u +%Y-%m-01T00:00:00Z)
if [ -n "$EMAIL" ]; then
cat > budget.json <<EOF
{"properties": {"category": "Cost", "amount": $AMOUNT, "timeGrain": "Monthly",
  "timePeriod": {"startDate": "$START"},
  "notifications": {
    "at50":  {"enabled": true, "operator": "GreaterThanOrEqualTo", "threshold": 50,  "thresholdType": "Actual", "contactEmails": ["$EMAIL"]},
    "at80":  {"enabled": true, "operator": "GreaterThanOrEqualTo", "threshold": 80,  "thresholdType": "Actual", "contactEmails": ["$EMAIL"]},
    "at100": {"enabled": true, "operator": "GreaterThanOrEqualTo", "threshold": 100, "thresholdType": "Actual", "contactEmails": ["$EMAIL"]}}}}
EOF
  az rest --method put --body @budget.json \
    --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.Consumption/budgets/assay-monthly?api-version=2023-05-01"
else
  echo "Set EMAIL first."
fi
```

In the portal: Cost Management → Budgets.

**Then choose how to deploy:**

| | From your machine | With GitHub Actions |
|---|---|---|
| **For** | a first look, or a one-off environment | staging and production, released by a push |
| **Runs on** | your computer, with your `az login` | GitHub's runners, in a private repository of yours |
| **Needs** | the two code repositories cloned | a private GitHub repository, on a paid plan |

## Deploy from your machine

The steps create **staging**, from the configuration in Assay's own repository. Run them
all in **one terminal window**: later steps use values the earlier ones set (`$ASSAY`,
`$ASSAYFE`, `$STATE_ACCOUNT`, `$ENTRA_CLIENT_ID`, `$BE`, `$FE`). In a new one, from step 4
on, set `ASSAY`, `ASSAYFE`, `STATE_ACCOUNT` and `ENTRA_CLIENT_ID` again, and
`cd "$ASSAY/infra/azure/example/environments/staging"`; from step 5 on, also step 4's
secret line, and step 8 needs step 7's `BE` and `FE`.

**1. The code, at the releases you deploy.** Pick a release of each from the tags of
[`cupelworks/assay`](https://github.com/cupelworks/assay/tags) and
[`cupelworks/assayfe`](https://github.com/cupelworks/assayfe/tags) (the newest of each is
a safe start), and clone them in an empty folder, with your releases in place of
`vX.Y.Z`:

```bash
git clone --branch vX.Y.Z https://github.com/cupelworks/assay.git
git clone --branch vX.Y.Z https://github.com/cupelworks/assayfe.git
ASSAY=$PWD/assay
ASSAYFE=$PWD/assayfe
```

Check that the two work together, with the BE release in place of `X.Y.Z`: an FE release
names the API releases it works with. If it says **outside**, pick another FE release:

```bash
node "$ASSAYFE/scripts/check-api-version.mjs" X.Y.Z
```

**2. Terraform's state**, once per subscription: skip this step if you already have one.
Terraform keeps a record of everything it created, the *state*, and every run reads and
updates it. It lives in a storage account of yours, shared by every environment (each
under its own key), and stays when an environment is removed. The state holds every
secret of the deployment, so the account allows only Microsoft sign-in (no access keys),
and keeps old versions and deleted files for 30 days so a damaged state can be recovered.
Only the storage account's name is yours to choose (unique across all of Azure, 3 to 24
lowercase letters and digits): set it after `STATE_ACCOUNT=`, and keep the other names.

```bash
STATE_ACCOUNT=
az group create --name rg-terraform-state --location northeurope
az storage account create --name "$STATE_ACCOUNT" --resource-group rg-terraform-state \
  --sku Standard_LRS --https-only true --min-tls-version TLS1_2 \
  --allow-blob-public-access false --allow-shared-key-access false
az storage account blob-service-properties update --account-name "$STATE_ACCOUNT" \
  --resource-group rg-terraform-state --enable-versioning true \
  --enable-delete-retention true --delete-retention-days 30 \
  --enable-container-delete-retention true --container-delete-retention-days 30
# let yourself read and write the state with your Azure login (before creating the
# container: with no access keys, creating it needs this role)
az role assignment create --role "Storage Blob Data Contributor" \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --scope "$(az storage account show --name "$STATE_ACCOUNT" --query id -o tsv)"
```

The role takes a minute to apply. Then create the container; if it says you're not
authorised, wait a minute and run it again:

```bash
az storage container create --name tfstate --account-name "$STATE_ACCOUNT" --auth-mode login
```

**3. The sign-in.** Each environment has its own Entra ID app registration: the "app"
people sign in to. Only the people assigned to it get in, and you're assigned.

```bash
"$ASSAY/infra/azure/scripts/entra-app.sh" create "Assay (staging)"
```

It prints the **client ID** and a **client secret**, shown only now and valid one year:
keep the secret in your password manager. Then keep the client ID in this terminal: run
this line, paste the client ID and press Enter:

```bash
read -r ENTRA_CLIENT_ID
```

**4. Your settings**, in the staging folder. `backend.hcl` says where the state is,
`terraform.tfvars` holds your subscription and the sign-in's client ID (both files are
git-ignored), and the secret is read without showing it or keeping it in your history:

```bash
cd "$ASSAY/infra/azure/example/environments/staging"
cat > backend.hcl <<EOF
resource_group_name  = "rg-terraform-state"
storage_account_name = "$STATE_ACCOUNT"
container_name       = "tfstate"
key                  = "assay-staging.tfstate"
use_azuread_auth     = true
EOF
cat > terraform.tfvars <<EOF
subscription_id = "$(az account show --query id -o tsv)"
entra_client_id = "$ENTRA_CLIENT_ID"
EOF
```

Then the secret, on its own: run this line, paste the client secret from step 3 and press
Enter (nothing shows while you paste):

```bash
read -s TF_VAR_entra_client_secret && export TF_VAR_entra_client_secret
```

The environment's other choices are in the same folder's `main.tf`. Everything works
as it is; what you may change:

| | What | In `main.tf` |
|---|---|---|
| *Recommended* | Who gets the alert emails | `alert_emails` |
| *Recommended* | Your application under test, if your tests ask it for answers | `extra_environment` (`ASSAY_TARGET_*`), or later from Assay's Settings |
| *Optional* | The LLM judge: provider and model, and its key | `judge`; the key with `read -s TF_VAR_judge_api_key && export TF_VAR_judge_api_key` |
| *Optional* | How many workers: `min_replicas` and `max_replicas` in `worker`, both set to that number (nothing scales the worker by itself) | `worker` |
| *Optional* | Region, resource group name, sizes, the worker's off switch, the upload limit | the module's inputs are in `modules/assay/variables.tf` |

**5. The infrastructure**, with placeholder images: Assay's own images need the
registry this creates. About 20 minutes the first time.

```bash
terraform init -backend-config=backend.hcl
terraform apply
```

`terraform apply` lists what it will create and asks you to confirm: type `yes`.

**6. The sign-in's return address.** After signing in, Microsoft sends people back only
to an address the app registration lists, and the FE's address exists now:

```bash
"$ASSAY/infra/azure/scripts/entra-app.sh" redirect "$ENTRA_CLIENT_ID" "$(terraform output -raw fe_url)"
```

It answers "Sign-in now returns to …". Without it, Microsoft refuses the sign-in
("redirect URI mismatch"). An environment created anew gets a new address, and needs this
step again.

**7. The images**, built in your registry from the two clones (no Docker needed
locally; the worker's image is about 3.5 GB and takes a while). Each is tagged with its
version, read from the clone: Assay's from `pyproject.toml`, the FE's from its
`package.json` (step 1 checked that the two work together).

```bash
REGISTRY=$(terraform output -raw registry_name)
BE=$(sed -nE 's/^version = "(.*)"/\1/p' "$ASSAY/pyproject.toml")
FE=$(cd "$ASSAYFE" && node -p 'require("./package.json").version')
(cd "$ASSAY" && az acr build --registry "$REGISTRY" --image "assay-api:$BE" --file Dockerfile .)
(cd "$ASSAY" && az acr build --registry "$REGISTRY" --image "assay-worker:$BE" --file Dockerfile.worker .)
(cd "$ASSAYFE" && az acr build --registry "$REGISTRY" --image "assay-fe:$FE" --file Dockerfile \
  --build-arg "VERSION=$FE" --build-arg "REVISION=$(git rev-parse HEAD)" .)
```

**8. The migrations, then the release:**

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

**9. Check it.** Open `terraform output -raw fe_url` and sign in. `/api/docs` shows the
API, and `/api/health/worker` should answer `ready`. Expect one "worker not running"
alert email shortly after, then a "resolved" one: Azure's replica count lags the new
worker.

**Later, a new release:** move the two clones to the new releases, check them, then, in
this folder with step 4's secret set, steps 7 and 8. Images already in the registry are
kept, so going back is the same steps with the old releases.

```bash
git -C "$ASSAY" fetch --tags && git -C "$ASSAY" checkout vX.Y.Z
git -C "$ASSAYFE" fetch --tags && git -C "$ASSAYFE" checkout vX.Y.Z
node "$ASSAYFE/scripts/check-api-version.mjs" X.Y.Z
```

## Deploy with GitHub Actions

The pipeline is a GitHub Actions workflow. Other CI systems have no template here.

Your deployment is a **private repository of yours** (not a fork: a fork of a public
repository is public, and so are its workflow logs), with exactly this content. Most of
it is copied from [`cupelworks/assay`](https://github.com/cupelworks/assay/tree/main/infra/azure)'s
`main` branch, not from a release, on purpose: it has the pipeline's latest fixes, while
the release you deploy is set in `main.tf`.

```
your-deployment-repo/      copied from cupelworks/assay (main)
├── environments/          ← infra/azure/example/environments/
│   ├── staging/main.tf      two lines to change (step 7)
│   └── production/main.tf   the same
├── .github/workflows/
│   ├── deploy.yml         ← infra/azure/pipelines/github-actions/deploy.yml
│   └── destroy.yml        ← infra/azure/pipelines/github-actions/destroy.yml (optional, step 9)
└── .gitignore             you create it, with the lines below
```

The `.gitignore` keeps out what Terraform makes when it runs in the repository (as when
removing an environment from your machine):

```
.terraform/
.terraform.lock.hcl
*.tfstate
*.tfstate.*
backend.hcl
terraform.tfvars
```

The lock file is among them: each run takes the providers' newest versions within the
constraints in each environment's `versions.tf`.

The first push to `main` deploys staging, so **add the content only at step 7**, once
the variables and secrets it needs exist.

The steps set up **staging**; [Production](#production), after them, repeats the ones it
needs. Run the commands in **one terminal window**: later steps use values the earlier
ones set (`$REPO`, `$ENV`, `$ASSAY`, `$STATE_ACCOUNT`, `$ENTRA_CLIENT_ID`, `$APP_ID`). In a
new one, set again the ones the next step uses: from step 3 on, `REPO`, `ENV`, `ASSAY` and
`STATE_ACCOUNT`; from step 6 on, also `ENTRA_CLIENT_ID` and `APP_ID`.

**1. Your repository.** Create the private repository on GitHub, empty (no README, no
`.gitignore`, no licence): its content
comes in step 7. In an empty folder, set its name after `REPO=` (`your-org/your-repo`),
and clone Assay's repository, for the files to copy and the sign-in's script:

```bash
REPO=
ENV=staging
git clone https://github.com/cupelworks/assay.git
ASSAY=$PWD/assay
```

**2. Terraform's state**, once per subscription: skip this step if you already have one.
Terraform keeps a record of everything it created, the *state*, and every run reads and
updates it, the pipeline's included. It lives in a storage account of yours, shared by
every environment (each under its own key), and stays when an environment is removed.
The state holds every secret of the deployment, so the account allows only Microsoft
sign-in (no access keys), and keeps old versions and deleted files for 30 days so a
damaged state can be recovered. Only the storage account's name is yours to choose
(unique across all of Azure, 3 to 24 lowercase letters and digits): set it after
`STATE_ACCOUNT=`, and keep the other names.

```bash
STATE_ACCOUNT=
az group create --name rg-terraform-state --location northeurope
az storage account create --name "$STATE_ACCOUNT" --resource-group rg-terraform-state \
  --sku Standard_LRS --https-only true --min-tls-version TLS1_2 \
  --allow-blob-public-access false --allow-shared-key-access false
az storage account blob-service-properties update --account-name "$STATE_ACCOUNT" \
  --resource-group rg-terraform-state --enable-versioning true \
  --enable-delete-retention true --delete-retention-days 30 \
  --enable-container-delete-retention true --container-delete-retention-days 30
# let yourself read and write the state with your Azure login (before creating the
# container: with no access keys, creating it needs this role)
az role assignment create --role "Storage Blob Data Contributor" \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --scope "$(az storage account show --name "$STATE_ACCOUNT" --query id -o tsv)"
```

The role takes a minute to apply. Then create the container; if it says you're not
authorised, wait a minute and run it again:

```bash
az storage container create --name tfstate --account-name "$STATE_ACCOUNT" --auth-mode login
```

**3. The sign-in.** Each environment has its own Entra ID app registration: the "app"
people sign in to. Only the people assigned to it get in, and you're assigned. The
script is in the Assay clone from step 1:

```bash
"$ASSAY/infra/azure/scripts/entra-app.sh" create "Assay (staging)"
```

It prints the **client ID** and a **client secret**, shown only now and valid one year:
keep the secret in your password manager for step 6. Then keep the client ID in this
terminal: run this line, paste the client ID and press Enter:

```bash
read -r ENTRA_CLIENT_ID
```

**4. The GitHub environment.** In the deployment repository's Settings → Environments,
create `staging` (and `production`, if you'll have one: limit its deployment branches to
`main`). Environments in a **private** repository need a paid GitHub plan (Pro, Team or
Enterprise); required reviewers there need Enterprise, so the gate on production is that
only people who can write to the repository can start the run. On GitHub Free, a private
repository has no environments: you'd need one workflow file per environment and a
federated credential per file, which this template doesn't cover.

**5. An Azure identity for the environment**, which GitHub logs in as with OIDC (no
stored password). Azure trusts it only from that GitHub environment, so a staging run
can't touch production.

**5a. What Azure will trust, and a check.** GitHub names each run of the environment to
Azure; this asks GitHub for that name:

```bash
PREFIX=$(gh api "repos/$REPO/actions/oidc/customization/sub" --jq '.sub_claim_prefix // empty' 2>/dev/null) \
  && SUBJECT="${PREFIX:-repo:$REPO}:environment:${ENV}" \
  || SUBJECT=
echo "${SUBJECT:-Not found on GitHub: $REPO}"
```

It should print something like `repo:your-org@123/your-repo@456:environment:staging`. If
it says **Not found on GitHub…**, fix `REPO` (or sign in with `gh auth login`) and run 5a
again. Don't go on until it looks right.

**5b. The identity:**

```bash
APP_ID=$(az ad app create --display-name "assay-deploy-$ENV" --query appId -o tsv)
az ad sp create --id "$APP_ID"
# trust GitHub's runs of that environment, and nothing else; "github-$ENV" is only the
# credential's label: keep it, the login fixes in "When something goes wrong" use it
az ad app federated-credential create --id "$APP_ID" --parameters "{\"name\": \"github-$ENV\", \"issuer\": \"https://token.actions.githubusercontent.com\", \"subject\": \"$SUBJECT\", \"audiences\": [\"api://AzureADTokenExchange\"]}"
# it creates resources and role assignments...
az role assignment create --assignee "$APP_ID" --role Owner \
  --scope "/subscriptions/$(az account show --query id -o tsv)"
# ...and reads and writes Terraform's state
az role assignment create --assignee "$APP_ID" --role "Storage Blob Data Contributor" \
  --scope "$(az storage account show --name "$STATE_ACCOUNT" --query id -o tsv)"
# check: the name it trusts must be 5a's
az ad app federated-credential list --id "$APP_ID" --query "[].subject" -o tsv
```

If a role assignment says the identity isn't found, Azure hasn't caught up yet: wait a
minute and run that line again. Owner on the subscription is the simplest; scope it to
the environment's resource group if your organisation requires it (the group must then
exist before the first run).

**6. The environment's variables and secrets**, for the pipeline to read:

```bash
gh variable set AZURE_CLIENT_ID          --env "$ENV" --repo "$REPO" --body "$APP_ID"
gh variable set AZURE_TENANT_ID          --env "$ENV" --repo "$REPO" --body "$(az account show --query tenantId -o tsv)"
gh variable set AZURE_SUBSCRIPTION_ID    --env "$ENV" --repo "$REPO" --body "$(az account show --query id -o tsv)"
gh variable set TF_STATE_RESOURCE_GROUP  --env "$ENV" --repo "$REPO" --body rg-terraform-state
gh variable set TF_STATE_STORAGE_ACCOUNT --env "$ENV" --repo "$REPO" --body "$STATE_ACCOUNT"
gh variable set TF_STATE_CONTAINER       --env "$ENV" --repo "$REPO" --body tfstate
gh variable set TF_STATE_KEY             --env "$ENV" --repo "$REPO" --body "assay-$ENV.tfstate"
gh variable set ENTRA_CLIENT_ID          --env "$ENV" --repo "$REPO" --body "$ENTRA_CLIENT_ID"
gh variable list --env "$ENV" --repo "$REPO"
```

The list should show the eight variables. Then the secret, on its own: run this line and
paste step 3's client secret when it asks:

```bash
gh secret set ENTRA_CLIENT_SECRET --env "$ENV" --repo "$REPO"
```

| Kind | Name | What it is |
|---|---|---|
| Variable | `AZURE_CLIENT_ID` | the identity from step 5, which the pipeline logs in as |
| Variable | `AZURE_TENANT_ID` | your Entra directory |
| Variable | `AZURE_SUBSCRIPTION_ID` | the subscription to deploy to |
| Variable | `TF_STATE_RESOURCE_GROUP`, `TF_STATE_STORAGE_ACCOUNT`, `TF_STATE_CONTAINER`, `TF_STATE_KEY` | where the state is (step 2), and the environment's key in it |
| Variable | `ENTRA_CLIENT_ID` | the sign-in from step 3 |
| Secret | `ENTRA_CLIENT_SECRET` | its client secret |
| Secret, optional | `JUDGE_API_KEY` | the LLM judge's API key: `gh secret set JUDGE_API_KEY --env "$ENV" --repo "$REPO"` |

The environment's values are in the repository's Settings → Environments → staging.
Optional repository variables `ASSAY_REPOSITORY` and `ASSAY_FE_REPOSITORY`, in Settings →
Secrets and variables → Actions → Variables, point the pipeline at other copies of the
code.

**7. The repository's content, then the first run.** Put in it the folders and the
workflow the tree at the top shows, from the Assay clone, and the `.gitignore`. Then pick a release of each
from the tags of [`cupelworks/assay`](https://github.com/cupelworks/assay/tags) and
[`cupelworks/assayfe`](https://github.com/cupelworks/assayfe/tags) (the newest of each is
a safe start: the FE's names the API releases it works with, and the pipeline checks the
pair before deploying), and set them in each `main.tf`:

- **The BE release, in the module's `source`.** Delete the line
  `source = "../../../modules/assay"` (the example's own copy of the module), and
  uncomment the line above it with your release:

  ```hcl
  source = "git::https://github.com/cupelworks/assay.git//infra/azure/modules/assay?ref=vX.Y.Z"
  ```
- **The FE release, `fe_version`:** `fe_version = "X.Y.Z"` with yours.

The rest of each `main.tf` works as it is; what you may change:

| | What | In `main.tf` |
|---|---|---|
| *Recommended* | Who gets the alert emails | `alert_emails` |
| *Recommended* | Your application under test, if your tests ask it for answers | `extra_environment` (`ASSAY_TARGET_*`), or later from Assay's Settings |
| *Optional* | The LLM judge: provider and model (its key is step 6's `JUDGE_API_KEY`) | `judge` |
| *Optional* | How many workers: `min_replicas` and `max_replicas` in `worker`, both set to that number (nothing scales the worker by itself) | `worker` |
| *Optional* | Region, resource group name, sizes, the worker's off switch, the upload limit | the module's inputs are in the Assay clone's `infra/azure/modules/assay/variables.tf` |

Then commit it all, `.gitignore` included, and push it to `main` with git: the first run
starts.

Follow it in the repository's **Actions** tab. The first run creates the environment
with placeholder images, then builds and releases the real ones: about 20 minutes, most
of it the database and the worker's image. Don't stop it halfway: Terraform would leave
its state locked and some resources unrecorded. Its last step, "Wait for every app's new
revision", ends with the FE's address:

```
Assay <BE version> with its FE <FE version> runs at https://ca-assay-staging-fe.<random>.northeurope.azurecontainerapps.io
```

**8. The sign-in's return address.** After signing in, Microsoft sends people back only
to an address the app registration lists. Give it the address step 7's run printed: run this
line, paste the address and press Enter:

```bash
read -r FE_URL
```

Then:

```bash
"$ASSAY/infra/azure/scripts/entra-app.sh" redirect "$ENTRA_CLIENT_ID" "$FE_URL"
```

It answers "Sign-in now returns to …". Open the address and sign in: until this is done,
Microsoft refuses the sign-in ("redirect URI mismatch"). `/api/docs` shows the API, and
`/api/health/worker` should answer `ready`. Expect one "worker not running" alert email
shortly after, then a "resolved" one: Azure's replica count lags the new worker.
Releases keep the address; only an environment created anew gets a new one, and needs
this step again.

**9. Optional: a button that removes staging.** Add `destroy.yml` to
`.github/workflows/` (from the Assay clone, as the tree shows) and push it with
`[skip ci]` in the commit message, so the push doesn't deploy. How to use it is in [Removing it](#with-github-actions-staging).

### Releasing

1. A new release is tagged in `cupelworks/assay` (`vX.Y.Z`) or `cupelworks/assayfe`.
2. In your deployment repository, change the version in `environments/staging/main.tf`
   (the `?ref=` or `fe_version`) and push to `main`: **staging deploys**.
3. Once staging looks right, make the same change in `environments/production/main.tf`,
   push, then **Run workflow → production**: production deploys only when started by
   hand, and only from `main`.

The pipeline stops before deploying when the FE doesn't work with that API (the FE's
`package.json` names the API versions it needs), or when a tag doesn't hold the version
it names. A version already built is reused, never built again over. Going back is the
same change, to the old versions.

It also stops, before applying anything, when Terraform's plan would **delete or replace
what holds the data**: the resource group, the database server, its database or the Key
Vault. That only happens for a setting Azure can't change in place, such as the region or
the database's geo-redundant backups, and it would lose the data. Make such a change
deliberately, from your machine, after a backup.

### Production

Production has its own sign-in, identity and GitHub environment. In the same terminal,
with `ENV=production`:

1. Step 3, with `create "Assay (production)"`, and its `ENTRA_CLIENT_ID`.
2. Step 4's `production` environment, if you didn't create it then.
3. Steps 5 and 6 as they are: `$ENV` names the identity `assay-deploy-production` and
   the state's key `assay-production.tfstate`.
4. In the repository's **Actions** tab: **Deploy Assay to Azure → Run workflow →
   production**.
5. Step 8, with the address that run prints.

### Keep Azure out of public repositories

The pipeline template skips itself in `cupelworks/assay`. In any public repository
holding Assay's code: no Azure variables or secrets, never a `pull_request_target`
workflow, and Settings → Actions → "Require approval for all outside collaborators".

## Running it

- **Logs:** in Log Analytics, `ContainerAppConsoleLogs_CL`; every Assay log line is JSON
  (`Log_s`), with the request ID and the run ID as fields. A 500's body carries the same
  request ID.
- **Alerts** (with `alert_emails`): the worker hasn't run for 15 minutes; the FE answered
  server errors (the API's, or the API unreachable); the API, the worker or Beat logged an
  error. The worker's alert is off while `worker_enabled` is false. Expect one worker
  alert email just after an environment is created: Azure's replica count lags the new
  worker, and the alert resolves itself (a second email) once it catches up.
- **Settings:** the application under test and the judge can also be changed from the UI
  (Settings); saved settings win over the environment. Settings › Operations shows how
  the deployment is set up.
- **The sign-in**, from the Assay clone's `infra/azure/scripts/`, with its client ID in
  `ENTRA_CLIENT_ID` (`read -r ENTRA_CLIENT_ID`, then paste it):

  | When | Command |
  |---|---|
  | To find the client ID again | `az ad app list --display-name "Assay (staging)" --query "[].appId" -o tsv` |
  | To let someone else in | `entra-app.sh allow "$ENTRA_CLIENT_ID" "$SIGN_IN_NAME"`, with their sign-in name in `SIGN_IN_NAME` |
  | Before the secret expires (a year) | `entra-app.sh rotate "$ENTRA_CLIENT_ID"`: a new secret beside the old one; apply it (the pipeline's `ENTRA_CLIENT_SECRET`, then a run), then delete the old one with the command it prints |

  Sign out (`/.auth/logout`) ends the session with Assay; the Microsoft session stays, so
  coming back signs you in at once.
- **Secrets that expire:** the sign-in's client secret after a year (above); the judge's
  key whenever its provider says.
- **Images pile up** in the registry, one per release and app. Clean up old ones with
  `az acr run --registry "$REGISTRY" --cmd "acr purge --filter 'assay-.*:.*' --ago 30d --keep 5" /dev/null`.
- **The broker keeps nothing on disk.** If Redis restarts, queued tasks are lost, and
  Beat's reconciliation scan sends the waiting runs again. Point
  `ASSAY_CELERY_BROKER_URL` at a managed Redis through `extra_environment` if you prefer.

## When something goes wrong

- **`RequestDisallowedByAzure` … "not accepting new customers"** on the first apply: the
  region refuses new resources for your subscription. Destroy what was created and pick
  another region (see Before you start).
- **The pipeline stops at "Log in to Azure" with an `AADSTS…` error.** The pipeline logs
  in without a password. For each run, GitHub gives it a signed name, printed just above
  the error as `subject claim - repo:…:environment:staging`. Azure lets it in only if the
  identity (`AZURE_CLIENT_ID`) trusts that exact name: its *federated credential*, from
  Deploy with GitHub Actions, step 5. The error code says what's wrong:

  | Error | Means | Fix |
  |---|---|---|
  | `AADSTS70025` "has no configured federated identity credentials" | The identity trusts no name: step 5's credential was never created | Create it (below) |
  | `AADSTS700213` "No matching federated identity record" | It trusts a different name | Correct it (below) |
  | `AADSTS700016` "Application … was not found" | `AZURE_CLIENT_ID` names an identity that doesn't exist (deleted, or a typo) | Redo step 5, and set the new `AZURE_CLIENT_ID` (step 6) |

  Use the name from the error's `subject claim` line and the identity's client ID:

  ```bash
  # AADSTS70025: create it
  az ad app federated-credential create --id <AZURE_CLIENT_ID> --parameters '{"name": "github-staging", "issuer": "https://token.actions.githubusercontent.com", "subject": "<subject claim>", "audiences": ["api://AzureADTokenExchange"]}'
  # AADSTS700213: correct it
  az ad app federated-credential update --id <AZURE_CLIENT_ID> --federated-credential-id github-staging --parameters '{"subject": "<subject claim>"}'
  ```

  Then open the failed run and **Re-run jobs**. If it fails the same way at once, wait a
  minute: Azure takes a moment to apply it.
- **"Terraform init" fails with `403` or `AuthorizationPermissionMismatch`**: the identity
  can't read Terraform's state. It needs **Storage Blob Data Contributor** on the state's
  storage account, step 5's last role.
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

Removing an environment deletes **everything in it, the database and its backups
included**. Copy anything you want to keep first. There are two ways:

| | With GitHub Actions | From your machine |
|---|---|---|
| **Which environment** | staging only | any, production included |
| **What you need** | the deploy's setup, nothing more | Terraform and the Azure CLI, signed in as Owner on the subscription, with Storage Blob Data Contributor on the state's storage account |
| **How** | a button in GitHub | three steps in a terminal |

Production can't be removed with the pipeline on purpose: a private repository on GitHub
Team can't require anyone's approval before a run, so whoever can push could press it.

Either way, the **sign-in's app registration stays**: the pipeline has no rights in your
directory, and you may want to keep it for the next environment. Delete it yourself if
you're done with it (below).

### With GitHub Actions (staging)

**Once:** the destroy workflow in your deployment repository, as in Deploy with GitHub
Actions, step 9.

**Then, each time:**

1. In your deployment repository on GitHub: **Actions → Destroy Assay's staging → Run
   workflow**.
2. Type `staging` in the box and press **Run workflow**. Anything else stops the run
   before it touches Azure.
3. Wait for it to finish: up to 40 minutes, most of it Azure deleting the Container Apps
   environment. It waits for any deploy that's running, and a deploy started meanwhile
   waits for it.

It deletes the log workspace for good first (otherwise Azure keeps it 14 days and the next
staging brings the old logs back), then runs `terraform destroy`. Its last lines repeat
the command that deletes the sign-in, with its client ID, if you're done with it.

### From your machine (any environment)

Run everything in **one terminal window**: the values you set in step 1 last only as long
as that window. The commands use staging; for production, use `environments/production`
and its names (`rg-assay-prod`, `log-assay-prod`).

**1. Get Terraform ready.** It needs its record of what it created (the *state*, in your
storage account) and the three values the configuration always asks for.

If you deployed from your machine, use the folder you deployed from
(`infra/azure/example/environments/staging` in the Assay clone) with
`TF_VAR_entra_client_secret` set as in Deploy from your machine, step 4, and go to step 2.
If the pipeline deployed it, in a clone of your deployment repository:

```bash
cd environments/staging
cp backend.hcl.example backend.hcl
```

Fill in `backend.hcl` with the GitHub environment's `TF_STATE_*` values: the storage
account's name (`TF_STATE_STORAGE_ACCOUNT`) above all, and the key (`TF_STATE_KEY`).
Then:

```bash
terraform init -backend-config=backend.hcl
```

Then the two values the configuration asks for, one line each: run the line, paste the
value and press Enter. First the GitHub environment's `AZURE_SUBSCRIPTION_ID`:

```bash
read -r TF_VAR_subscription_id && export TF_VAR_subscription_id
```

Then its `ENTRA_CLIENT_ID`:

```bash
read -r TF_VAR_entra_client_id && export TF_VAR_entra_client_id
```

Then the secret, on its own: run this line, paste the sign-in's client secret and press
Enter (nothing shows while you paste):

```bash
read -s TF_VAR_entra_client_secret && export TF_VAR_entra_client_secret
```

The GitHub values are in the deployment repository's **Settings → Environments →
staging**. `terraform init` should end with "Terraform has been successfully
initialized!", and `terraform state list` should then list the environment's resources.
If init asks about `-reconfigure`, run it again with `-reconfigure`.

**2. Delete the logs for good,** if you'll create the environment again: otherwise Azure
keeps the log workspace 14 days and the new environment brings the old logs back.

```bash
az monitor log-analytics workspace delete --resource-group rg-assay-staging \
  --workspace-name log-assay-staging --force true --yes
```

**3. Destroy**, and confirm with `yes` when Terraform lists what it will delete:

```bash
terraform destroy
```

Then, only if you're done with the sign-in, delete it: run the first line, paste its
client ID (the GitHub environment's `ENTRA_CLIENT_ID`, or `entra_client_id` in
`terraform.tfvars`) and press Enter, then the second:

```bash
read -r ENTRA_CLIENT_ID
```

```bash
az ad app delete --id "$ENTRA_CLIENT_ID"
```

Deleting the Container Apps environment can take 40 minutes. `terraform destroy` waits;
it isn't stuck.

### What's left afterwards

- **Gone:** every resource of the environment, with the resource group Azure made for it
  (`<resource group>-infra`). Terraform purges the Key Vault, which Azure would otherwise
  keep 7 days.
- **Kept:** the state's storage account (other environments use it), the pipeline's
  identity, the GitHub environment with its variables and secrets, and the sign-in's app
  registration unless you deleted it.
- **Network Watcher:** Azure may have created one for the region, usually in a resource
  group named `NetworkWatcherRG`, for the first virtual network there. It's Azure's own,
  free, and shared by every virtual network in that region, so leave it in place.

### Creating it again

**With GitHub Actions, the next push to `main` creates staging again**, from scratch. To keep
it removed, disable the deploy (Actions → Deploy Assay to Azure → **Disable workflow**).
To create it again:

1. If you deleted the sign-in: make a new one, and put its client ID and secret in the
   GitHub environment (Deploy with GitHub Actions, steps 3 and 6).
2. Push to `main`, or **Run workflow** on the deploy.
3. Give the sign-in the new environment's address, as in Deploy with GitHub Actions, step
   8: a new environment gets a new one.

From your machine, it's Deploy from your machine from step 5, with step 6 again for the
new address.

## Limits

- The pipeline is a GitHub Actions workflow; other CI systems have no template here.
- Terraform's Azure provider is pinned to `~> 4.81` (version 5 isn't supported yet), and
  `azapi` to `~> 2.5`.
- The module, the example and the script are checked with `terraform test` against a
  mocked provider, `terraform validate` and `bash -n` (see `.github/workflows/ci.yml`),
  not by deploying to Azure in this repository.

## Using the module from your own configuration

Instead of the example's layout, your own Terraform can call the module. Pin it to a
release, so an update to Assay never changes your infrastructure unannounced:

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
