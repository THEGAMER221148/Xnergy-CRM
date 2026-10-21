# Manual migration job: updating this resource does not execute the migration.
resource "azurerm_container_app_job" "migration" {
  name                         = "${local.name}-migrate"
  location                     = data.azurerm_resource_group.rg.location
  resource_group_name          = data.azurerm_resource_group.rg.name
  container_app_environment_id = azurerm_container_app_environment.environment.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 600
  replica_retry_limit          = 0

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name  = "postgres-admin-password"
    value = var.postgres_admin_password
  }

  secret {
    name  = "postgres-app-password"
    value = var.postgres_app_password
  }

  template {
    container {
      name    = "migrate"
      image   = "dealosstagingzk998acr.azurecr.io/dealos@sha256:2438a6f4d5da5e1730a5d8d597e052cfb53f8d3425fc6ee99d22423dee3b328e"
      command = ["python", "/app/scripts/provision_team.py"]
      cpu     = 0.5
      memory  = "1Gi"

      env {
        name  = "PGHOST"
        value = azurerm_postgresql_flexible_server.postgres.fqdn
      }
      env {
        name  = "PGUSER"
        value = var.postgres_admin
      }
      env {
        name        = "PGPASSWORD"
        secret_name = "postgres-admin-password"
      }
      env {
        name        = "DEALOS_APP_PASSWORD"
        secret_name = "postgres-app-password"
      }
      env {
        name  = "ADMIN_ISSUER"
        value = var.oidc_issuer
      }
      env {
        name  = "ADMIN_OBJECT_ID"
        value = "49cf165c-40b3-4f5d-a68d-2829ac76904e"
      }
      env {
        name  = "ADMIN_EMAIL"
        value = "nia@xnergy.com"
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_postgresql_flexible_server_configuration.extensions,
    azurerm_postgresql_flexible_server_database.dealos,
  ]
  tags = local.tags
}
