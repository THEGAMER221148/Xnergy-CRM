locals { name="${var.prefix}-${var.environment}" }
resource "azurerm_resource_group" "rg" { name="${local.name}-rg" location=var.location }

resource "azurerm_virtual_network" "vnet" {
  name="${local.name}-vnet" location=var.location resource_group_name=azurerm_resource_group.rg.name
  address_space=["10.40.0.0/16"]
}
resource "azurerm_subnet" "apps" {
  name="apps" resource_group_name=azurerm_resource_group.rg.name virtual_network_name=azurerm_virtual_network.vnet.name
  address_prefixes=["10.40.1.0/24"]
}
resource "azurerm_subnet" "db" {
  name="postgres" resource_group_name=azurerm_resource_group.rg.name virtual_network_name=azurerm_virtual_network.vnet.name
  address_prefixes=["10.40.2.0/24"]
  delegation { name="postgres" service_delegation { name="Microsoft.DBforPostgreSQL/flexibleServers" actions=["Microsoft.Network/virtualNetworks/subnets/join/action"] } }
}
resource "azurerm_private_dns_zone" "pg" {
  name="${local.name}.postgres.database.azure.com" resource_group_name=azurerm_resource_group.rg.name
}
resource "azurerm_private_dns_zone_virtual_network_link" "pg" {
  name="pg-link" private_dns_zone_name=azurerm_private_dns_zone.pg.name resource_group_name=azurerm_resource_group.rg.name
  virtual_network_id=azurerm_virtual_network.vnet.id
}

resource "azurerm_key_vault" "kv" {
  name=substr(replace("${local.name}-kv","_","-"),0,24) location=var.location resource_group_name=azurerm_resource_group.rg.name
  tenant_id=data.azurerm_client_config.current.tenant_id sku_name="premium"
  purge_protection_enabled=true soft_delete_retention_days=90 enable_rbac_authorization=true
  public_network_access_enabled=false
}
data "azurerm_client_config" "current" {}

resource "azurerm_storage_account" "docs" {
  name=substr(replace("${var.prefix}${var.environment}docs","-",""),0,24)
  resource_group_name=azurerm_resource_group.rg.name location=var.location
  account_tier="Standard" account_replication_type="ZRS"
  min_tls_version="TLS1_2" public_network_access_enabled=false
  allow_nested_items_to_be_public=false shared_access_key_enabled=false
  blob_properties {
    versioning_enabled=true
    delete_retention_policy { days=30 }
    container_delete_retention_policy { days=30 }
  }
}
resource "azurerm_storage_container" "quarantine" { name="dealos-quarantine" storage_account_id=azurerm_storage_account.docs.id container_access_type="private" }
resource "azurerm_storage_container" "clean" { name="dealos-clean" storage_account_id=azurerm_storage_account.docs.id container_access_type="private" }

resource "azurerm_postgresql_flexible_server" "pg" {
  name="${local.name}-pg" resource_group_name=azurerm_resource_group.rg.name location=var.location
  version="17" delegated_subnet_id=azurerm_subnet.db.id private_dns_zone_id=azurerm_private_dns_zone.pg.id
  administrator_login=var.postgres_admin administrator_password=var.postgres_admin_password
  sku_name="GP_Standard_D4s_v3" storage_mb=131072
  backup_retention_days=35 geo_redundant_backup_enabled=true
  zone="1"
  high_availability { mode="ZoneRedundant" standby_availability_zone="2" }
  depends_on=[azurerm_private_dns_zone_virtual_network_link.pg]
}
resource "azurerm_postgresql_flexible_server_database" "db" {
  name="dealos" server_id=azurerm_postgresql_flexible_server.pg.id collation="en_US.utf8" charset="UTF8"
}

resource "azurerm_redis_cache" "redis" {
  name="${local.name}-redis" location=var.location resource_group_name=azurerm_resource_group.rg.name
  capacity=1 family="P" sku_name="Premium" non_ssl_port_enabled=false minimum_tls_version="1.2"
  redis_configuration {}
}

resource "azurerm_servicebus_namespace" "sb" {
  name="${local.name}-sb" location=var.location resource_group_name=azurerm_resource_group.rg.name sku="Premium" capacity=1
}
resource "azurerm_servicebus_queue" "scan" {
  name="dealos-malware-results" namespace_id=azurerm_servicebus_namespace.sb.id
  lock_duration="PT5M" max_delivery_count=5 dead_lettering_on_message_expiration=true
}

resource "azurerm_log_analytics_workspace" "logs" {
  name="${local.name}-logs" location=var.location resource_group_name=azurerm_resource_group.rg.name
  sku="PerGB2018" retention_in_days=180
}
resource "azurerm_container_app_environment" "cae" {
  name="${local.name}-cae" location=var.location resource_group_name=azurerm_resource_group.rg.name
  log_analytics_workspace_id=azurerm_log_analytics_workspace.logs.id
}
resource "azurerm_user_assigned_identity" "app" {
  name="${local.name}-identity" location=var.location resource_group_name=azurerm_resource_group.rg.name
}
resource "azurerm_role_assignment" "blob" {
  scope=azurerm_storage_account.docs.id role_definition_name="Storage Blob Data Contributor" principal_id=azurerm_user_assigned_identity.app.principal_id
}
resource "azurerm_role_assignment" "sb" {
  scope=azurerm_servicebus_namespace.sb.id role_definition_name="Azure Service Bus Data Receiver" principal_id=azurerm_user_assigned_identity.app.principal_id
}

# Container Apps receives runtime secrets separately through CI/CD or Key Vault references.
resource "azurerm_container_app" "api" {
  name="${local.name}-api" container_app_environment_id=azurerm_container_app_environment.cae.id resource_group_name=azurerm_resource_group.rg.name
  revision_mode="Single"
  identity { type="UserAssigned" identity_ids=[azurerm_user_assigned_identity.app.id] }
  template {
    min_replicas=2 max_replicas=10
    container {
      name="api" image=var.container_image cpu=1.0 memory="2Gi"
      env { name="APP_ENV" value="production" }
      env { name="COOKIE_SECURE" value="true" }
      env { name="MALWARE_SCAN_REQUIRED" value="true" }
      env { name="AZURE_STORAGE_ACCOUNT_URL" value=azurerm_storage_account.docs.primary_blob_endpoint }
      env { name="AZURE_SERVICEBUS_FQDN" value="${azurerm_servicebus_namespace.sb.name}.servicebus.windows.net" }
      env { name="MALWARE_SCAN_QUEUE" value=azurerm_servicebus_queue.scan.name }
    }
  }
  ingress { external_enabled=true target_port=8000 transport="auto" traffic_weight { percentage=100 latest_revision=true } }
}

# IMPORTANT: enable Microsoft Defender for Storage on-upload malware scanning and route
# malware scan results through Event Grid to azurerm_servicebus_queue.scan.
# This remains a subscription/security-control step because organizations may manage Defender centrally.
