variable "prefix" {
  description = "Short application prefix used in Azure resource names."
  type        = string
  default     = "dealos"
}

variable "location" {
  description = "Azure region for the DealOS deployment."
  type        = string
  default     = "westus2"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "staging"
}

variable "postgres_admin" {
  description = "PostgreSQL administrator username used for provisioning and migrations."
  type        = string
  default     = "dealosadmin"
}

variable "postgres_admin_password" {
  description = "PostgreSQL administrator password."
  type        = string
  sensitive   = true
}

variable "postgres_app_password" {
  description = "Password for the least-privilege dealos_app PostgreSQL runtime role."
  type        = string
  sensitive   = true
}

variable "container_image" {
  description = "Fully qualified container image deployed to Azure Container Apps."
  type        = string
}

variable "app_base_url" {
  description = "Public HTTPS base URL of the deployed DealOS application."
  type        = string
}

variable "oidc_issuer" {
  description = "OIDC issuer used by the DealOS application for user authentication."
  type        = string
}

variable "oidc_client_id" {
  description = "OIDC client/application ID used by DealOS."
  type        = string
}

variable "oidc_client_secret" {
  description = "OIDC confidential-client secret used by DealOS."
  type        = string
  sensitive   = true
}

variable "oidc_allowed_internal_domain" {
  description = "Internal email domain permitted for Xnergy users."
  type        = string
  default     = "xnergy.com"
}

variable "session_ttl_seconds" {
  description = "Server-side authenticated session lifetime in seconds."
  type        = number
  default     = 28800
}

variable "max_upload_bytes" {
  description = "Maximum permitted document upload size in bytes."
  type        = number
  default     = 104857600
}
