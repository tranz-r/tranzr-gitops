variable "argocd_admin_password" {
  description = "ArgoCD admin password"
  sensitive   = true
}

# Cluster auth: use either kubeconfig_path OR (k8s_host + k8s_token)
variable "kubeconfig_path" {
  type        = string
  default     = ""
  description = "Path to kubeconfig file (optional; used when set, otherwise host+token are used)"
}

variable "k8s_host" {
  description = "Kubernetes API server URL (optional when kubeconfig_path is set)"
  type        = string
  default     = ""
}

variable "k8s_token" {
  description = "Kubernetes bearer token (optional when kubeconfig_path is set)"
  sensitive   = true
  type        = string
  default     = ""
}

# Production cluster (Argo CD in-cluster or explicit URL)
variable "production_cluster_server" {
  description = "Kubernetes API server URL for production cluster (Argo CD destination)"
  type        = string
  sensitive   = true
}

variable "production_namespace" {
  description = "Namespace for production deployment"
  type        = string
  default     = "tranzr-moves-system"
}

# Staging cluster (must be registered in Argo CD)
variable "staging_cluster_server" {
  description = "Kubernetes API server URL for staging cluster (Argo CD destination)"
  type        = string
  default     = "https://kubernetes.default.svc"
}

variable "staging_namespace" {
  description = "Namespace for staging deployment"
  type        = string
  default     = "tranzr-moves-system"
}
