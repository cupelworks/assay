# What the pipeline reads after its first apply, and what you look up.

output "fe_url" {
  description = "Assay's address: the FE, with the API's docs at /api/docs."
  value       = module.assay.fe_url
}

output "resource_group_name" {
  value = azurerm_resource_group.assay.name
}

output "registry_name" {
  value = module.assay.registry_name
}

output "registry_login_server" {
  value = module.assay.registry_login_server
}

output "image_tag" {
  value = module.assay.image_tag
}

output "fe_image_tag" {
  value = module.assay.fe_image_tag
}

output "fe_app_name" {
  value = module.assay.fe_app_name
}

output "api_app_name" {
  value = module.assay.api_app_name
}

output "worker_app_name" {
  value = module.assay.worker_app_name
}

output "beat_app_name" {
  value = module.assay.beat_app_name
}

output "migrate_job_name" {
  value = module.assay.migrate_job_name
}
