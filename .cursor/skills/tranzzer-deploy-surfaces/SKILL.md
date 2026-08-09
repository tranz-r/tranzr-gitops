---
name: tranzzer-deploy-surfaces
description: >-
  Checklist to update api-gateway, tranzr-gitops (External Secrets / Key Vault /
  Helm values), and docker-compose when Tranzzer features add APIs, env vars, or
  secrets. Use when implementing features, adding configuration, introducing
  secrets, new HTTP routes, CORS origins, or deploying backend/frontend changes
  across desktop-webapp, tranzr-moves-services, api-gateway, or tranzr-gitops.
---

# Tranzzer deploy surfaces (do not miss)

When a feature or change introduces **APIs, env, secrets, CORS, or new services**,
assess **all three deploy surfaces** before finishing. Skipping one leaves local
or AKS broken.

## Surfaces

| Surface | Repo / path | When it usually needs a change |
|---------|-------------|-------------------------------|
| **API Gateway** | `api-gateway` | New public route prefixes, auth policies, CORS origins (e.g. portal ports) |
| **GitOps / AKS** | `tranzr-gitops` | New env, secrets via External Secrets → Azure Key Vault, image/deploy wiring |
| **Docker Compose** | `tranzr-moves-services/docker-compose*.yml` + `.env.example` | Local container env for notifications/worker/gateway; document `.env` keys |

Also update **host local secrets** when the API/Worker run outside Compose:
`README.md` Step 4 `dotnet user-secrets` (and Notifications host env if run on host).

## Mandatory assessment (copy into the task)

```
Deploy-surface check:
- [ ] Gateway — new/changed routes? CORS origin? (or N/A with reason)
- [ ] GitOps — values.yaml env and/or ExternalSecret + AKV name? (or N/A)
- [ ] Compose — service env + .env.example? (or N/A)
- [ ] User-secrets / README — documented for local API? (or N/A)
```

Do **not** mark the feature done until each box is decided. Prefer an explicit
**N/A** over silence.

## Secrets (Azure Key Vault + External Secrets)

Pattern in `tranzr-gitops/app/values.yaml`:

1. **Create secret in Azure Key Vault** (manual / platform — not in git):
   ```bash
   az keyvault secret set --vault-name <vault> \
     --name tranzr-<kebab-name> --value '...'
   ```
2. **Register** under `externalSecrets.applicationSecrets`:
   ```yaml
   - secretKey: tranzr-<kebab-name>   # usually == remoteRef.key
     description: "..."
     remoteRef:
       key: tranzr-<kebab-name>
   ```
3. **Wire** `deployments.<service>.envFromSecrets`:
   ```yaml
   - name: Section__Property   # or SCREAMING_SNAKE
     secretKey: tranzr-<kebab-name>
   ```

Conventions:
- AKV / `secretKey`: **kebab-case**; product secrets prefer `tranzr-` prefix
- Sensitive values **only** via External Secrets → Key Vault (never commit)
- Non-sensitive config: `deployments.*.env` literals (e.g. `WebPush__Subject`)
- Public + private key pairs: store **both** in KV when both hosts need the same
  values (avoids drift); treat **private** as must-secret

Templates already range over values — usually **no** template edits.

## Local Compose

- Inject with `${VAR}` from gitignored `.env`
- Add placeholders to `.env.example`
- Map to ASP.NET: `Section__Property` (e.g. `WebPush__VapidPrivateKey`)
- API often runs on **host** (`dotnet run`) — Compose alone is not enough; set
  user-secrets too when the API reads the same options

## Gateway

- Routes: `api-gateway` YARP config (`appsettings*.json`)
- CORS: Development / production allow-lists for new portal origins
- Catch-all routes (e.g. `/api/v1/drivers/{**catch-all}`) may already cover new
  subpaths — still **verify**, do not assume

## Examples

| Change | Gateway | GitOps | Compose |
|--------|---------|--------|---------|
| New driver sub-API under `/api/v1/drivers/**` | Likely N/A if catch-all exists | N/A if no new env | N/A |
| New secret (VAPID, Stripe, ACS) | N/A | AKV + `applicationSecrets` + `envFromSecrets` | `.env` + compose env |
| New portal on port 300X | CORS origin | Ingress/CORS if any | N/A |
| New microservice | New route cluster | New deployment + secrets | New compose service |

## Anti-patterns

- Adding `appsettings.json` placeholders only and shipping to AKS without gitops
- Putting private keys in `NEXT_PUBLIC_*`, ConfigMaps, or git
- Updating Compose but forgetting Key Vault / ExternalSecret (or the reverse)
- Assuming gateway needs no look because “internal API”
