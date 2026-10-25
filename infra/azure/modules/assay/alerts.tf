# Emails when something needs a look: the worker isn't running, the FE answers
# server errors, or Assay logs an error. None without var.alert_emails.

locals {
  alerts = length(var.alert_emails) > 0
  # The apps whose logs carry Assay's errors (JSON, one record per line)
  logging_apps = concat(
    [azurerm_container_app.api.name, azurerm_container_app.beat.name],
    azurerm_container_app.worker[*].name,
  )
}

resource "azurerm_monitor_action_group" "email" {
  count               = local.alerts ? 1 : 0
  name                = "ag-${local.prefix}"
  resource_group_name = var.resource_group_name
  short_name          = substr("assay${var.environment}", 0, 12)
  tags                = local.tags

  dynamic "email_receiver" {
    for_each = var.alert_emails
    content {
      name                    = "email-${email_receiver.key}"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }
}

# Runs wait as Pending while no worker runs. Off while the worker is switched off.
resource "azurerm_monitor_metric_alert" "worker_not_running" {
  count               = local.alerts && var.worker_enabled ? 1 : 0
  name                = "alert-${local.prefix}-worker-not-running"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_container_app.worker[0].id]
  description         = "No worker replica has run for 15 minutes: runs wait as Pending."
  severity            = 1
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = "Replicas"
    aggregation      = "Maximum"
    operator         = "LessThan"
    threshold        = 1
  }

  action {
    action_group_id = azurerm_monitor_action_group.email[0].id
  }
}

# Every request passes through the FE: its 5xx are the API's own and the
# API unreachable (nginx's 502/504) alike.
resource "azurerm_monitor_metric_alert" "server_errors" {
  count               = local.alerts ? 1 : 0
  name                = "alert-${local.prefix}-server-errors"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_container_app.fe.id]
  description         = "Assay answered server errors (5xx) in the last 15 minutes: the API's request IDs are in its logs."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = "Requests"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0

    dimension {
      name     = "statusCodeCategory"
      operator = "Include"
      values   = ["5xx"]
    }
  }

  action {
    action_group_id = azurerm_monitor_action_group.email[0].id
  }
}

# A run that couldn't be executed, an unexpected 500, anything Assay logs as an
# error. The table only exists once an app has logged: the empty datatable
# keeps the query valid before that.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "errors_logged" {
  count                 = local.alerts ? 1 : 0
  name                  = "alert-${local.prefix}-errors-logged"
  resource_group_name   = var.resource_group_name
  location              = var.location
  scopes                = [azurerm_log_analytics_workspace.this.id]
  description           = "The API, the worker or Beat logged an error in the last 15 minutes."
  severity              = 2
  evaluation_frequency  = "PT5M"
  window_duration       = "PT15M"
  skip_query_validation = true
  tags                  = local.tags

  criteria {
    query                   = <<-KQL
      union isfuzzy=true
        (datatable(ContainerAppName_s: string, Log_s: string)[]),
        (ContainerAppConsoleLogs_CL)
      | where ContainerAppName_s in (${join(", ", [for app in local.logging_apps : "\"${app}\""])})
      | extend entry = parse_json(Log_s)
      | where tostring(entry.level) in ("ERROR", "CRITICAL")
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action {
    action_groups = [azurerm_monitor_action_group.email[0].id]
  }
}
