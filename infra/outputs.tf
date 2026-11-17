output "web_url" {
  value = var.enable_runtime ? "https://${azurerm_container_app.web[0].ingress[0].fqdn}" : null
}
output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}
output "vault_uri" {
  value = azurerm_key_vault.main.vault_uri
}
