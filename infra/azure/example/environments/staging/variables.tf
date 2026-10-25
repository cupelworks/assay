# Set by the pipeline, from this environment's GitHub variables and secrets, or
# from your machine: terraform.tfvars (copy terraform.tfvars.example) and TF_VAR_*.

variable "subscription_id" {
  description = "The Azure subscription (AZURE_SUBSCRIPTION_ID)."
  type        = string
}

variable "image_tag" {
  description = "The BE version the API and worker run; the pipeline sets it. Empty runs a placeholder."
  type        = string
  default     = ""
}

variable "fe_image_tag" {
  description = "The FE version the FE runs; the pipeline sets it. Empty runs a placeholder."
  type        = string
  default     = ""
}

variable "entra_client_id" {
  description = "The sign-in's app registration (ENTRA_CLIENT_ID; scripts/entra-app.sh create)."
  type        = string
}

variable "entra_client_secret" {
  description = "Its client secret (the ENTRA_CLIENT_SECRET secret)."
  type        = string
  sensitive   = true
}

variable "judge_api_key" {
  description = "The LLM judge's API key (the JUDGE_API_KEY secret); null for none."
  type        = string
  default     = null
  sensitive   = true
}
