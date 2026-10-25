# Secrets: the database URL (with its password), the broker URL (with Redis's
# password), the judge's API key and the sign-in's client secret. The containers
# read them through their identity; they never appear in the pipeline or in app
# settings.

resource "azurerm_key_vault" "this" {
  name                       = "kv${local.compact}"
  location                   = var.location
  resource_group_name        = var.resource_group_name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  tags                       = local.tags
}

# Whoever runs Terraform writes the secrets...
resource "azurerm_role_assignment" "deployer_secrets" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ...and the containers read them.
resource "azurerm_role_assignment" "apps_secrets" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
}

# Role assignments take a little while to apply.
resource "time_sleep" "roles" {
  create_duration = "60s"
  depends_on      = [azurerm_role_assignment.deployer_secrets, azurerm_role_assignment.apps_secrets, azurerm_role_assignment.apps_pull]
}

resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "azurerm_key_vault_secret" "database_url" {
  name         = "database-url"
  key_vault_id = azurerm_key_vault.this.id
  value        = "postgresql+asyncpg://${azurerm_postgresql_flexible_server.this.administrator_login}:${random_password.postgres.result}@${azurerm_postgresql_flexible_server.this.fqdn}:5432/${azurerm_postgresql_flexible_server_database.assay.name}"
  depends_on   = [time_sleep.roles]
}

resource "azurerm_key_vault_secret" "redis_password" {
  name         = "redis-password"
  key_vault_id = azurerm_key_vault.this.id
  value        = random_password.redis.result
  depends_on   = [time_sleep.roles]
}

resource "azurerm_key_vault_secret" "broker_url" {
  name         = "broker-url"
  key_vault_id = azurerm_key_vault.this.id
  value        = "redis://:${random_password.redis.result}@${local.redis_app_name}:6379/0"
  depends_on   = [time_sleep.roles]
}

resource "azurerm_key_vault_secret" "judge_api_key" {
  count        = nonsensitive(var.judge_api_key != null) ? 1 : 0
  name         = "judge-api-key"
  key_vault_id = azurerm_key_vault.this.id
  value        = var.judge_api_key
  depends_on   = [time_sleep.roles]
}

resource "azurerm_key_vault_secret" "entra_client_secret" {
  name         = "entra-client-secret"
  key_vault_id = azurerm_key_vault.this.id
  value        = var.entra_client_secret
  depends_on   = [time_sleep.roles]
}
