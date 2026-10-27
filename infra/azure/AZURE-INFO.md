# What Assay creates in Azure

Every resource one environment of Assay creates (staging or production), why it's
there and what it costs; what lives outside the module; and the timings and quirks worth
knowing before the first deployment. How to deploy is in [README.md](README.md).

**How resources are named.** Most are named after their type, Assay and the environment:
for staging, `vnet-assay-staging`, `ca-assay-staging-api`. The tables write that middle
part as `<prefix>`. A few names must be unique across all of Azure, since they become web
addresses: the container registry, the Key Vault and the database server. They end with
five random characters, written `<random>` in the tables. The database server keeps its
hyphens (`psql-assay-staging-7k2qz`); the registry and Key Vault have none, which the
registry doesn't allow (`crassaystaging7k2qz`), and the tables write that form as
`<compact>`.

## Contents

- [At a glance](#at-a-glance)
- [Every resource](#every-resource)
  - [The resource group](#the-resource-group)
  - [Network](#network)
  - [Database](#database)
  - [Secrets](#secrets)
  - [Images](#images)
  - [Identity and access](#identity-and-access)
  - [Apps](#apps)
  - [Sign-in](#sign-in)
  - [Monitoring](#monitoring)
  - [Terraform's own helpers](#terraforms-own-helpers)
- [Created by Azure](#created-by-azure)
- [Outside the module](#outside-the-module)
- [What it costs](#what-it-costs)
- [The worker's off switch](#the-workers-off-switch)
- [Timings and quirks](#timings-and-quirks)

## At a glance

**One environment is 19 resources in Azure:** its resource group, and the 18 the Azure
portal lists inside it:

- **the environment's container:** 1 resource group;
- **the private network:** 1 virtual network, 1 private DNS zone;
- **data and secrets:** 1 database server, 1 Key Vault;
- **images and identity:** 1 container registry, 1 managed identity;
- **running Assay:** 1 Container Apps environment, 5 apps (FE, API, worker, Beat, Redis)
  and 1 migration job;
- **watching it:** 1 log workspace, and the alerts: 1 email group and 3 alert rules.

Without alert emails, it's 15 (no email group or alert rules). With the worker switched
off, there's one app fewer, and its alert goes too.

**Some of those contain smaller pieces,** which the portal shows inside them rather than as
separate resources: the network's 2 subnets and DNS link, the server's database, the Key
Vault's 4 secrets (5 with a judge key), 3 permissions, and the sign-in settings on the FE.

**Terraform's plan says 35, not 19** (with alert emails and no judge key; 36 with a judge
key, 31 without alert emails), because it counts those pieces too, plus 4 helpers of its
own that never exist in Azure (random passwords, a random name ending, a short wait).

**Staging plus production** is twice all that. Azure also adds two things of its own: see
[Created by Azure](#created-by-azure).

## Every resource

### The resource group

| Resource | Name | Why |
|---|---|---|
| Resource group | `rg-assay-<environment>`, e.g. `rg-assay-staging` | Holds the whole environment, so it's created, found, billed and removed as one. The ready-made configuration in `example/` creates it; the module itself only needs one to exist, so you can also use a group you already have. |

### Network

| Resource | Name | Why |
|---|---|---|
| Virtual network | `vnet-<prefix>` | The private network. Nothing in it is reachable from the internet except the FE. |
| Subnet (part) | `snet-apps`, a /23 | The Container Apps environment. A /23: Azure's minimum for this kind of environment (workload profiles) is a /27; the module leaves room. |
| Subnet (part) | `snet-database`, a /28 | PostgreSQL, delegated to it, with the `Microsoft.Storage` endpoint PostgreSQL uses for its backups. |
| Private DNS zone | `<prefix>.private.postgres.database.azure.com` | Lets the apps find the database by name on the private network. |
| DNS link (part) | `vnet-<prefix>` | Connects that zone to the virtual network. |

### Database

| Resource | Name | Why |
|---|---|---|
| PostgreSQL Flexible Server | `psql-<prefix>-<random>` | Everything Assay keeps: datasets, tests, runs and their results. Private, TLS required, backups by Azure. |
| Database (part) | `assay` | Assay's database on that server. |

### Secrets

| Resource | Name | Why |
|---|---|---|
| Key Vault | `kv<compact>` | Holds every secret; the apps read them with their identity, so no password sits in their settings or in the pipeline. |
| Secret (part) | `database-url` | The database's address, with its password. |
| Secret (part) | `redis-password`, `broker-url` | The broker's password, and its address with it. |
| Secret (part) | `entra-client-secret` | The sign-in's client secret. |
| Secret (part, optional) | `judge-api-key` | The LLM judge's API key, when one is given. |

### Images

| Resource | Name | Why |
|---|---|---|
| Container registry (Basic) | `cr<compact>` | The environment's own images: `assay-api`, `assay-worker`, `assay-fe`, each tagged with its version. Built there by the pipeline. |

### Identity and access

| Resource | Name | Why |
|---|---|---|
| Managed identity | `id-<prefix>` | The one identity every app uses: pulls images, reads secrets. |
| Role assignment (part) | AcrPull, for `id-<prefix>` | Lets the apps pull from the registry. |
| Role assignment (part) | Key Vault Secrets User, for `id-<prefix>` | Lets the apps read the secrets. |
| Role assignment (part) | Key Vault Secrets Officer, for whoever runs Terraform | Lets Terraform (you, or the pipeline) write the secrets. |

### Apps

| Resource | Name | Why |
|---|---|---|
| Container Apps environment | `cae-<prefix>` | Runs the apps below on the private network; Consumption profile, billed per app. |
| Container app | `ca-<prefix>-fe` | **The only thing reachable from the internet**, behind the sign-in: nginx serving the FE and passing `/api` on to the API. |
| Container app | `ca-<prefix>-api` | Assay's API, internal only, served under `/api`. |
| Container app | `ca-<prefix>-worker` | Scores the runs. Always runs as many replicas as `worker.min_replicas` says (1 by default): it takes no traffic, so Azure never scales it. Absent while switched off. |
| Container app | `ca-<prefix>-beat` | Exactly one: schedules the scan that sends lost runs again. |
| Container app | `ca-<prefix>-redis` | The broker between the API and the worker; private, password-protected, keeps nothing on disk. |
| Container Apps job | `caj-<prefix>-migrate` | Runs the database migrations before each release. |

### Sign-in

| Resource | Name | Why |
|---|---|---|
| Sign-in settings (part) | `current`, on `ca-<prefix>-fe` | Entra ID sign-in in front of the FE: nothing is served without signing in. The only resource set through the `azapi` provider. |

### Monitoring

| Resource | Name | Why |
|---|---|---|
| Log Analytics workspace | `log-<prefix>` | Every app's logs, one JSON record per line, searchable by field. |
| Action group (with alerts) | `ag-<prefix>` | Who gets the alert emails. |
| Metric alert (with alerts) | `alert-<prefix>-worker-not-running` | No worker replica for 15 minutes: runs would wait. Absent while the worker is off. |
| Metric alert (with alerts) | `alert-<prefix>-server-errors` | The FE answered server errors: the API's, or the API unreachable. |
| Log alert (with alerts) | `alert-<prefix>-errors-logged` | The API, the worker or Beat logged an error. |

### Terraform's own helpers

Never created in Azure; they only exist in Terraform's state.

| Helper | Why |
|---|---|
| `random_string.suffix` | The five random characters in the globally unique names. |
| `random_password.postgres`, `random_password.redis` | The database's and the broker's passwords, generated once and stored in Key Vault. |
| `time_sleep.roles` | Waits a minute after the role assignments, which Azure takes a moment to apply. |

## Created by Azure

| What | Why | Remove it? |
|---|---|---|
| Resource group `<resource group>-infra` | The Container Apps environment's own plumbing: its load balancer and public address. Azure manages it. | Goes with the environment. |
| Network Watcher, usually in `NetworkWatcherRG` | Created by Azure for the first virtual network in a region. Free, shared by every virtual network in that region. | **No: leave it in place.** |

## Outside the module

| What | How many | Where | Cost |
|---|---|---|---|
| The sign-in's app registration (`Assay (<environment>)`) | One per environment | Your Entra directory, created by `scripts/entra-app.sh` | Free |
| The pipeline's identity (`assay-deploy-<environment>`), with its OIDC trust and two roles (Owner on the subscription, Storage Blob Data Contributor on the state account) | One per environment | Your Entra directory and subscription | Free |
| Terraform's state: a resource group, a storage account, a container | One, shared by every environment | `rg-terraform-state` | A few cents a month |
| GitHub environments, variables and secrets | One environment per Azure environment | Your private deployment repository | Free on a paid plan (see README) |
| A budget | One, optional | The subscription | Free |

## What it costs

| Kind | Resources | How it's billed |
|---|---|---|
| **Billed by size and time** | The container apps and the job; PostgreSQL | Container Apps per vCPU-second and GiB-second while a replica runs (less when idle), with a monthly free allowance per subscription (180,000 vCPU-seconds and 360,000 GiB-seconds). PostgreSQL per hour by size, plus its storage. |
| **Billed by use** | Log Analytics, the registry, Key Vault, the alerts, the DNS zone | Logs per GB taken in (31 days kept at no extra cost); the registry a small monthly fee with 10 GB included; Key Vault per operation; each alert rule a small monthly fee; the DNS zone $0.50 a month (list price as of October 2026). |
| **Free** | The resource group, the virtual network and subnets, the identity, the role assignments, the action group's emails, the sign-in, the Container Apps environment itself | |

**Most of the bill is the worker, then the database.** The worker's size is what moves the
total most: with the NLP models loaded it uses **about 800 MB**, so the default
1 vCPU / 2 GiB holds it comfortably; 2 vCPU / 4 GiB would double its cost and help only
when many NLP checks run at once. In staging, the API sleeps
when idle (`min_replicas = 0`) and the worker can be switched off. Estimate your sizes with
the [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/), and set
a budget before the first apply. Azure's cost data lags a day or more behind.

## The worker's off switch

- **Switching it off:** in `environments/staging/main.tf`, set `worker_enabled = false`
  and push. The pipeline applies, and Terraform removes the worker app.
- **Switching it back on:** set `worker_enabled = true` and push. The pipeline applies,
  Terraform creates the worker app again from the same image and version, and a minute or
  two later it's running. From a machine, it's the same with a local `terraform apply`.

While it's off:
- runs wait as `Pending`, and `GET /health/worker` says `no_worker`;
- once it's back, the worker picks them up from Redis, or the reconciliation scan sends them
  again;
- everything else works as usual: browsing, editing, uploads, settings.

Nothing switches it on automatically. Scaling it on its queue would let Azure stop a
worker in the middle of a long run (a statistics batch), and Assay doesn't recover a run
interrupted while `Running` yet.

## Timings and quirks

| What | Expect |
|---|---|
| **Creating an environment** | About 20 minutes the first time: PostgreSQL 5–6, the Container Apps environment about 3, building the worker's image (about 3.5 GB) several more. |
| **A release** | Minutes. Every app moves to its new revision only once it's healthy. Nothing is destroyed. |
| **Deleting an environment** | Up to about 40 minutes: the Container Apps environment releases its share of the virtual network last. `terraform destroy` waits; it isn't stuck. |
| **Azure's read delay** | Right after creating something, Azure sometimes reports it missing, and Terraform stops with "Provider produced inconsistent result after apply". Apply again; README, "When something goes wrong". |
| **Alert emails you can ignore** | The worker alert can fire once just after an environment is created (the metric lags the new replica), then resolves itself. A destroy can fire the errors alert (the worker loses Redis first). |
| **What a destroy leaves behind** | The log workspace is kept for 14 days after it's deleted, and re-creating one with the same name brings it back with its data, unless you delete it for good first (README, "Removing it"). Key Vault is kept 7 days, and Terraform purges it on destroy. |
