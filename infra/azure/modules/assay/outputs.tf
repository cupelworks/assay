output "registry_name" {
  description = "The container registry's name (`az acr build --registry`)."
  value       = azurerm_container_registry.this.name
}

output "registry_login_server" {
  description = "The container registry's address, the prefix of every image."
  value       = azurerm_container_registry.this.login_server
}

output "image_tag" {
  description = "The image tag the apps run; empty while they run the placeholder."
  value       = var.image_tag
}

output "fe_image_tag" {
  description = "The FE image tag the FE app runs; empty while it runs the placeholder."
  value       = var.fe_image_tag
}

output "fe_app_name" {
  value = azurerm_container_app.fe.name
}

output "api_app_name" {
  value = azurerm_container_app.api.name
}

output "worker_app_name" {
  description = "Empty while the worker is switched off (worker_enabled = false)."
  value       = try(azurerm_container_app.worker[0].name, "")
}

output "beat_app_name" {
  value = azurerm_container_app.beat.name
}

output "migrate_job_name" {
  description = "The job that runs `alembic upgrade head` (`az containerapp job start`)."
  value       = azurerm_container_app_job.migrate.name
}

output "fe_url" {
  description = "Assay's public address: the FE, with the API under /api and its docs at /api/docs."
  value       = "https://${azurerm_container_app.fe.ingress[0].fqdn}"
}

output "api_url" {
  description = "The API's address inside the environment, never reachable from the internet."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "key_vault_name" {
  value = azurerm_key_vault.this.name
}

output "postgres_server_name" {
  value = azurerm_postgresql_flexible_server.this.name
}

output "container_app_environment_name" {
  value = azurerm_container_app_environment.this.name
}
