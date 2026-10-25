terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.5"
    }
  }

  # Terraform's state in your storage account, each environment under its own
  # key: from your machine, terraform init -backend-config=backend.hcl (copy
  # backend.hcl.example); the pipeline passes the same from TF_STATE_*.
  backend "azurerm" {}
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

provider "azapi" {
  subscription_id = var.subscription_id
}
