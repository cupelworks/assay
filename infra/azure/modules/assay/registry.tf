# Where the deployment's own images live. The pipeline builds them here
# (`az acr build`); the apps pull them with their identity, no password.

resource "azurerm_container_registry" "this" {
  name                = "cr${local.compact}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "apps_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
}
