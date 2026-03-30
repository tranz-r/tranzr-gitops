# Staging: develop branch -> staging cluster
resource "argocd_application_set" "staging" {
  metadata {
    name = "tranzr-moves-staging"
  }

  spec {
    generator {
      git {
        repo_url = "https://github.com/tranz-r/tranzr-gitops.git"
        revision = "develop"

        directory {
          path = "app"
        }
      }
    }

    template {
      metadata {
        name = "tranzr-moves-staging"
      }

      spec {
        project = "tranzr-moves-staging"
        source {
          repo_url        = "https://github.com/tranz-r/tranzr-gitops.git"
          target_revision = "develop"
          path            = "{{path}}"

          helm {
            value_files = ["values.yaml", "values-staging.yaml"]
          }
        }

        destination {
          server    = var.staging_cluster_server
          namespace = var.staging_namespace
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
