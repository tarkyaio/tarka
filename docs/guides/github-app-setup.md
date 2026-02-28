# GitHub App Setup Guide

This guide explains how to set up a GitHub App for the Tarka to collect code change context (commits, workflow runs, documentation) during incident investigations.

## Why GitHub App?

GitHub Apps are the recommended way to integrate with GitHub because:
- **Scoped permissions**: Read-only access to specific repositories
- **Short-lived tokens**: 1-hour token TTL with automatic refresh
- **Not tied to individual users**: Survives employee turnover
- **Audit trail**: All API calls are logged under the app, not a user

## Prerequisites

- GitHub organization admin access (or repo admin for specific repos)
- Kubernetes cluster with the Tarka deployed
- Ability to configure secrets in your deployment

## Step 1: Create GitHub App

1. Go to your GitHub organization settings: `https://github.com/organizations/<your-org>/settings/apps`
2. Click **"New GitHub App"**
3. Configure the app:
   - **GitHub App name**: `tarka-<your-cluster>` (must be globally unique)
   - **Homepage URL**: Your Tarka console URL (e.g., `https://tarka.yourcompany.com`)
   - **Webhook**: Uncheck "Active" (we don't need webhooks)

## Step 2: Configure Permissions

Set the following **Repository permissions** (all read-only):

- **Contents**: Read (to access commits, files, docs)
- **Actions**: Read (to access workflow runs and logs)
- **Metadata**: Read (automatically granted, provides repo metadata)

Leave all other permissions as "No access".

## Step 3: Install the App

1. After creating the app, go to **"Install App"** in the left sidebar
2. Click **"Install"** next to your organization
3. Choose installation scope:
   - **All repositories**: Simplest, works for all services
   - **Only select repositories**: More restrictive, requires updating when new services are added

4. Click **"Install"**
5. Note the **Installation ID** from the URL after installation:
   ```
   https://github.com/organizations/<org>/settings/installations/<INSTALLATION_ID>
   ```

## Step 4: Generate Private Key

1. Go back to the app settings: `https://github.com/organizations/<your-org>/settings/apps/<app-name>`
2. Scroll down to **"Private keys"**
3. Click **"Generate a private key"**
4. Download the `.pem` file (keep this secure!)

## Step 5: Extract App ID

1. In the app settings page, note the **App ID** (near the top, below the app name)

## Step 6: Configure the Tarka

Add the following environment variables to your deployment:

### Via Kubernetes Secret (Recommended)

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tarka-github
  namespace: tarka
type: Opaque
stringData:
  github-app-id: "123456"  # Your App ID
  github-app-installation-id: "78901234"  # Your Installation ID
  github-app-private-key: |
    -----BEGIN RSA PRIVATE KEY-----
    <contents of your .pem file>
    -----END RSA PRIVATE KEY-----
```

### Via Deployment Environment Variables

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tarka
spec:
  template:
    spec:
      containers:
      - name: agent
        env:
        # Enable GitHub evidence collection
        - name: GITHUB_EVIDENCE_ENABLED
          value: "1"

        # Enable GitHub chat tools (optional, for interactive RCA)
        - name: CHAT_ALLOW_GITHUB_READ
          value: "1"

        # GitHub App credentials
        - name: GITHUB_APP_ID
          valueFrom:
            secretKeyRef:
              name: tarka-github
              key: github-app-id

        - name: GITHUB_APP_INSTALLATION_ID
          valueFrom:
            secretKeyRef:
              name: tarka-github
              key: github-app-installation-id

        - name: GITHUB_APP_PRIVATE_KEY
          valueFrom:
            secretKeyRef:
              name: tarka-github
              key: github-app-private-key

        # Optional: Default org for repo discovery
        - name: GITHUB_DEFAULT_ORG
          value: "myorg"

        # Optional: Restrict chat tools to specific repos
        - name: CHAT_GITHUB_REPO_ALLOWLIST
          value: "myorg/service1,myorg/service2"
```

## Step 7: Verify Setup

1. Restart the Tarka deployment
2. Trigger an alert for a service with a GitHub repo
3. Check the investigation report for the **"GitHub / Changes"** section
4. Verify it shows:
   - Repository discovered
   - Recent commits (if any in time window)
   - Workflow runs (if any)

Example report section:
```markdown
### GitHub / Changes

**Repository:** `myorg/my-service` (discovered via: naming_convention)

**Recent Commits** (time window before alert):
- `abc1234` by alice: fix: increase connection pool size
  - 2026-02-18T10:00:00Z

**Recent Builds:**
- ✅ Workflow `CI` #12345: completed/success
  - 2026-02-18T10:05:00Z
```

## Troubleshooting

### "github_repo_not_found" Error

**Symptom**: Reports show no GitHub section, logs show "github_repo_not_found"

**Causes**:
1. Service name doesn't match GitHub repo naming convention
2. No K8s annotations pointing to GitHub repo
3. Service not in service catalog

**Solutions**:
- Add `github.com/repo` annotation to Deployment/StatefulSet:
  ```yaml
  metadata:
    annotations:
      github.com/repo: "myorg/my-service"
  ```
- Set `GITHUB_DEFAULT_ORG` environment variable
- Add service to `config/service-catalog.yaml`:
  ```yaml
  services:
    my-service:
      github_repo: "myorg/my-service"
  ```

### "API rate limit exceeded" Error

**Symptom**: GitHub evidence collection fails with rate limit errors

**Causes**:
- Too many investigations running concurrently
- App making too many API calls per repo

**Solutions**:
- GitHub Apps get 5,000 requests/hour per installation
- Each investigation makes ~3-5 API calls (commits, workflows, files)
- Monitor rate limit usage via chat tool or GitHub API
- Consider increasing investigation time window to reduce frequency

### Authentication Failures

**Symptom**: Logs show "401 Unauthorized" or "403 Forbidden"

**Causes**:
1. Invalid App ID or Installation ID
2. Expired or invalid private key
3. App not installed on target repository

**Solutions**:
- Verify App ID and Installation ID in GitHub settings
- Re-generate private key and update secret
- Check app installation scope includes the target repo

### Chat Tools Not Working

**Symptom**: Chat doesn't show GitHub tools or tools return errors

**Causes**:
1. `CHAT_ALLOW_GITHUB_READ=0` (disabled)
2. Repo not in allowlist (if configured)

**Solutions**:
- Set `CHAT_ALLOW_GITHUB_READ=1`
- Add repo to `CHAT_GITHUB_REPO_ALLOWLIST` or remove allowlist restriction

## Security Best Practices

1. **Rotate private keys periodically** (e.g., every 90 days)
2. **Use Kubernetes Secrets** (not ConfigMaps) for credentials
3. **Enable audit logging** in GitHub org settings to monitor app API usage
4. **Restrict installation scope** to only repos that need it
5. **Use repo allowlist** (`CHAT_GITHUB_REPO_ALLOWLIST`) in production to prevent accidental queries to sensitive repos

## Service Discovery Methods

The agent tries multiple methods to discover which GitHub repo corresponds to a K8s workload:

1. **K8s annotations**: `github.com/repo` or `tarka.io/github-repo` on Deployment/StatefulSet
2. **Alert labels**: `github_repo` label in Prometheus alert
3. **Naming convention**: `{GITHUB_DEFAULT_ORG}/{workload-name}` (e.g., `myorg/payment-service`)
4. **Service catalog**: `config/service-catalog.yaml` mapping
5. **Helm metadata**: Chart source from Helm release secrets
6. **OCI image labels**: `org.opencontainers.image.source` label from container image
7. **Third-party catalog**: `config/third-party-catalog.yaml` for common OSS services (CoreDNS, cert-manager, etc.)
8. **Graceful skip**: If no repo found, report shows "source unavailable"

**Recommendation**: Use K8s annotations for first-party services, and the agent will auto-discover them:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payment-service
  annotations:
    github.com/repo: "myorg/payment-service"
```

## Further Reading

- [GitHub Apps Documentation](https://docs.github.com/en/apps)
- [GitHub API Rate Limits](https://docs.github.com/en/rest/overview/rate-limits-for-the-rest-api)
- [Tarka Chat Tools](../chat_tools.md)
