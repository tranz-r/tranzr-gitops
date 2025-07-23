provider "argocd" {
  server_addr = "argocd.labgrid.net"
  username    = "admin"
  password    = var.argocd_admin_password
}