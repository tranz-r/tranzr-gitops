# TranzrMoves Helm Chart

A comprehensive Helm chart for deploying the TranzrMoves microservices architecture on Kubernetes with Azure Key Vault integration.

## Overview

This chart deploys two main components:
- **tranzr-gateway**: API Gateway service that handles external requests
- **tranzr-service**: Core business logic service

Both services are configured with:
- External secrets management via Azure Key Vault
- Horizontal Pod Autoscaling (HPA)
- Private GitHub Container Registry support
- Production-ready security configurations
- Ingress with TLS termination

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- External Secrets Operator installed in your cluster
- Azure Key Vault ClusterSecretStore configured (`azure-kv-cluster-store`)
- cert-manager for TLS certificate management
- nginx-ingress-controller for ingress

## Installation

### 1. Add Required Secrets to Azure Key Vault

Before deploying, ensure the following secrets are available in your Azure Key Vault:

#### GitHub Registry Credentials
```bash
# GitHub username for container registry access
github-registry-username

# GitHub personal access token with packages:read permission  
github-registry-token
```

#### Application Secrets (Examples - customize based on your needs)
```bash
# Gateway secrets
tranzr-gateway-database-url
tranzr-gateway-api-key
tranzr-gateway-jwt-secret

# Service secrets  
tranzr-service-database-url
tranzr-service-redis-url
tranzr-service-mq-url
```

### 2. Update Values Configuration

Create a custom values file:

```yaml
# values-production.yaml

# Update the external secrets data sections with your actual secret keys
externalSecrets:
  gateway:
    data:
      - secretKey: DATABASE_URL
        remoteRef:
          key: tranzr-gateway-database-url
      - secretKey: API_KEY
        remoteRef:
          key: tranzr-gateway-api-key
      - secretKey: JWT_SECRET
        remoteRef:
          key: tranzr-gateway-jwt-secret

  service:
    data:
      - secretKey: DATABASE_URL
        remoteRef:
          key: tranzr-service-database-url
      - secretKey: REDIS_URL
        remoteRef:
          key: tranzr-service-redis-url
      - secretKey: MESSAGE_QUEUE_URL
        remoteRef:
          key: tranzr-service-mq-url

# Update image repositories if different
deployments:
  gateway:
    image:
      repository: ghcr.io/yourusername/tranzr-gateway
      tag: "v1.0.0"
  
  service:
    image:
      repository: ghcr.io/yourusername/tranzr-service
      tag: "v1.0.0"

# Update hostname for your environment
ingress:
  hosts:
    - host: tranzr-gw.yourdomain.com
      paths:
        - path: /
          pathType: Prefix
          serviceName: tranzr-gateway
          servicePort: 80
  tls:
    - secretName: tranzr-gateway-tls
      hosts:
        - tranzr-gw.yourdomain.com
```

### 3. Install the Chart

```bash
# Install with custom values
helm install tranzrmoves ./Apps/charts/tranzrmoves \
  --namespace tranzrmoves \
  --create-namespace \
  --values values-production.yaml

# Or install with default values (for testing)
helm install tranzrmoves ./Apps/charts/tranzrmoves \
  --namespace tranzrmoves \
  --create-namespace
```

## Configuration

### Key Configuration Options

| Parameter | Description | Default |
|-----------|-------------|---------|
| `deployments.gateway.enabled` | Enable gateway deployment | `true` |
| `deployments.service.enabled` | Enable service deployment | `true` |
| `deployments.gateway.replicaCount` | Number of gateway replicas | `2` |
| `deployments.service.replicaCount` | Number of service replicas | `3` |
| `ingress.enabled` | Enable ingress | `true` |
| `ingress.hostname` | Ingress hostname | `tranzr-gw.labgrid.net` |
| `externalSecrets.gateway.enabled` | Enable gateway external secrets | `true` |
| `externalSecrets.service.enabled` | Enable service external secrets | `true` |
| `externalSecrets.githubRegistry.enabled` | Enable GitHub registry secrets | `true` |

### Autoscaling Configuration

Both deployments support horizontal pod autoscaling:

```yaml
deployments:
  gateway:
    autoscaling:
      enabled: true
      minReplicas: 2
      maxReplicas: 10
      targetCPUUtilizationPercentage: 70
      targetMemoryUtilizationPercentage: 80
```

### Resource Configuration

Configure resource requests and limits:

```yaml
deployments:
  gateway:
    resources:
      requests:
        cpu: 500m
        memory: 512Mi
      limits:
        cpu: 1000m
        memory: 1Gi
```

## Security

### Pod Security Context

The chart enforces security best practices:
- Runs as non-root user (UID 1000)
- Drops all capabilities
- Read-only root filesystem
- Security context group (GID 2000)

### External Secrets

All sensitive data is managed through External Secrets Operator:
- Application secrets from Azure Key Vault
- GitHub registry credentials for private images
- Automatic secret rotation support

## Monitoring & Observability

### Health Checks

Both services are configured with:
- Liveness probes on `/health` endpoint
- Readiness probes on `/ready` endpoint
- Configurable probe timings and thresholds

### Logging

Access application logs:

```bash
# Gateway logs
kubectl logs -n tranzrmoves -l app.kubernetes.io/component=gateway

# Service logs  
kubectl logs -n tranzrmoves -l app.kubernetes.io/component=service
```

## Troubleshooting

### Common Issues

1. **External Secrets not syncing**
   ```bash
   kubectl get externalsecrets -n tranzrmoves
   kubectl describe externalsecret <secret-name> -n tranzrmoves
   ```

2. **Image pull failures**
   ```bash
   # Check if GitHub registry secret exists
   kubectl get secret github-registry-secret -n tranzrmoves
   
   # Verify secret content
   kubectl get secret github-registry-secret -n tranzrmoves -o yaml
   ```

3. **Pod startup issues**
   ```bash
   # Check pod events
   kubectl describe pod <pod-name> -n tranzrmoves
   
   # Check pod logs
   kubectl logs <pod-name> -n tranzrmoves
   ```

### Scaling

Manual scaling:

```bash
# Scale gateway
kubectl scale deployment tranzr-gateway --replicas=5 -n tranzrmoves

# Scale service
kubectl scale deployment tranzr-service --replicas=10 -n tranzrmoves
```

## Upgrading

```bash
# Upgrade with new values
helm upgrade tranzrmoves ./Apps/charts/tranzrmoves \
  --namespace tranzrmoves \
  --values values-production.yaml

# Rollback if needed
helm rollback tranzrmoves 1 --namespace tranzrmoves
```

## Uninstallation

```bash
helm uninstall tranzrmoves --namespace tranzrmoves
```

## Development

### Local Testing

```bash
# Template validation
helm template tranzrmoves ./Apps/charts/tranzrmoves \
  --values values-test.yaml

# Dry run
helm install tranzrmoves ./Apps/charts/tranzrmoves \
  --namespace tranzrmoves \
  --dry-run --debug
```

## Support

For questions and support:
- Email: admin@labgrid.net
- Documentation: https://docs.labgrid.net
- GitHub: https://github.com/labgrid/tranzrmoves 