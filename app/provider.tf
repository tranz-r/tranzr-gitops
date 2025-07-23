terraform {
    required_version = ">= 1.0.0"
    required_providers {

    argocd = {
      source = "argoproj-labs/argocd"
      version = "7.3.1"
    }

    azurerm = {
        source  = "hashicorp/azurerm"
        version = "~>4.0"
    }

    helm = {
        source  = "hashicorp/helm"
        version = "2.16.1"
    }
  }

  backend "azurerm" {
    resource_group_name  = "labgrid"
    storage_account_name = "labgrid"
    container_name       = "labgridtfstate"
    key                  = "labgrid.tranzrmoves.tfstate"
  }
}

provider "azurerm" {
  resource_provider_registrations = "all"
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}

provider "helm" {
  kubernetes {
    # config_path = "~/.kube/config" # Update with your kubeconfig path
    host  = var.k8s_host
    token = var.k8s_token
  }
}