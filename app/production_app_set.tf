resource "argocd_application_set" "production" {
  metadata {
    name = "tranzr-moves"
  }

  spec {
    generator {
      git {
        repo_url = "https://github.com/tranz-r/tranzr-gitops.git"
        revision = "main"

        directory {
          path = "app"
        }
      }
    }

    template {
      metadata {
        name = "tranzr-moves"
      }

      spec {
        project = "tranzr-moves"
        source {
          repo_url        = "https://github.com/tranz-r/tranzr-gitops.git"
          target_revision = "main"
          path            = "{{path}}"

          helm {
            value_files = ["values.yaml"]
          }
        }

        destination {
          server    = "https://kubernetes.default.svc"
          namespace = "tranzr-moves-system"
        }

        sync_policy {
          automated {
            prune     = false
            self_heal = true
          }
          sync_options = ["CreateNamespace=true"]
        }
      }
    }
  }
}