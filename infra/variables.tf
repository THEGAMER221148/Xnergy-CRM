variable "prefix" { type=string default="dealos" }
variable "location" { type=string default="westus2" }
variable "environment" { type=string default="prod" }
variable "postgres_admin" { type=string default="dealosadmin" }
variable "postgres_admin_password" { type=string sensitive=true }
variable "container_image" { type=string }
