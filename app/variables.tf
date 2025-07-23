variable "argocd_admin_password" {
  description = "ArgoCD admin password"
  sensitive = true
}

variable "k8s_host" {
  description = "Kubernetes host"
  type = string
}

variable "k8s_token" {
  description = "Kubernetes token"
  sensitive = true
  type = string
}