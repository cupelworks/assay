# PostgreSQL Flexible Server on the private subnet: no public endpoint, TLS
# required (the containers set PGSSLMODE=require, which both drivers honour).

resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                          = "psql-${local.prefix}-${random_string.suffix.result}"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  version                       = var.postgres.version
  sku_name                      = var.postgres.sku_name
  storage_mb                    = var.postgres.storage_mb
  backup_retention_days         = var.postgres.backup_retention_days
  geo_redundant_backup_enabled  = var.postgres.geo_redundant_backup
  auto_grow_enabled             = var.postgres.auto_grow
  administrator_login           = "assayadmin"
  administrator_password        = random_password.postgres.result
  delegated_subnet_id           = azurerm_subnet.database.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false
  tags                          = local.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]

  lifecycle {
    # Azure picks the zone when it isn't set; don't fight it on later applies
    ignore_changes = [zone, high_availability[0].standby_availability_zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "assay" {
  name      = "assay"
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}
