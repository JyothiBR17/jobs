locals {
  separate_workload_region = var.workload_location != var.location
  apps_subnet_id           = local.separate_workload_region ? azurerm_subnet.workload_apps[0].id : azurerm_subnet.apps.id
  postgres_subnet_id       = local.separate_workload_region ? azurerm_subnet.workload_pg[0].id : azurerm_subnet.pg.id
}

resource "azurerm_virtual_network" "workload" {
  count               = local.separate_workload_region ? 1 : 0
  name                = "${var.prefix}-${var.workload_location}-vnet"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.workload_location
  address_space       = ["10.63.0.0/16"]
}

resource "azurerm_subnet" "workload_apps" {
  count                = local.separate_workload_region ? 1 : 0
  name                 = "${var.prefix}-apps"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.workload[0].name
  address_prefixes     = ["10.63.0.0/23"]
  delegation {
    name = "container-apps"
    service_delegation {
      name = "Microsoft.App/environments"
    }
  }
}

resource "azurerm_subnet" "workload_pg" {
  count                = local.separate_workload_region ? 1 : 0
  name                 = "${var.prefix}-postgres"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.workload[0].name
  address_prefixes     = ["10.63.2.0/27"]
  delegation {
    name = "postgres"
    service_delegation {
      name = "Microsoft.DBforPostgreSQL/flexibleServers"
    }
  }
}

resource "azurerm_private_dns_zone_virtual_network_link" "workload" {
  count                 = local.separate_workload_region ? 1 : 0
  name                  = "${var.prefix}-${var.workload_location}-postgres"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.pg.name
  virtual_network_id    = azurerm_virtual_network.workload[0].id
}
