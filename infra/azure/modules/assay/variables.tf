# --- naming and placement ---------------------------------------------------

variable "name" {
  description = "Short name used in every resource's name, lowercase letters and digits (e.g. \"assay\")."
  type        = string
  default     = "assay"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,11}$", var.name))
    error_message = "name must be 2–12 lowercase letters or digits, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name used in resource names (e.g. \"dev\", \"prod\")."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.environment))
    error_message = "environment must be 2–8 lowercase letters or digits, starting with a letter."
  }
}

variable "location" {
  description = "Azure region for every resource (e.g. \"northeurope\")."
  type        = string
}

variable "resource_group_name" {
  description = "Existing resource group to create everything in."
  type        = string
}

variable "tags" {
  description = "Tags added to every resource."
  type        = map(string)
  default     = {}
}

# --- network ---------------------------------------------------------------

variable "address_space" {
  description = "The virtual network's address space. Needs room for a /23 (apps) and a /28 (database)."
  type        = string
  default     = "10.20.0.0/16"
}

# --- sign-in ---------------------------------------------------------------

variable "entra" {
  description = <<-EOT
    The Entra ID app registration people sign in to before they reach the FE (the script
    infra/azure/scripts/entra-app.sh creates one): its client ID, and its directory
    (tenant) when it isn't the one Terraform runs in. Required: the API has no sign-in
    of its own, so nothing is served without this one.
  EOT
  type = object({
    client_id = string
    tenant_id = optional(string)
  })

  validation {
    condition = alltrue([
      for id in compact([var.entra.client_id, var.entra.tenant_id]) :
      can(regex("^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$", id))
    ])
    error_message = "entra.client_id (and tenant_id, when set) must be GUIDs."
  }
}

variable "entra_client_secret" {
  description = "The app registration's client secret, stored in Key Vault. Pass it as TF_VAR_entra_client_secret, never in a file."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.entra_client_secret) > 0
    error_message = "entra_client_secret must not be empty."
  }
}

# --- images ----------------------------------------------------------------

variable "image_tag" {
  description = <<-EOT
    Tag of the `assay-api` and `assay-worker` images in this deployment's registry. Empty
    (the first apply, before any image is built) runs a placeholder image instead.
  EOT
  type        = string
  default     = ""
}

variable "fe_image_tag" {
  description = <<-EOT
    Tag of the `assay-fe` image (the FE's own version) in this deployment's registry.
    Empty (before the FE's image is built) runs a placeholder image instead.
  EOT
  type        = string
  default     = ""
}

variable "redis_image" {
  description = "Image of the Redis broker running inside the environment (Redis 7.2, BSD-licensed)."
  type        = string
  default     = "docker.io/library/redis:7.2-alpine"
}

# --- sizes -----------------------------------------------------------------

variable "api" {
  description = "API container size and replicas. min_replicas 0 lets it scale to zero when idle (a few seconds to wake on the next request)."
  type = object({
    cpu          = number
    memory       = string
    min_replicas = number
    max_replicas = number
  })
  default = { cpu = 0.5, memory = "1Gi", min_replicas = 1, max_replicas = 2 }

  validation {
    condition     = var.api.min_replicas >= 0 && var.api.max_replicas >= 1 && var.api.min_replicas <= var.api.max_replicas
    error_message = "api: min_replicas must be at least 0 and at most max_replicas, which must be at least 1."
  }
}

variable "fe" {
  description = "FE container size and replicas: nginx serving the FE and passing /api on to the API."
  type = object({
    cpu          = number
    memory       = string
    min_replicas = number
    max_replicas = number
  })
  default = { cpu = 0.25, memory = "0.5Gi", min_replicas = 1, max_replicas = 2 }

  validation {
    condition     = var.fe.min_replicas >= 0 && var.fe.max_replicas >= 1 && var.fe.min_replicas <= var.fe.max_replicas
    error_message = "fe: min_replicas must be at least 0 and at most max_replicas, which must be at least 1."
  }
}

variable "worker" {
  description = <<-EOT
    Worker container size and how many run. The worker takes no traffic, so Azure never
    scales it: it always runs min_replicas replicas, and max_replicas must be the same
    number (set both to N for N workers). With the NLP models loaded each uses about
    800 MB, so 1 vCPU / 2 GiB holds it. To stop it, use worker_enabled.
  EOT
  type = object({
    cpu          = number
    memory       = string
    min_replicas = number
    max_replicas = number
  })
  default = { cpu = 1, memory = "2Gi", min_replicas = 1, max_replicas = 1 }

  validation {
    condition     = var.worker.min_replicas >= 1
    error_message = "worker: min_replicas must be at least 1. To stop the worker, use worker_enabled."
  }

  validation {
    condition     = var.worker.min_replicas == var.worker.max_replicas
    error_message = "worker: min_replicas and max_replicas must be equal: nothing scales the worker, so it always runs min_replicas. Set both to the number of workers you want."
  }
}

variable "worker_enabled" {
  description = <<-EOT
    false removes the worker app (nothing billed for it), e.g. in staging between tests;
    true creates it again, in a minute or two. Runs wait as Pending meanwhile, and the
    reconciliation scan sends them again once it's back. Its "not running" alert is off
    while it's off.
  EOT
  type        = bool
  default     = true
}

variable "postgres" {
  description = <<-EOT
    PostgreSQL Flexible Server sizing and backups. geo_redundant_backup keeps the backups
    in the paired region too, and can only be chosen when the server is created: changing
    it later replaces the server, and its data with it. auto_grow adds storage before the
    disk fills (a full disk makes the database read-only); storage never shrinks.
  EOT
  type = object({
    sku_name              = string
    version               = string
    storage_mb            = number
    backup_retention_days = number
    geo_redundant_backup  = optional(bool, false)
    auto_grow             = optional(bool, false)
  })
  default = {
    sku_name              = "B_Standard_B1ms"
    version               = "16"
    storage_mb            = 32768
    backup_retention_days = 7
  }

  validation {
    condition     = var.postgres.backup_retention_days >= 7 && var.postgres.backup_retention_days <= 35
    error_message = "postgres.backup_retention_days must be between 7 and 35."
  }
}

variable "alert_emails" {
  description = <<-EOT
    Addresses emailed when the worker isn't running, the FE answers server errors (the
    API's, or the API unreachable), or the API, the worker or Beat log an error. Empty
    (the default) creates no alerts.
  EOT
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "Days the Log Analytics workspace keeps logs."
  type        = number
  default     = 30
}

# --- Assay settings ----------------------------------------------------------

variable "cors_allowed_origins" {
  description = <<-EOT
    Other origins allowed to call the API from a browser. Empty (the default) allows none:
    the FE is served from its own address and reaches the API under /api through it.
  EOT
  type        = list(string)
  default     = []
}

variable "dataset_upload_max_mb" {
  description = <<-EOT
    The largest dataset file an upload accepts, in MB: the API's limit
    (ASSAY_DATASET_UPLOAD_MAX_MB). The FE's nginx accepts 1 MB more, for the form around
    the file, so the API is the one that answers.
  EOT
  type        = number
  default     = 10

  validation {
    condition     = var.dataset_upload_max_mb >= 1 && floor(var.dataset_upload_max_mb) == var.dataset_upload_max_mb
    error_message = "dataset_upload_max_mb must be a whole number of megabytes, at least 1."
  }
}

variable "judge" {
  description = <<-EOT
    The LLM judge's settings (ASSAY_JUDGE_*). null leaves the judge unset; it can still
    be chosen later through `PATCH /settings/judge`.
  EOT
  type = object({
    provider = string
    model    = string
    url      = optional(string)
  })
  default = null

  validation {
    condition     = var.judge == null || contains(["anthropic", "openai"], try(var.judge.provider, ""))
    error_message = "judge.provider must be \"anthropic\" or \"openai\"."
  }
}

variable "judge_api_key" {
  description = <<-EOT
    The judge's API key, stored in Key Vault and given to the worker as ANTHROPIC_API_KEY
    or OPENAI_API_KEY (by the judge's provider). null stores none.
  EOT
  type        = string
  default     = null
  sensitive   = true
}

variable "extra_environment" {
  description = "More ASSAY_* environment variables for the API and the worker (not secrets), e.g. ASSAY_TARGET_URL."
  type        = map(string)
  default     = {}
}
