#!/usr/bin/env bash
# The Entra ID app registration people sign in to before they reach Assay's FE:
# one per environment, created once by someone who may create app
# registrations in the directory (an Application Administrator, or a Global
# Administrator), with the Azure CLI signed in to it (az login). The pipeline
# never needs rights in the directory.
#
#   entra-app.sh create <display name>          e.g. "Assay (staging)"
#       The registration: only the people assigned to it sign in, you first.
#       Prints its client ID, the directory and a client secret valid one year.
#   entra-app.sh redirect <client id> <fe url>  after the first terraform apply
#       Lets the FE's address (terraform output fe_url) receive the sign-in.
#   entra-app.sh allow <client id> <user>       a user's sign-in name or object ID
#       Lets one more person sign in.
#   entra-app.sh rotate <client id>             before the secret expires
#       Adds a new secret, valid one year, beside the old one; prints it and the
#       old ones' key IDs to delete once the new one is applied.

set -euo pipefail

# The default access of an app without roles of its own
DEFAULT_ACCESS="00000000-0000-0000-0000-000000000000"
SECRET_NAME="container-apps-sign-in"

usage() {
  sed -n '8,17p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

service_principal() {
  az ad sp show --id "$1" --query id --output tsv
}

assign() {
  local sp_id="$1" user_id="$2"
  az rest --method post \
    --url "https://graph.microsoft.com/v1.0/servicePrincipals/${sp_id}/appRoleAssignedTo" \
    --body "{\"principalId\": \"${user_id}\", \"resourceId\": \"${sp_id}\", \"appRoleId\": \"${DEFAULT_ACCESS}\"}" \
    --output none
}

new_secret() {
  # --append keeps the secrets already there
  az ad app credential reset --id "$1" --append --display-name "$SECRET_NAME" --years 1 \
    --query password --output tsv
}

case "${1:-}" in
  create)
    [ $# -eq 2 ] || usage
    app_id=$(az ad app create --display-name "$2" --sign-in-audience AzureADMyOrg \
      --enable-id-token-issuance true --query appId --output tsv)
    sp_id=$(az ad sp create --id "$app_id" --query id --output tsv)
    # Only the people assigned to it sign in, not everyone in the directory
    az ad sp update --id "$sp_id" --set appRoleAssignmentRequired=true
    assign "$sp_id" "$(az ad signed-in-user show --query id --output tsv)"
    secret=$(new_secret "$app_id")
    cat <<EOF
Created "$2". You're assigned to it; entra-app.sh allow adds others.

  client ID:  $app_id
  directory:  $(az account show --query tenantId --output tsv)

The client secret, shown only now. Keep it in your password manager and pass it
as TF_VAR_entra_client_secret (or the pipeline's ENTRA_CLIENT_SECRET secret); it
expires in one year (entra-app.sh rotate):

  $secret

After the first terraform apply:
  entra-app.sh redirect $app_id "\$(terraform output -raw fe_url)"
EOF
    ;;

  redirect)
    [ $# -eq 3 ] || usage
    url="${3%/}"
    az ad app update --id "$2" --web-redirect-uris "${url}/.auth/login/aad/callback"
    echo "Sign-in now returns to ${url}."
    ;;

  allow)
    [ $# -eq 3 ] || usage
    assign "$(service_principal "$2")" "$(az ad user show --id "$3" --query id --output tsv)"
    echo "$3 can sign in."
    ;;

  rotate)
    [ $# -eq 2 ] || usage
    old=$(az ad app credential list --id "$2" --query "[].keyId" --output tsv)
    secret=$(new_secret "$2")
    cat <<EOF
A new client secret, shown only now; it expires in one year:

  $secret

Set it as TF_VAR_entra_client_secret (or the pipeline's secret) and apply. Then
delete the old ones:
EOF
    for key_id in $old; do
      echo "  az ad app credential delete --id $2 --key-id $key_id"
    done
    ;;

  *)
    usage
    ;;
esac
