# Plans the module against a mocked Azure provider (no account needed):
# terraform init && terraform test, from the module's folder.

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "00000000-0000-0000-0000-000000000000"
      object_id = "00000000-0000-0000-0000-000000000001"
    }
  }

  mock_resource "azurerm_container_registry" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.ContainerRegistry/registries/crassaydevabcde"
      login_server = "crassaydevabcde.azurecr.io"
    }
  }

  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/log-assay-dev"
    }
  }

  mock_resource "azurerm_virtual_network" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet-assay-dev"
    }
  }

  mock_resource "azurerm_subnet" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet-assay-dev/subnets/snet"
    }
  }

  mock_resource "azurerm_private_dns_zone" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Network/privateDnsZones/assay-dev.private.postgres.database.azure.com"
    }
  }

  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.DBforPostgreSQL/flexibleServers/psql-assay-dev"
      fqdn = "psql-assay-dev.postgres.database.azure.com"
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-assay-dev"
      principal_id = "00000000-0000-0000-0000-000000000002"
    }
  }

  mock_resource "azurerm_key_vault" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kvassaydev"
    }
  }

  mock_resource "azurerm_key_vault_secret" {
    defaults = {
      versionless_id = "https://kvassaydev.vault.azure.net/secrets/secret"
    }
  }

  mock_resource "azurerm_container_app" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.App/containerApps/ca-assay-dev-fe"
    }
  }

  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.App/managedEnvironments/cae-assay-dev"
    }
  }
}

mock_provider "time" {}

mock_provider "azapi" {}

variables {
  environment         = "dev"
  location            = "northeurope"
  resource_group_name = "rg-assay-dev"
  entra = {
    client_id = "11111111-1111-1111-1111-111111111111"
  }
  entra_client_secret = "test-secret"
}

run "first_apply_runs_placeholders_and_keeps_the_api_private" {
  command = plan

  assert {
    condition     = azurerm_container_app.api.template[0].container[0].image == "mcr.microsoft.com/k8se/quickstart:latest"
    error_message = "Without an image tag, the API runs the placeholder."
  }

  assert {
    condition     = azurerm_container_app.fe.template[0].container[0].image == "mcr.microsoft.com/k8se/quickstart:latest" && azurerm_container_app.fe.ingress[0].target_port == 80
    error_message = "Without an FE image tag, the FE runs the placeholder, on its port 80."
  }

  assert {
    condition     = azurerm_container_app.api.ingress[0].external_enabled == false
    error_message = "The API is reachable only inside the environment."
  }

  assert {
    condition     = azurerm_container_app.fe.ingress[0].external_enabled
    error_message = "The FE is the app reachable from the internet."
  }

  assert {
    condition     = length(azurerm_container_app.fe.template[0].container[0].liveness_probe) == 0
    error_message = "The placeholder has no /healthz to probe."
  }

  assert {
    condition     = azurerm_container_app.api.ingress[0].target_port == 80
    error_message = "The placeholder serves on port 80."
  }

  assert {
    condition     = length(azurerm_key_vault_secret.judge_api_key) == 0
    error_message = "No judge key stores no secret."
  }

  assert {
    condition     = azurerm_container_app.beat.template[0].min_replicas == 1 && azurerm_container_app.beat.template[0].max_replicas == 1
    error_message = "There is exactly one Beat."
  }
}

run "a_release_runs_the_images_with_their_settings" {
  # apply against the mocks: the registry's address is known only after an apply
  command = apply

  variables {
    image_tag             = "2.4.1"
    fe_image_tag          = "2.5.2"
    dataset_upload_max_mb = 25
    judge = {
      provider = "openai"
      model    = "gpt-5"
    }
    judge_api_key        = "sk-test"
    cors_allowed_origins = ["https://app.example.com"]
    extra_environment    = { ASSAY_TARGET_URL = "https://my-app.example.com/chat" }
  }

  assert {
    condition     = azurerm_container_app.api.template[0].container[0].image == "crassaydevabcde.azurecr.io/assay-api:2.4.1"
    error_message = "The API runs the tagged image from the deployment's registry."
  }

  assert {
    condition     = azurerm_container_app.worker[0].template[0].container[0].image == "crassaydevabcde.azurecr.io/assay-worker:2.4.1"
    error_message = "The worker runs the tagged worker image."
  }

  assert {
    condition     = azurerm_container_app.fe.template[0].container[0].image == "crassaydevabcde.azurecr.io/assay-fe:2.5.2"
    error_message = "The FE runs its own version's image."
  }

  assert {
    condition     = !azurerm_container_app.api.ingress[0].external_enabled && azurerm_container_app.api.ingress[0].target_port == 8000
    error_message = "The API stays internal, on Assay's port."
  }

  assert {
    condition     = azurerm_container_app.fe.ingress[0].external_enabled && azurerm_container_app.fe.ingress[0].target_port == 8080
    error_message = "The FE is public, on its nginx's port."
  }

  assert {
    condition     = one(azurerm_container_app.fe.template[0].container[0].liveness_probe[*].path) == "/healthz" && one(azurerm_container_app.fe.template[0].container[0].readiness_probe[*].path) == "/healthz"
    error_message = "The FE is probed on /healthz."
  }

  assert {
    condition = startswith(
      one([for env in azurerm_container_app.fe.template[0].container[0].env : env.value if env.name == "ASSAY_API_URL"]),
      "https://",
    )
    error_message = "The FE reaches the API at its address inside the environment, over HTTPS."
  }

  assert {
    condition = (
      one([for env in azurerm_container_app.api.template[0].container[0].env : env.value if env.name == "ASSAY_DATASET_UPLOAD_MAX_MB"]) == "25" &&
      one([for env in azurerm_container_app.fe.template[0].container[0].env : env.value if env.name == "ASSAY_UPLOAD_MAX_MB"]) == "26"
    )
    error_message = "One upload limit: the API's, and 1 MB more for the FE's nginx."
  }

  assert {
    condition     = one([for env in azurerm_container_app.api.template[0].container[0].env : env.value if env.name == "ASSAY_ROOT_PATH"]) == "/api"
    error_message = "The API is told it's served under /api."
  }

  assert {
    condition     = !contains(azurerm_container_app.worker[0].template[0].container[0].env[*].name, "ASSAY_ROOT_PATH")
    error_message = "The root path and the upload limit are the API's alone."
  }

  assert {
    condition     = length(azurerm_key_vault_secret.judge_api_key) == 1
    error_message = "A judge key is stored in Key Vault."
  }

  assert {
    condition = contains(
      [for env in azurerm_container_app.worker[0].template[0].container[0].env : env.name if env.secret_name == "judge-api-key"],
      "OPENAI_API_KEY",
    )
    error_message = "The worker reads an OpenAI judge's key as OPENAI_API_KEY."
  }

  assert {
    condition = alltrue([
      for name in ["ASSAY_DATABASE_URL", "ASSAY_CELERY_BROKER_URL", "ASSAY_CELERY_RESULT_BACKEND", "PGSSLMODE", "ASSAY_JUDGE_PROVIDER", "ASSAY_JUDGE_MODEL", "ASSAY_CORS_ALLOWED_ORIGINS", "ASSAY_TARGET_URL"] :
      contains(azurerm_container_app.api.template[0].container[0].env[*].name, name)
    ])
    error_message = "The API gets the database, the broker, TLS, the judge, CORS and the extra settings."
  }

  assert {
    condition     = !contains(azurerm_container_app.api.template[0].container[0].env[*].name, "OPENAI_API_KEY")
    error_message = "Only the worker gets the judge's key."
  }

  assert {
    condition     = azurerm_container_app_job.migrate.template[0].container[0].command == tolist(["alembic", "upgrade", "head"])
    error_message = "The migration job runs alembic."
  }
}

run "nothing_is_served_without_signing_in" {
  command = plan

  assert {
    condition     = azapi_resource.fe_sign_in.parent_id == azurerm_container_app.fe.id && azapi_resource.fe_sign_in.body.properties.platform.enabled
    error_message = "Sign-in is on, on the FE."
  }

  assert {
    condition     = azapi_resource.fe_sign_in.body.properties.globalValidation.unauthenticatedClientAction == "RedirectToLoginPage"
    error_message = "Without signing in, a page goes to the sign-in and a call gets a 403; nothing is served."
  }

  assert {
    condition     = azapi_resource.fe_sign_in.body.properties.identityProviders.azureActiveDirectory.registration.openIdIssuer == "https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0"
    error_message = "Only the directory Terraform runs in signs in, by default."
  }

  assert {
    condition     = azapi_resource.fe_sign_in.body.properties.identityProviders.azureActiveDirectory.registration.clientSecretSettingName == one(azurerm_container_app.fe.secret[*].name)
    error_message = "The sign-in reads its client secret from the FE app's secret, from Key Vault."
  }

  assert {
    condition     = azurerm_key_vault_secret.entra_client_secret.value == "test-secret"
    error_message = "The client secret is kept in Key Vault."
  }
}

run "another_directory_can_be_named" {
  command = plan

  variables {
    entra = {
      client_id = "11111111-1111-1111-1111-111111111111"
      tenant_id = "22222222-2222-2222-2222-222222222222"
    }
  }

  assert {
    condition     = azapi_resource.fe_sign_in.body.properties.identityProviders.azureActiveDirectory.registration.openIdIssuer == "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/v2.0"
    error_message = "A named directory is the one that signs in."
  }
}

run "a_client_id_that_isnt_a_guid_is_refused" {
  command = plan

  variables {
    entra = {
      client_id = "assay"
    }
  }

  expect_failures = [var.entra]
}

run "no_alert_without_addresses" {
  command = plan

  assert {
    condition     = length(azurerm_monitor_action_group.email) == 0 && length(azurerm_monitor_metric_alert.worker_not_running) == 0 && length(azurerm_monitor_metric_alert.server_errors) == 0 && length(azurerm_monitor_scheduled_query_rules_alert_v2.errors_logged) == 0
    error_message = "Without alert addresses, no alert is created."
  }

  assert {
    condition     = !azurerm_postgresql_flexible_server.this.geo_redundant_backup_enabled && !azurerm_postgresql_flexible_server.this.auto_grow_enabled
    error_message = "Geo-redundant backups and auto-grow are opt-in."
  }
}

run "production_sizing_and_alerts" {
  command = plan

  variables {
    alert_emails = ["ops@example.com"]
    postgres = {
      sku_name              = "B_Standard_B2s"
      version               = "16"
      storage_mb            = 32768
      backup_retention_days = 35
      geo_redundant_backup  = true
      auto_grow             = true
    }
  }

  assert {
    condition     = one(azurerm_monitor_action_group.email[0].email_receiver[*].email_address) == "ops@example.com"
    error_message = "Alerts email the given address."
  }

  assert {
    condition     = length(azurerm_monitor_metric_alert.worker_not_running) == 1 && length(azurerm_monitor_metric_alert.server_errors) == 1 && length(azurerm_monitor_scheduled_query_rules_alert_v2.errors_logged) == 1
    error_message = "With an address, the three alerts exist."
  }

  assert {
    condition     = one(azurerm_monitor_metric_alert.server_errors[0].scopes) == azurerm_container_app.fe.id
    error_message = "Server errors are counted where every request passes: the FE."
  }

  assert {
    condition     = strcontains(azurerm_monitor_scheduled_query_rules_alert_v2.errors_logged[0].criteria[0].query, "\"ca-assay-dev-worker\"")
    error_message = "The error alert reads the worker's logs."
  }

  assert {
    condition     = azurerm_postgresql_flexible_server.this.geo_redundant_backup_enabled && azurerm_postgresql_flexible_server.this.auto_grow_enabled && azurerm_postgresql_flexible_server.this.backup_retention_days == 35
    error_message = "Production's database keeps 35 days of geo-redundant backups and grows."
  }
}

run "the_worker_can_be_switched_off" {
  command = plan

  variables {
    worker_enabled = false
    alert_emails   = ["ops@example.com"]
    api            = { cpu = 0.5, memory = "1Gi", min_replicas = 0, max_replicas = 1 }
  }

  assert {
    condition     = length(azurerm_container_app.worker) == 0
    error_message = "A switched-off worker has no app, so nothing runs or is billed."
  }

  assert {
    condition     = length(azurerm_monitor_metric_alert.worker_not_running) == 0
    error_message = "Its not-running alert is off meanwhile."
  }

  assert {
    condition     = azurerm_container_app.api.template[0].min_replicas == 0
    error_message = "The API may scale to zero."
  }
}

run "a_worker_expected_to_scale_is_refused" {
  command = plan

  variables {
    worker = { cpu = 1, memory = "2Gi", min_replicas = 1, max_replicas = 3 }
  }

  expect_failures = [var.worker]
}

run "a_worker_with_no_replica_is_refused" {
  command = plan

  variables {
    worker = { cpu = 1, memory = "2Gi", min_replicas = 0, max_replicas = 2 }
  }

  expect_failures = [var.worker]
}

run "the_fe_and_the_api_share_one_origin_by_default" {
  command = apply

  variables {
    image_tag = "2.4.1"
  }

  assert {
    condition     = one([for env in azurerm_container_app.api.template[0].container[0].env : env.value if env.name == "ASSAY_CORS_ALLOWED_ORIGINS"]) == ""
    error_message = "No other origin is allowed: set empty, not left to the API's local default."
  }
}

run "an_upload_limit_that_isnt_whole_megabytes_is_refused" {
  command = plan

  variables {
    dataset_upload_max_mb = 2.5
  }

  expect_failures = [var.dataset_upload_max_mb]
}

run "an_unknown_judge_provider_is_refused" {
  command = plan

  variables {
    judge = {
      provider = "mystery"
      model    = "x"
    }
  }

  expect_failures = [var.judge]
}
