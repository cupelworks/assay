# Sign-in in front of the FE: Container Apps' built-in authentication with
# Entra ID. Nothing the FE serves, nor the API behind its /api, is reached
# without signing in to the app registration in var.entra; the API itself has
# no address outside the environment.

locals {
  entra_tenant_id   = coalesce(var.entra.tenant_id, data.azurerm_client_config.current.tenant_id)
  entra_secret_name = "microsoft-provider-authentication-secret"
}

resource "azapi_resource" "fe_sign_in" {
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  name      = "current"
  parent_id = azurerm_container_app.fe.id

  body = {
    properties = {
      platform = { enabled = true }
      globalValidation = {
        # A page is sent to Microsoft's sign-in; a request from a page that
        # sends X-Requested-With: XMLHttpRequest (the FE's calls) gets a 403
        # instead, so the FE can reload and sign in again
        unauthenticatedClientAction = "RedirectToLoginPage"
        redirectToProvider          = "azureactivedirectory"
      }
      httpSettings = { requireHttps = true }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId                = var.entra.client_id
            clientSecretSettingName = local.entra_secret_name
            # Only the registration's own directory signs in
            openIdIssuer = "https://login.microsoftonline.com/${local.entra_tenant_id}/v2.0"
          }
        }
      }
    }
  }
}
