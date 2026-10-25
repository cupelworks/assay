# Assay on Azure: the FE, the API, the worker, Beat and a migration job on Container Apps,
# PostgreSQL Flexible Server, a Redis broker inside the environment, images in a
# container registry, secrets in Key Vault, logs in Log Analytics; all on a private
# virtual network.

data "azurerm_client_config" "current" {}

# Globally unique names (registry, Key Vault) need a short random suffix.
resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  prefix = "${var.name}-${var.environment}"
  # alphanumeric, for the names that allow nothing else
  compact = "${var.name}${var.environment}${random_string.suffix.result}"

  tags = merge({ application = "assay", environment = var.environment }, var.tags)

  placeholder_image = "mcr.microsoft.com/k8se/quickstart:latest"
  has_images        = var.image_tag != ""
  api_image         = local.has_images ? "${azurerm_container_registry.this.login_server}/assay-api:${var.image_tag}" : local.placeholder_image
  worker_image      = local.has_images ? "${azurerm_container_registry.this.login_server}/assay-worker:${var.image_tag}" : local.placeholder_image
  # the placeholder serves on port 80, Assay's API on 8000
  api_port = local.has_images ? 8000 : 80

  has_fe_image = var.fe_image_tag != ""
  fe_image     = local.has_fe_image ? "${azurerm_container_registry.this.login_server}/assay-fe:${var.fe_image_tag}" : local.placeholder_image
  # the FE's nginx serves on 8080 (unprivileged)
  fe_port = local.has_fe_image ? 8080 : 80

  # The path the FE's nginx serves the API under, removing it before passing a
  # request on; the API is told, so its docs work there (ASSAY_ROOT_PATH)
  api_root_path = "/api"
}

# The one identity every container uses: pulls images, reads Key Vault secrets.
resource "azurerm_user_assigned_identity" "apps" {
  name                = "id-${local.prefix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = local.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${local.prefix}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}
