resource "azurerm_resource_group" "main" {
  name     = "${var.prefix}-rg"
  location = var.location
}

resource "azurerm_virtual_network" "main" {
  name                = "${var.prefix}-vnet"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  address_space       = ["10.62.0.0/16"]
}

resource "azurerm_subnet" "apps" {
  name                 = "apps"
  virtual_network_name = azurerm_virtual_network.main.name
  resource_group_name  = azurerm_resource_group.main.name
  address_prefixes     = ["10.62.0.0/23"]
  delegation {
    name = "container-apps"
    service_delegation {
      name = "Microsoft.App/environments"
    }
  }
}

resource "azurerm_subnet" "pg" {
  name                 = "postgres"
  virtual_network_name = azurerm_virtual_network.main.name
  resource_group_name  = azurerm_resource_group.main.name
  address_prefixes     = ["10.62.2.0/27"]
  delegation {
    name = "postgres"
    service_delegation {
      name = "Microsoft.DBforPostgreSQL/flexibleServers"
    }
  }
}

resource "azurerm_private_dns_zone" "pg" {
  name                = "private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "pg" {
  name                  = "${var.prefix}-postgres"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.pg.name
  virtual_network_id    = azurerm_virtual_network.main.id
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                              = "${var.prefix}-pg"
  resource_group_name               = azurerm_resource_group.main.name
  location                          = var.location
  version                           = "16"
  delegated_subnet_id               = azurerm_subnet.pg.id
  private_dns_zone_id               = azurerm_private_dns_zone.pg.id
  public_network_access_enabled     = false
  administrator_login               = "jobhuntadmin"
  administrator_password_wo         = var.postgres_bootstrap_password
  administrator_password_wo_version = 1
  storage_mb                        = 32768
  sku_name                          = "B_Standard_B1ms"
  backup_retention_days             = 14
  depends_on                        = [azurerm_private_dns_zone_virtual_network_link.pg]
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "jobhunter"
  server_id = azurerm_postgresql_flexible_server.main.id
  collation = "en_US.utf8"
  charset   = "UTF8"
}

resource "azurerm_storage_account" "main" {
  name                            = "${var.prefix}docs"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  min_tls_version                 = "TLS1_2"
  blob_properties {
    delete_retention_policy {
      days = 14
    }
  }
}

resource "azurerm_storage_container" "resumes" {
  name                  = "resumes"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

resource "azurerm_user_assigned_identity" "apps" {
  name                = "${var.prefix}-runtime"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_key_vault" "main" {
  name                       = "${var.prefix}-vault"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 14
}

resource "azurerm_role_assignment" "vault" {
  scope                = azurerm_key_vault.main.id
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
  role_definition_name = "Key Vault Secrets User"
}

resource "azurerm_role_assignment" "resume" {
  scope                = azurerm_storage_account.main.id
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
  role_definition_name = "Storage Blob Data Contributor"
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.prefix}-logs"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 0.1
}

resource "azurerm_application_insights" "main" {
  name                = "${var.prefix}-insights"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
}

resource "azurerm_container_app_environment" "main" {
  name                       = "${var.prefix}-env"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id   = azurerm_subnet.apps.id
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

locals {
  runtime_env = {
    APP_ENV          = "production"
    ENTRA_TENANT     = data.azurerm_client_config.current.tenant_id
    ENTRA_AUDIENCE   = var.entra_client_id
    ENTRA_CLIENT_ID  = var.entra_client_id
    ENTRA_SCOPE      = "api://${var.entra_client_id}/access_as_user"
    ENTRA_OWNER_OID  = var.entra_owner_oid
    AZURE_CLIENT_ID  = azurerm_user_assigned_identity.apps.client_id
    BLOB_ACCOUNT_URL = azurerm_storage_account.main.primary_blob_endpoint
    JOB_BOARDS_JSON  = var.job_boards_json
  }
}

resource "azurerm_container_app" "web" {
  count                        = var.enable_runtime ? 1 : 0
  name                         = "${var.prefix}-web"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }
  secret {
    name                = "database"
    identity            = azurerm_user_assigned_identity.apps.id
    key_vault_secret_id = var.database_dsn_secret_uri
  }
  ingress {
    external_enabled           = true
    allow_insecure_connections = false
    target_port                = 8000
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
  template {
    min_replicas = 0
    max_replicas = 2
    container {
      name   = "web"
      image  = var.image
      cpu    = 0.25
      memory = "0.5Gi"
      env {
        name        = "DATABASE_URL"
        secret_name = "database"
      }
      dynamic "env" {
        for_each = local.runtime_env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [azurerm_role_assignment.vault, azurerm_role_assignment.resume]
}

resource "azurerm_container_app_job" "worker" {
  count                        = var.enable_runtime ? 1 : 0
  name                         = "${var.prefix}-worker"
  location                     = var.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_timeout_in_seconds   = 1100
  replica_retry_limit          = 1
  schedule_trigger_config {
    cron_expression          = var.discovery_cron
    parallelism              = 1
    replica_completion_count = 1
  }
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }
  secret {
    name                = "database"
    identity            = azurerm_user_assigned_identity.apps.id
    key_vault_secret_id = var.database_dsn_secret_uri
  }
  dynamic "secret" {
    for_each = var.openai_secret_uri == null ? [] : [var.openai_secret_uri]
    content {
      name                = "openai"
      identity            = azurerm_user_assigned_identity.apps.id
      key_vault_secret_id = secret.value
    }
  }
  template {
    container {
      name    = "worker"
      image   = var.image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["python", "-m", "app.worker"]
      env {
        name        = "DATABASE_URL"
        secret_name = "database"
      }
      dynamic "env" {
        for_each = var.openai_secret_uri == null ? [] : ["openai"]
        content {
          name        = "OPENAI_API_KEY"
          secret_name = env.value
        }
      }
      dynamic "env" {
        for_each = local.runtime_env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [azurerm_role_assignment.vault]
}

resource "azurerm_servicebus_namespace" "main" {
  name                = "${var.prefix}-bus"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"
  local_auth_enabled  = false
  minimum_tls_version = "1.2"
}

resource "azurerm_servicebus_queue" "pipeline" {
  for_each                             = toset(["discovery", "analysis", "sponsorship", "documents", "preparation", "approval", "notification"])
  name                                 = each.key
  namespace_id                         = azurerm_servicebus_namespace.main.id
  max_delivery_count                   = 5
  dead_lettering_on_message_expiration = true
}
