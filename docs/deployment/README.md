# Deploying Tarka

Tarka ships in two deployment forms. Choose based on your environment:

| Method | Best for | Guide |
|--------|----------|-------|
| **Helm chart** | Any Kubernetes cluster; recommended for most teams | [helm.md](helm.md) |
| **Standalone manifests** | AWS EKS with full automation (IAM, S3, Secrets Manager) | [manifests.md](manifests.md) |

## Which should I use?

**Helm** if you want a standard, upgradeable Kubernetes deployment you can manage with `helm upgrade`. Works on EKS, GKE, AKS, or any cluster. Secrets and config are your responsibility.

**Manifests** if you are on AWS EKS and want the `deploy.sh` script to handle everything end-to-end — ECR image builds, S3 bucket creation, IAM role + IRSA, AWS Secrets Manager sync, and `kubectl apply` of the raw manifests in `deploy/manifests/`.
