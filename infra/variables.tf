variable "subscription_id" {
  type = string
}
variable "location" {
  type    = string
  default = "eastus"
}
variable "prefix" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{5,15}$", var.prefix))
    error_message = "Use a globally unique lowercase alphanumeric prefix with 6 to 16 characters."
  }
}
variable "image" {
  type        = string
  description = "Public non-sensitive application image pinned by SHA-256 digest."
  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.image))
    error_message = "Pin the container image by SHA-256 digest."
  }
}
variable "entra_client_id" {
  type = string
}
variable "entra_owner_oid" {
  type = string
}
variable "database_dsn_secret_uri" {
  type        = string
  description = "Versionless Key Vault URL to the TLS PostgreSQL connection string. Populate out of band."
}
variable "openai_secret_uri" {
  type    = string
  default = null
}
variable "postgres_bootstrap_password" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "job_boards_json" {
  type    = string
  default = "[]"
}
variable "discovery_cron" {
  type    = string
  default = "*/30 * * * *"
}
variable "enable_runtime" {
  type    = bool
  default = false
}
