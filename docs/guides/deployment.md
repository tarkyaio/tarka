# Deployment Guide

How to deploy Tarka to Kubernetes for production use.

## Architecture Overview

Tarka runs as multiple components in Kubernetes:

1. **Webhook Receiver**: Receives alerts from Alertmanager, publishes to queue
2. **NATS JetStream**: Durable message queue for async job processing
3. **Workers**: Consume jobs, run investigations, write to S3
4. **Console UI**: React frontend for case browsing and chat
5. **PostgreSQL**: (Optional) Case memory and metadata indexing

## Deployment Methods

### Helm Chart (recommended)

The official Helm chart is published as an OCI artifact to GitHub Container Registry. It manages all Kubernetes resources, supports multiple secret backends, and includes optional NATS and PostgreSQL subcharts.

```bash
helm install tarka oci://ghcr.io/tarkyaio/charts/tarka \
  --namespace tarka --create-namespace \
  --set config.data.PROMETHEUS_URL=http://prometheus.monitoring.svc:9090 \
  --set config.data.ALERTMANAGER_URL=http://alertmanager.monitoring.svc:9093 \
  --set config.data.S3_BUCKET=tarka-reports \
  --set config.data.CLUSTER_NAME=my-cluster
```

See the full guide: **[Helm Chart Deployment](helm-chart.md)**

### Standalone Manifests

Deploy using the raw Kubernetes manifests in `k8s/`. This approach gives you full control over each resource and does not require Helm. The `deploy.sh` script can automate the full deployment including AWS infrastructure setup (IAM roles, S3 buckets, secrets).

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/nats-jetstream.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/worker-deployment.yaml
```

See the full guide: **[Standalone Manifest Deployment](standalone-manifests.md)**

For automated AWS EKS deployment with `deploy.sh`, see **[DEPLOYMENT.md](../../DEPLOYMENT.md)**.

## Storage Requirements

### S3 Bucket

- ~100KB per report
- Structure: `s3://bucket/prefix/YYYY-MM-DD/fingerprint-family.json`
- Configure lifecycle policies based on retention needs

### PostgreSQL (Optional)

- ~50MB per 1000 cases (without embeddings)
- Schema auto-migrated on first run

## Monitoring

### Health Checks

```bash
# Webhook
curl http://tarka-webhook.tarka.svc:8080/healthz

# NATS
curl http://nats.tarka.svc:8222/healthz
```

### Logs

```bash
kubectl logs -n tarka deployment/tarka-webhook -f
kubectl logs -n tarka deployment/tarka-worker -f
```

## Security Best Practices

1. **Least privilege**: Use the read-only ClusterRole for K8s access
2. **Network policies**: Restrict traffic to necessary services (the Helm chart includes Calico, Cilium, and Istio policies)
3. **Secrets management**: Use ExternalSecrets with AWS Secrets Manager, or bring your own Secret
4. **Authentication**: Enable OIDC for Console UI access
5. **TLS**: Use Ingress with TLS termination for production

## Upgrades

Rolling updates are safe for all components. Workers support graceful shutdown, so in-progress investigations complete before pod termination.

## Troubleshooting

See [operations.md](operations.md#troubleshooting) for a detailed troubleshooting guide.
