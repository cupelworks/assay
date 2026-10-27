# Production: the releases it runs and its sizes. It deploys only when started
# by hand (Run workflow → production), from main: bump the versions here once
# staging has run them.
#
# What to change before deploying: infra/azure/README.md, step 4 from your machine
# (the settings), step 7 with the pipeline (the deployment repository's files).

locals {
  # The FE release production runs (cupelworks/assayfe's tag v2.5.2). The BE's is the
  # module's ?ref= below. The pipeline reads both from this file.
  fe_version = "2.5.2"
}

resource "azurerm_resource_group" "assay" {
  name     = "rg-assay-prod"
  location = "northeurope"
}

module "assay" {
  # In your deployment repository, the module at a release, which is also the
  # BE version deployed:
  #   source = "git::https://github.com/cupelworks/assay.git//infra/azure/modules/assay?ref=v2.4.2"
  source = "../../../modules/assay"

  environment         = "prod"
  location            = azurerm_resource_group.assay.location
  resource_group_name = azurerm_resource_group.assay.name

  image_tag    = var.image_tag
  fe_image_tag = var.fe_image_tag

  entra = {
    client_id = var.entra_client_id
  }
  entra_client_secret = var.entra_client_secret

  api = { cpu = 0.5, memory = "1Gi", min_replicas = 1, max_replicas = 2 }
  # One worker; for more, set both to the number wanted (nothing scales it)
  worker = { cpu = 1, memory = "2Gi", min_replicas = 1, max_replicas = 1 }

  # B2s: more connections than B1ms allows, for every replica's pool. Geo-
  # redundant backups can only be chosen when the server is created.
  postgres = {
    sku_name              = "B_Standard_B2s"
    version               = "16"
    storage_mb            = 32768
    backup_retention_days = 35
    geo_redundant_backup  = true
    auto_grow             = true
  }

  alert_emails = [] # e.g. ["you@example.com"]

  # judge = { provider = "anthropic", model = "claude-sonnet-5-5" }
  judge_api_key = var.judge_api_key

  # Any other Assay setting, e.g. the application under test:
  # extra_environment = { ASSAY_TARGET_URL = "https://my-app.example.com/api/chat" }
}
