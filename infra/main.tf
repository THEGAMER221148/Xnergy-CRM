data "azurerm_resource_group" "rg" {
  name = "${var.prefix}-${var.environment}-rg"
}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
  numeric = true

  keepers = {
    prefix      = var.prefix
    environment = var.environment
  }
}

locals {
  name = "${var.prefix}-${var.environment}"

  unique_suffix = random_string.suffix.result

  quarantine_container = "dealos-quarantine"
  clean_container      = "dealos-clean"

  tags = {
    Application = "Xnergy-DealOS"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# -------------------------------------------------------------------
# NETWORK
# -------------------------------------------------------------------

resource "azurerm_virtual_network" "vnet" {
  name                = "${local.name}-vnet"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  address_space       = ["10.40.0.0/16"]

  tags = local.tags
}

resource "azurerm_subnet" "apps" {
  name                 = "container-apps"
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.40.0.0/21"]

  delegation {
    name = "container-apps"

    service_delegation {
      name = "Microsoft.App/environments"

      actions = [
        "Microsoft.Network/virtualNetworks/subnets/join/action",
      ]
    }
  }
}

resource "azurerm_subnet" "postgres" {
  name                 = "postgres"
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.40.8.0/24"]
  service_endpoints    = ["Microsoft.Storage"]

  delegation {
    name = "postgres"

    service_delegation {
      name = "Microsoft.DBforPostgreSQL/flexibleServers"

      actions = [
        "Microsoft.Network/virtualNetworks/subnets/join/action",
      ]
    }
  }
}

# -------------------------------------------------------------------
# APPLICATION MANAGED IDENTITY
# -------------------------------------------------------------------

resource "azurerm_user_assigned_identity" "app" {
  name                = "${local.name}-identity"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name

  tags = local.tags
}

# -------------------------------------------------------------------
# AZURE CONTAINER REGISTRY
# -------------------------------------------------------------------

resource "azurerm_container_registry" "acr" {
  name = substr(
    "${replace(var.prefix, "-", "")}${replace(var.environment, "-", "")}${local.unique_suffix}acr",
    0,
    50
  )

  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location

  sku                    = "Standard"
  admin_enabled          = false
  anonymous_pull_enabled = false

  tags = local.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# -------------------------------------------------------------------
# POSTGRESQL PRIVATE DNS
# -------------------------------------------------------------------

resource "azurerm_private_dns_zone" "postgres" {
  name                = "${local.name}.postgres.database.azure.com"
  resource_group_name = data.azurerm_resource_group.rg.name

  tags = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "${local.name}-postgres-link"
  resource_group_name   = data.azurerm_resource_group.rg.name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.vnet.id

  registration_enabled = false

  tags = local.tags
}

# -------------------------------------------------------------------
# POSTGRESQL FLEXIBLE SERVER
# -------------------------------------------------------------------

resource "azurerm_postgresql_flexible_server" "postgres" {
  name = substr(
    "${local.name}-${local.unique_suffix}-pg",
    0,
    63
  )

  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location

  version = "17"

  delegated_subnet_id           = azurerm_subnet.postgres.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false

  administrator_login    = var.postgres_admin
  administrator_password = var.postgres_admin_password

  authentication {
    active_directory_auth_enabled = false
    password_auth_enabled         = true
  }

  sku_name   = "GP_Standard_D4s_v3"
  storage_mb = 131072

  backup_retention_days        = 35
  geo_redundant_backup_enabled = false

  zone = "1"

  high_availability {
    mode                      = "SameZone"
    standby_availability_zone = "1"
  }

  depends_on = [
    azurerm_private_dns_zone_virtual_network_link.postgres,
  ]

  tags = local.tags
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "CITEXT,PGCRYPTO"
}

resource "azurerm_postgresql_flexible_server_database" "dealos" {
  name      = "dealos"
  server_id = azurerm_postgresql_flexible_server.postgres.id

  collation = "en_US.utf8"
  charset   = "UTF8"
}

# -------------------------------------------------------------------
# AZURE MANAGED REDIS
# -------------------------------------------------------------------

resource "azurerm_managed_redis" "redis" {
  name = substr(
    "${local.name}-${local.unique_suffix}-redis",
    0,
    63
  )

  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location

  sku_name = "Balanced_B0"

  high_availability_enabled = true
  public_network_access     = "Enabled"

  default_database {
    access_keys_authentication_enabled = false
    client_protocol                    = "Encrypted"
    clustering_policy                  = "EnterpriseCluster"
    eviction_policy                    = "NoEviction"
  }

  tags = local.tags
}

resource "azurerm_managed_redis_access_policy_assignment" "app" {
  managed_redis_id = azurerm_managed_redis.redis.id
  object_id        = azurerm_user_assigned_identity.app.principal_id
}

# -------------------------------------------------------------------
# DOCUMENT STORAGE
#
# The application creates the quarantine and clean containers on
# startup using its managed identity. Terraform therefore does not
# need Blob data-plane permissions on this account.
# -------------------------------------------------------------------

resource "azurerm_storage_account" "docs" {
  name = substr(
    "${replace(var.prefix, "-", "")}${replace(var.environment, "-", "")}${local.unique_suffix}docs",
    0,
    24
  )

  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location

  account_tier             = "Standard"
  account_replication_type = "ZRS"

  min_tls_version = "TLS1_2"

  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  public_network_access_enabled   = true

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }

  tags = local.tags
}

resource "azurerm_role_assignment" "app_blob_data" {
  scope                = azurerm_storage_account.docs.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# -------------------------------------------------------------------
# SERVICE BUS
# -------------------------------------------------------------------

resource "azurerm_servicebus_namespace" "servicebus" {
  name = substr(
    "${local.name}-${local.unique_suffix}-bus",
    0,
    50
  )

  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name

  sku                          = "Premium"
  capacity                     = 1
  premium_messaging_partitions = 1

  tags = local.tags
}

resource "azurerm_servicebus_queue" "malware_results" {
  name         = "dealos-malware-results"
  namespace_id = azurerm_servicebus_namespace.servicebus.id

  lock_duration = "PT5M"

  max_delivery_count = 5

  dead_lettering_on_message_expiration = true
}

resource "azurerm_role_assignment" "servicebus_receiver" {
  scope                = azurerm_servicebus_namespace.servicebus.id
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# -------------------------------------------------------------------
# LOG ANALYTICS
# -------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "logs" {
  name                = "${local.name}-logs"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name

  sku               = "PerGB2018"
  retention_in_days = 180

  tags = local.tags
}

# -------------------------------------------------------------------
# CONTAINER APPS ENVIRONMENT
# -------------------------------------------------------------------

resource "azurerm_container_app_environment" "environment" {
  name                = "${local.name}-cae"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name

  log_analytics_workspace_id         = azurerm_log_analytics_workspace.logs.id
  infrastructure_subnet_id           = azurerm_subnet.apps.id
  infrastructure_resource_group_name = "ME_${local.name}-cae_${data.azurerm_resource_group.rg.name}_${data.azurerm_resource_group.rg.location}"

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
    minimum_count         = 0
    maximum_count         = 0
  }

  tags = local.tags
}

# -------------------------------------------------------------------
# DEALOS API
#
# Deployment workflow:
#
# 1. Terraform bootstraps infrastructure and ACR.
# 2. GitHub builds and pushes the DealOS image.
# 3. A one-off migration process initializes PostgreSQL and dealos_app.
# 4. Terraform performs the full apply and launches this Container App.
# -------------------------------------------------------------------

resource "azurerm_container_app" "api" {
  name                         = "${local.name}-api"
  container_app_environment_id = azurerm_container_app_environment.environment.id
  resource_group_name          = data.azurerm_resource_group.rg.name

  revision_mode         = "Single"
  workload_profile_name = "Consumption"

  identity {
    type = "UserAssigned"

    identity_ids = [
      azurerm_user_assigned_identity.app.id,
    ]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name = "database-url"

    value = "postgresql://dealos_app:${var.postgres_app_password}@${azurerm_postgresql_flexible_server.postgres.fqdn}:5432/dealos?sslmode=require"
  }

  secret {
    name  = "oidc-client-secret"
    value = var.oidc_client_secret
  }

  template {
    min_replicas = 2
    max_replicas = 10

    container {
      name   = "api"
      image  = var.container_image
      cpu    = 1.0
      memory = "2Gi"

      startup_probe {
        transport               = "TCP"
        port                    = 8000
        interval_seconds        = 10
        timeout                 = 3
        failure_count_threshold = 30
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/readyz"
        interval_seconds        = 30
        timeout                 = 15
        success_count_threshold = 1
        failure_count_threshold = 3
      }

      env {
        name  = "APP_ENV"
        value = "production"
      }

      env {
        name  = "APP_BASE_URL"
        value = var.app_base_url
      }

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      env {
        name  = "AZURE_REDIS_HOST"
        value = azurerm_managed_redis.redis.hostname
      }

      # Required so DefaultAzureCredential selects this
      # user-assigned managed identity.
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.app.client_id
      }

      env {
        name  = "OIDC_ISSUER"
        value = var.oidc_issuer
      }

      env {
        name  = "OIDC_CLIENT_ID"
        value = var.oidc_client_id
      }

      env {
        name        = "OIDC_CLIENT_SECRET"
        secret_name = "oidc-client-secret"
      }

      env {
        name  = "OIDC_SCOPES"
        value = "openid profile email"
      }

      env {
        name  = "OIDC_ALLOWED_INTERNAL_DOMAIN"
        value = var.oidc_allowed_internal_domain
      }

      env {
        name  = "DEV_AUTH_BYPASS"
        value = "false"
      }

      env {
        name  = "SESSION_TTL_SECONDS"
        value = tostring(var.session_ttl_seconds)
      }

      env {
        name  = "COOKIE_SECURE"
        value = "true"
      }

      env {
        name  = "AZURE_STORAGE_ACCOUNT_URL"
        value = azurerm_storage_account.docs.primary_blob_endpoint
      }

      env {
        name  = "AZURE_QUARANTINE_CONTAINER"
        value = local.quarantine_container
      }

      env {
        name  = "AZURE_CLEAN_CONTAINER"
        value = local.clean_container
      }

      env {
        name  = "MAX_UPLOAD_BYTES"
        value = tostring(var.max_upload_bytes)
      }

      env {
        name  = "MALWARE_SCAN_REQUIRED"
        value = "true"
      }

      env {
        name  = "AZURE_SERVICEBUS_FQDN"
        value = "${azurerm_servicebus_namespace.servicebus.name}.servicebus.windows.net"
      }

      env {
        name  = "MALWARE_SCAN_QUEUE"
        value = azurerm_servicebus_queue.malware_results.name
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.app_blob_data,
    azurerm_role_assignment.servicebus_receiver,
    azurerm_managed_redis_access_policy_assignment.app,
  ]

  tags = local.tags
}

# -------------------------------------------------------------------
# OUTPUTS
# -------------------------------------------------------------------

output "resource_group_name" {
  value = data.azurerm_resource_group.rg.name
}

output "container_registry_name" {
  value = azurerm_container_registry.acr.name
}

output "container_registry_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "container_app_environment_name" {
  value = azurerm_container_app_environment.environment.name
}

output "container_app_environment_default_domain" {
  value = azurerm_container_app_environment.environment.default_domain
}

output "container_app_name" {
  value = azurerm_container_app.api.name
}

output "container_app_fqdn" {
  value = azurerm_container_app.api.latest_revision_fqdn
}

output "postgresql_server_name" {
  value = azurerm_postgresql_flexible_server.postgres.name
}

output "postgresql_fqdn" {
  value = azurerm_postgresql_flexible_server.postgres.fqdn
}

output "managed_redis_hostname" {
  value = azurerm_managed_redis.redis.hostname
}

output "storage_account_name" {
  value = azurerm_storage_account.docs.name
}

output "servicebus_namespace" {
  value = azurerm_servicebus_namespace.servicebus.name
}

output "application_managed_identity_id" {
  value = azurerm_user_assigned_identity.app.id
}

output "application_managed_identity_client_id" {
  value = azurerm_user_assigned_identity.app.client_id
}

output "application_managed_identity_principal_id" {
  value = azurerm_user_assigned_identity.app.principal_id
}
