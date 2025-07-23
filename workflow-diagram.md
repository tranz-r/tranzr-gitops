# Workflow Integration Diagram

```mermaid
graph TD
    A[Developer Push with Conventional Commits] --> B{Commit Lint Check}
    B -->|✅ Valid| C[Semantic Release Workflow]
    B -->|❌ Invalid| D[Block Push]
    
    C --> E[Analyze Commits]
    E --> F{Version Bump Needed?}
    F -->|Yes| G[Create GitHub Release]
    F -->|No| H[Skip Release]
    
    G --> I[Update Chart.yaml appVersion]
    I --> J[Update Chart.yaml version]
    J --> K[Commit Changes]
    K --> L[Repository Dispatch Event]
    
    L --> M[ArgoCD ApplicationSet Workflow]
    M --> N[Terraform Apply]
    N --> O[ArgoCD Sync]
    O --> P[Helm Chart Deployed]
    
    Q[Manual Trigger] --> M
    R[Direct Push to app/] --> M
    
    style C fill:#e1f5fe
    style M fill:#f3e5f5
    style G fill:#e8f5e8
    style P fill:#fff3e0
