terraform {
  backend "azurerm" {
    storage_account_name = "dealosstagingtfstate"
    container_name       = "tfstate"
    key                  = "staging.terraform.tfstate"

    use_oidc         = true
    use_azuread_auth = true
  }
}
