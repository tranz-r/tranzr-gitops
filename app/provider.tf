terraform {
  required_version = ">= 1.0.0"
  required_providers {

    argocd = {
      source  = "argoproj-labs/argocd"
      version = "7.3.1"
    }

    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~>4.0"
    }

    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "2.33.0"
    }

    helm = {
      source  = "hashicorp/helm"
      version = "3.1.1"
    }

    kubectl = {
      source  = "gavinbunney/kubectl"
      version = "1.19.0"
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

# Use kubeconfig if path is set; otherwise use host + token (OAuth2/Keycloak or token from kubeconfig)
locals {
  use_kubeconfig  = trimspace(var.kubeconfig_path) != ""
  kubeconfig_path = local.use_kubeconfig ? var.kubeconfig_path : null
  k8s_host        = local.use_kubeconfig ? null : (trimspace(var.k8s_host) != "" ? var.k8s_host : null)
  k8s_token       = local.use_kubeconfig ? null : (trimspace(var.k8s_token) != "" ? var.k8s_token : null)
}

provider "kubernetes" {
  config_path = local.kubeconfig_path
  host        = local.k8s_host
  token       = local.k8s_token
}

provider "helm" {
  kubernetes = {
    config_path = local.kubeconfig_path
    host        = local.k8s_host
    token       = local.k8s_token
  }
}

provider "kubectl" {
  config_path = local.kubeconfig_path
  host        = local.k8s_host
  token       = local.k8s_token
}
