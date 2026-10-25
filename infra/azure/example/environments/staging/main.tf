# Staging: the releases it runs and its sizes. To release, change the versions
# and push to main: the pipeline deploys staging.
#
# What to change before deploying: infra/azure/README.md, "What you change".

locals {
  # The FE release staging runs (cupelworks/assayfe's tag v2.5.2). The BE's is the
  # module's ?ref= below. The pipeline reads both from this file.
  fe_version = "2.5.2"
}

resource "azurerm_resource_group" "assay" {
  name     = "rg-assay-staging"
  location = "northeurope"
}

module "assay" {
  # In your deployment repository, the module at a release, which is also the
  # BE version deployed:
  #   source = "git::https://github.com/cupelworks/assay.git//infra/azure/modules/assay?ref=v2.4.2"
  source = "../../../modules/assay"

  environment         = "staging"
  location            = azurerm_resource_group.assay.location
  resource_group_name = azurerm_resource_group.assay.name

  image_tag    = var.image_tag
  fe_image_tag = var.fe_image_tag

  entra = {
    client_id = var.entra_client_id
  }
  entra_client_secret = var.entra_client_secret

  # Cheap while nobody tests: the API sleeps when idle (a few seconds to wake),
  # and the worker can be switched off
  api            = { cpu = 0.5, memory = "1Gi", min_replicas = 0, max_replicas = 1 }
  worker         = { cpu = 1, memory = "2Gi", min_replicas = 1, max_replicas = 1 }
  worker_enabled = true # false between tests: runs wait as Pending until it's back
  # postgres: the module's default, B1ms with 7 days of backups

  alert_emails = [] # e.g. ["you@example.com"]

  # The LLM judge (optional), its key from the JUDGE_API_KEY secret:
  # judge = { provider = "anthropic", model = "claude-sonnet-5-5" }
  judge_api_key = var.judge_api_key

  # Any other Assay setting, e.g. the application under test:
  # extra_environment = { ASSAY_TARGET_URL = "https://my-app.example.com/api/chat" }
}
