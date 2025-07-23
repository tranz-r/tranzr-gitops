# Complete Deployment Flow

## Overview
This document outlines the complete flow from code commit to Kubernetes deployment using semantic release and ArgoCD.

## Event Flow Diagram

```mermaid
graph TD
    A[👨‍💻 Developer makes changes] --> B[📝 Git commit with conventional format]
    B --> C{🔍 Commitlint validation}
    C -->|❌ Invalid format| D[🚫 Push blocked]
    C -->|✅ Valid format| E[📤 Push to main branch]
    
    E --> F[🚀 Semantic Release Workflow Triggered]
    F --> G[📊 Analyze commit messages]
    G --> H{🎯 Version bump needed?}
    
    H -->|No changes| I[⏭️ Skip release]
    H -->|Changes detected| J[🏷️ Determine version bump]
    
    J --> K[📋 Create GitHub Release]
    K --> L[📝 Update Chart.yaml appVersion]
    L --> M[📝 Update Chart.yaml version]
    M --> N[💾 Commit changes [skip ci]]
    N --> O[📡 Repository Dispatch Event]
    
    O --> P[🎯 ArgoCD Workflow Triggered]
    P --> Q[🔐 Azure & K8s Authentication]
    Q --> R[🏗️ Terraform Init/Plan/Apply]
    R --> S[📦 ApplicationSet Updated]
    
    S --> T[👁️ ArgoCD Detects Changes]
    T --> U[🔄 ArgoCD Sync Process]
    U --> V[⚙️ Helm Template Generation]
    V --> W[🚀 Deploy to Kubernetes]
    
    W --> X[✅ Pods Running in tranzr-moves-system]
    
    style F fill:#e3f2fd
    style P fill:#f3e5f5
    style K fill:#e8f5e8
    style W fill:#fff3e0
    style X fill:#e8f5e8
```

## Detailed Step-by-Step Flow

### Phase 1: Development & Commit Validation
1. **Developer Workflow**
   - Makes code changes in `app/` directory
   - Commits using conventional format: `feat:`, `fix:`, `chore:`, etc.

2. **Commit Validation**
   - Commitlint workflow validates commit message format
   - Blocks push if format is invalid
   - Allows push if format follows conventional commits

### Phase 2: Semantic Release Process
3. **Trigger Conditions**
   ```yaml
   on:
     push:
       branches: [main]
       paths: ['app/**', '!app/Chart.yaml']
   ```

4. **Version Analysis**
   - `fix:` → Patch version (1.16.0 → 1.16.1)
   - `feat:` → Minor version (1.16.0 → 1.17.0)
   - `feat!:` or `BREAKING CHANGE:` → Major version (1.16.0 → 2.0.0)

5. **Chart.yaml Updates**
   ```bash
   # Before
   version: 0.1.0
   appVersion: "1.16.0"
   
   # After semantic release
   version: 0.1.1        # Auto-incremented
   appVersion: "1.17.0"  # Semantic version
   ```

6. **Repository Dispatch**
   ```json
   {
     "event-type": "deploy-argocd",
     "client-payload": {
       "version": "1.17.0",
       "ref": "refs/heads/main"
     }
   }
   ```

### Phase 3: ArgoCD Deployment Process
7. **Terraform Workflow Triggered**
   - Repository dispatch event triggers ArgoCD workflow
   - Can also be triggered manually or by direct app changes

8. **Infrastructure Authentication**
   ```bash
   # Azure authentication
   ARM_CLIENT_ID, ARM_CLIENT_SECRET, ARM_TENANT_ID, ARM_SUBSCRIPTION_ID
   
   # Kubernetes authentication via OIDC
   OIDC_TOKEN from Keycloak
   ```

9. **Terraform Execution**
   ```bash
   terraform init -upgrade
   terraform plan
   terraform apply -auto-approve
   ```

10. **ApplicationSet Configuration**
    ```yaml
    # Generated ApplicationSet
    metadata:
      name: tranzr-moves
    spec:
      generator:
        git:
          repoURL: https://github.com/tranz-r/tranzr-gitops.git
          path: app
      template:
        metadata:
          name: tranzr-moves
        spec:
          destination:
            namespace: tranzr-moves-system
          source:
            path: app
            helm:
              valueFiles: [values.yaml]
    ```

### Phase 4: ArgoCD Sync & Deployment
11. **ArgoCD Detection**
    - ArgoCD monitors the Git repository
    - Detects changes in `app/Chart.yaml` and templates
    - Triggers automatic sync (due to `self_heal: true`)

12. **Helm Processing**
    ```bash
    # ArgoCD generates manifests
    helm template tranzr-moves app/ --values values.yaml
    ```

13. **Kubernetes Deployment**
    - Creates/updates resources in `tranzr-moves-system` namespace
    - Deploys organized manifests from `app/templates/`:
      - Deployments: Gateway & Backend
      - Services: Gateway & Backend  
      - HPAs: Auto-scaling configurations
      - External Secrets: Azure Key Vault integration
      - Ingress: External access configuration
      - ServiceAccount: RBAC configuration

### Phase 5: Final State
14. **Running Application**
    ```bash
    # Deployed resources
    namespace: tranzr-moves-system
    ├── deployment/tranzr-gateway (2 replicas)
    ├── deployment/tranzr-service (3 replicas) 
    ├── service/tranzr-gateway
    ├── service/tranzr-service
    ├── hpa/tranzr-gateway-hpa
    ├── hpa/tranzr-service-hpa
    ├── ingress/tranzrmoves-ingress
    ├── externalsecret/tranzrmoves-secrets
    ├── externalsecret/github-registry-secret
    └── serviceaccount/tranzrmoves
    ```

## Trigger Scenarios

### Scenario A: Feature Addition
```bash
git commit -m "feat: add user authentication endpoint"
# Result: 1.16.0 → 1.17.0, full deployment
```

### Scenario B: Bug Fix
```bash
git commit -m "fix: resolve gateway timeout issue" 
# Result: 1.16.0 → 1.16.1, full deployment
```

### Scenario C: Breaking Change
```bash
git commit -m "feat!: redesign API structure

BREAKING CHANGE: All endpoints now use v2 format"
# Result: 1.16.0 → 2.0.0, full deployment
```

### Scenario D: Non-functional Change
```bash
git commit -m "docs: update README"
# Result: No version bump, no deployment
```

## Monitoring & Verification

### GitHub Actions
- Check workflow runs in Actions tab
- View semantic release summaries
- Monitor Terraform apply results

### ArgoCD Dashboard
- Access: https://argocd.labgrid.net
- Application: `tranzr-moves`
- Namespace: `tranzr-moves-system`

### Kubernetes Verification
```bash
# Check application status
kubectl get all -n tranzr-moves-system

# Check external secrets
kubectl get externalsecrets -n tranzr-moves-system

# Check ingress
kubectl get ingress -n tranzr-moves-system
```

### Application Access
- External URL: https://tranzr-gw.labgrid.net
- TLS Certificate: Let's Encrypt (automatic)
- Backend: nginx-ingress-controller

## Benefits of This Flow

✅ **Automated Versioning**: No manual version management  
✅ **Consistent Deployments**: Reproducible via GitOps  
✅ **Audit Trail**: Full history via Git commits and GitHub releases  
✅ **Rollback Capability**: Easy revert via ArgoCD or Git  
✅ **Security**: Secrets managed via Azure Key Vault  
✅ **Scalability**: Auto-scaling via HPA configurations  
✅ **Monitoring**: Built-in health checks and probes  

