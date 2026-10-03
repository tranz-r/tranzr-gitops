# GitHub Workflows Documentation

This repository uses automated workflows to handle semantic versioning and deployment.

## Workflows Overview

### 1. **Semantic Release (`semantic-release.yaml`)**
- **Trigger**: Push to `main` branch (app changes, excluding Chart.yaml)
- **Purpose**: Automatic versioning based on conventional commits
- **Actions**:
  - Analyzes commit messages for version bumps
  - Creates GitHub releases and tags
  - Updates `Chart.yaml` with new versions
  - Triggers ArgoCD deployment

### 2. **ArgoCD ApplicationSet (`argocd-applicationset.yaml`)**
- **Triggers**: 
  - Repository dispatch from semantic release
  - Manual workflow dispatch
  - Push to main/develop (direct changes)
- **Purpose**: Deploy ApplicationSet to ArgoCD via Terraform
- **Actions**:
  - Authenticates with Azure and Kubernetes
  - Applies Terraform configuration
  - Creates/updates ArgoCD ApplicationSet

### 3. **Commit Lint (`commitlint.yaml`)**
- **Trigger**: Push to `main`, all `pull_request` events (not `pull_request_target`)
- **Purpose**: Validates conventional commit format with pinned PyPI `commitlint==1.3.0` (matches action release `v1.3.0`)
- **Range**: Trusted event SHAs via env — PR `base..head`, push `before..after` (zero SHAs fail closed)
- **Historical exception**: Exact SHA `7c91ac878c344c53bb385c57bc8c8e75ac5eef06` (`Develop (#44)`) may skip lint only when it is a confirmed ancestor of `origin/main`. It was already accepted on main; ancestry-repair PRs reintroduce that SHA into the PR range. Any other commit, including a new one with the same title, is still validated. This is not a filter of all main commits.
- **Ensures**: Proper commit messages for semantic release

## Workflow Sequence

```
Developer Push (conventional commit)
         ↓
    Commit Lint ✅
         ↓
   Semantic Release
         ↓
   Chart.yaml Updated
         ↓
   Repository Dispatch
         ↓
   ArgoCD Deployment
         ↓
   Helm Chart Deployed
```

## Conventional Commit Examples

```bash
# Patch version bump (1.16.0 → 1.16.1)
git commit -m "fix: resolve gateway timeout issue"

# Minor version bump (1.16.0 → 1.17.0)
git commit -m "feat: add health check endpoint"

# Major version bump (1.16.0 → 2.0.0)
git commit -m "feat!: redesign API structure

BREAKING CHANGE: All endpoints now use v2 format"
```

## Required Secrets

- `AZURE_CREDENTIALS`: Azure service principal
- `KEYCLOAK_ISSUER_URL`: Keycloak endpoint
- `KEYCLOAK_CLIENT_SECRET`: Keycloak client secret
- `K8S_API_SERVER`: Kubernetes API server URL
- `ARGOCD_ADMIN_PASSWORD`: ArgoCD admin password
- `GITHUB_TOKEN`: Automatically provided by GitHub

## Chart Version Management

- **appVersion**: Managed by semantic release (application version)
- **version**: Auto-incremented patch version (chart version)
- Both versions are updated automatically in `Chart.yaml`
