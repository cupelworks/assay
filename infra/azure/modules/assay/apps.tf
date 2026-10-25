# The container apps environment and Assay's processes: the FE (the only one
# reachable from the internet), the API, the worker, exactly one Beat, the Redis
# broker, and a job that runs the migrations.

locals {
  redis_app_name   = "ca-${local.prefix}-redis"
  workload_profile = "Consumption"

  # Secrets every Assay container reads from Key Vault
  key_vault_secrets = merge(
    {
      "database-url" = azurerm_key_vault_secret.database_url.versionless_id
      "broker-url"   = azurerm_key_vault_secret.broker_url.versionless_id
    },
    nonsensitive(var.judge_api_key != null) ? { "judge-api-key" = azurerm_key_vault_secret.judge_api_key[0].versionless_id } : {},
  )

  # Environment of the API, the worker, Beat and the migrations
  secret_environment = {
    ASSAY_DATABASE_URL          = "database-url"
    ASSAY_CELERY_BROKER_URL     = "broker-url"
    ASSAY_CELERY_RESULT_BACKEND = "broker-url"
  }
  plain_environment = merge(
    {
      # TLS to PostgreSQL for asyncpg (the API) and psycopg2 (the worker) alike
      PGSSLMODE        = "require"
      ASSAY_LOG_FORMAT = "json"
      # Always set: empty means none, where unset would mean the local default
      ASSAY_CORS_ALLOWED_ORIGINS = join(",", var.cors_allowed_origins)
    },
    var.judge == null ? {} : merge(
      { ASSAY_JUDGE_PROVIDER = var.judge.provider, ASSAY_JUDGE_MODEL = var.judge.model },
      var.judge.url == null ? {} : { ASSAY_JUDGE_URL = var.judge.url },
    ),
    var.extra_environment,
  )
  # The API's own: served under the FE's /api, and the upload limit the FE's
  # nginx is sized for
  api_environment = merge(local.plain_environment, {
    ASSAY_ROOT_PATH             = local.api_root_path
    ASSAY_DATASET_UPLOAD_MAX_MB = tostring(var.dataset_upload_max_mb)
  })
  # The judge's key, under the variable its provider reads by default
  judge_key_variable = var.judge == null ? "ANTHROPIC_API_KEY" : (var.judge.provider == "openai" ? "OPENAI_API_KEY" : "ANTHROPIC_API_KEY")
  worker_secret_environment = merge(
    local.secret_environment,
    nonsensitive(var.judge_api_key != null) ? { (local.judge_key_variable) = "judge-api-key" } : {},
  )
}

resource "azurerm_container_app_environment" "this" {
  name                               = "cae-${local.prefix}"
  location                           = var.location
  resource_group_name                = var.resource_group_name
  log_analytics_workspace_id         = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id           = azurerm_subnet.apps.id
  infrastructure_resource_group_name = "${var.resource_group_name}-infra"
  internal_load_balancer_enabled     = false
  tags                               = local.tags

  workload_profile {
    name                  = local.workload_profile
    workload_profile_type = "Consumption"
  }
}

# --- the broker --------------------------------------------------------------

# Redis inside the environment, reachable only by the other apps, with a password.
# It keeps nothing on disk: a run whose task is lost is re-sent by Beat's
# reconciliation scan.
resource "azurerm_container_app" "redis" {
  name                         = local.redis_app_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  workload_profile_name        = local.workload_profile
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  secret {
    name                = "redis-password"
    key_vault_secret_id = azurerm_key_vault_secret.redis_password.versionless_id
    identity            = azurerm_user_assigned_identity.apps.id
  }

  ingress {
    external_enabled = false
    transport        = "tcp"
    target_port      = 6379
    exposed_port     = 6379

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name    = "redis"
      image   = var.redis_image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["sh"]
      args    = ["-c", "exec redis-server --requirepass \"$REDIS_PASSWORD\" --save '' --appendonly no"]

      env {
        name        = "REDIS_PASSWORD"
        secret_name = "redis-password"
      }
    }
  }
}

# --- the API -------------------------------------------------------------------

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.prefix}-api"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  workload_profile_name        = local.workload_profile
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = local.key_vault_secrets
    content {
      name                = secret.key
      key_vault_secret_id = secret.value
      identity            = azurerm_user_assigned_identity.apps.id
    }
  }

  ingress {
    # Never reachable from the internet: only the other apps reach it, the FE's
    # nginx passing /api on to it
    external_enabled = false
    target_port      = local.api_port
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.api.min_replicas
    max_replicas = var.api.max_replicas

    container {
      name   = "api"
      image  = local.api_image
      cpu    = var.api.cpu
      memory = var.api.memory

      dynamic "env" {
        for_each = local.has_images ? local.api_environment : {}
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.has_images ? local.secret_environment : {}
        content {
          name        = env.key
          secret_name = env.value
        }
      }

      dynamic "liveness_probe" {
        for_each = local.has_images ? [1] : []
        content {
          transport = "HTTP"
          port      = local.api_port
          path      = "/health"
        }
      }

      dynamic "readiness_probe" {
        for_each = local.has_images ? [1] : []
        content {
          transport = "HTTP"
          port      = local.api_port
          path      = "/health"
        }
      }
    }
  }

  depends_on = [time_sleep.roles]
}


# --- the FE ------------------------------------------------------------------------

# The only app reachable from the internet: nginx serving the FE's files and
# passing /api/* on to the API over the private network, without the prefix.
resource "azurerm_container_app" "fe" {
  name                         = "ca-${local.prefix}-fe"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  workload_profile_name        = local.workload_profile
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  # The sign-in's client secret, under the name signin.tf gives the platform
  secret {
    name                = local.entra_secret_name
    key_vault_secret_id = azurerm_key_vault_secret.entra_client_secret.versionless_id
    identity            = azurerm_user_assigned_identity.apps.id
  }

  ingress {
    external_enabled = true
    target_port      = local.fe_port
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.fe.min_replicas
    max_replicas = var.fe.max_replicas

    container {
      name   = "fe"
      image  = local.fe_image
      cpu    = var.fe.cpu
      memory = var.fe.memory

      # The API's address inside the environment, over HTTPS
      env {
        name  = "ASSAY_API_URL"
        value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
      }

      # One megabyte more than the API's limit, for the form around the file:
      # a file over the limit gets the API's own 413
      env {
        name  = "ASSAY_UPLOAD_MAX_MB"
        value = tostring(var.dataset_upload_max_mb + 1)
      }

      dynamic "liveness_probe" {
        for_each = local.has_fe_image ? [1] : []
        content {
          transport = "HTTP"
          port      = local.fe_port
          path      = "/healthz"
        }
      }

      dynamic "readiness_probe" {
        for_each = local.has_fe_image ? [1] : []
        content {
          transport = "HTTP"
          port      = local.fe_port
          path      = "/healthz"
        }
      }
    }
  }

  depends_on = [time_sleep.roles]
}

# --- the worker and Beat ---------------------------------------------------------

# The worker became optional (count): an existing one is kept, not recreated
moved {
  from = azurerm_container_app.worker
  to   = azurerm_container_app.worker[0]
}

# Absent while switched off (worker_enabled = false): with no ingress and no
# scale rule, Container Apps keeps a replica running even at minimum zero, so
# the off switch removes the app. It keeps nothing of its own: runs are in the
# database, and the reconciliation scan sends the waiting ones again once it's
# back.
resource "azurerm_container_app" "worker" {
  count                        = var.worker_enabled ? 1 : 0
  name                         = "ca-${local.prefix}-worker"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  workload_profile_name        = local.workload_profile
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = local.key_vault_secrets
    content {
      name                = secret.key
      key_vault_secret_id = secret.value
      identity            = azurerm_user_assigned_identity.apps.id
    }
  }

  template {
    min_replicas = var.worker.min_replicas
    max_replicas = var.worker.max_replicas

    container {
      name   = "worker"
      image  = local.worker_image
      cpu    = var.worker.cpu
      memory = var.worker.memory

      dynamic "env" {
        for_each = local.has_images ? local.plain_environment : {}
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.has_images ? local.worker_secret_environment : {}
        content {
          name        = env.key
          secret_name = env.value
        }
      }
    }
  }

  depends_on = [time_sleep.roles]
}

# Exactly one Beat: it schedules the scan that re-sends lost runs.
resource "azurerm_container_app" "beat" {
  name                         = "ca-${local.prefix}-beat"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  workload_profile_name        = local.workload_profile
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = local.key_vault_secrets
    content {
      name                = secret.key
      key_vault_secret_id = secret.value
      identity            = azurerm_user_assigned_identity.apps.id
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name    = "beat"
      image   = local.worker_image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = local.has_images ? ["celery", "-A", "assay.worker", "beat", "--loglevel=info"] : null

      dynamic "env" {
        for_each = local.has_images ? local.plain_environment : {}
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.has_images ? local.secret_environment : {}
        content {
          name        = env.key
          secret_name = env.value
        }
      }
    }
  }

  depends_on = [time_sleep.roles]
}

# --- the migrations ------------------------------------------------------------

# Run before each release (the pipeline starts it with the new image, then rolls
# out the apps): brings the database's schema up to date.
resource "azurerm_container_app_job" "migrate" {
  name                         = "caj-${local.prefix}-migrate"
  location                     = var.location
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  workload_profile_name        = local.workload_profile
  replica_timeout_in_seconds   = 900
  replica_retry_limit          = 0
  tags                         = local.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = local.key_vault_secrets
    content {
      name                = secret.key
      key_vault_secret_id = secret.value
      identity            = azurerm_user_assigned_identity.apps.id
    }
  }

  template {
    container {
      name    = "migrate"
      image   = local.api_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["alembic", "upgrade", "head"]

      dynamic "env" {
        for_each = local.plain_environment
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.secret_environment
        content {
          name        = env.key
          secret_name = env.value
        }
      }
    }
  }

  depends_on = [time_sleep.roles]
}
