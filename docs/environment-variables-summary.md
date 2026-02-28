# Environment Variables Summary

## Complete list of environment variables with recommended defaults for production

---

## 📋 Infrastructure Evidence Collection

### **AWS Infrastructure Context** (CloudTrail Consolidated)

| Variable | Type | Default | Production | Description |
|----------|------|---------|------------|-------------|
| `AWS_EVIDENCE_ENABLED` | ConfigMap | `false` | **`false`** | Enable ALL AWS evidence (EC2, EBS, ELB, RDS, ECR, networking, CloudTrail) |
| `AWS_CLOUDTRAIL_LOOKBACK_MINUTES` | ConfigMap | `30` | **`30`** | Minutes before alert to query CloudTrail for precursor events |
| `AWS_CLOUDTRAIL_MAX_EVENTS` | ConfigMap | `50` | **`50`** | Max CloudTrail events to include in pipeline reports |
| `CHAT_ALLOW_AWS_READ` | ConfigMap | `false` | **`false`** | Enable AWS chat tools (requires same IAM as AWS_EVIDENCE_ENABLED) |
| `CHAT_AWS_REGION_ALLOWLIST` | ConfigMap | `""` | **`""`** | Restrict AWS queries to specific regions (empty = allow all) |

**IAM Permissions Required (when enabled):**
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "ec2:Describe*",
      "elasticloadbalancing:Describe*",
      "rds:Describe*",
      "ecr:DescribeRepositories",
      "ecr:ListImages",
      "ecr:BatchGetImage",
      "cloudtrail:LookupEvents"
    ],
    "Resource": "*"
  }]
}
```

**What this enables:**
- **EC2**: Instances, volumes, network interfaces, VPCs, subnets, NAT gateways, NACLs, security groups, route tables
- **ELB**: Load balancers, target groups, target health
- **RDS**: Database instances, snapshots, events
- **ECR**: Repository listing, image listing within repos, image details
- **CloudTrail**: Infrastructure change events (last 90 days)

**Setup Guide:**
1. Create IAM role with above permissions
2. Attach to EKS service account via IRSA or pod IAM
3. Set `AWS_EVIDENCE_ENABLED=1` in ConfigMap
4. Optionally set `CHAT_ALLOW_AWS_READ=1` for chat tools

---

### **GitHub Code Change Context**

| Variable | Type | Default | Production | Description |
|----------|------|---------|------------|-------------|
| `GITHUB_EVIDENCE_ENABLED` | ConfigMap | `false` | **`false`** | Enable GitHub code change tracking in pipeline |
| `CHAT_ALLOW_GITHUB_READ` | ConfigMap | `false` | **`false`** | Enable GitHub chat tools |
| `CHAT_GITHUB_REPO_ALLOWLIST` | ConfigMap | `""` | **`""`** | Restrict to specific repos (e.g., `myorg/repo1,myorg/repo2`) |
| `GITHUB_DEFAULT_ORG` | ConfigMap | `""` | **`""`** | Default GitHub org for repo discovery |
| `GITHUB_APP_ID` | Secret | - | **Required** | GitHub App ID for authentication |
| `GITHUB_APP_PRIVATE_KEY` | Secret | - | **Required** | GitHub App private key (PEM format, base64) |
| `GITHUB_APP_INSTALLATION_ID` | Secret | - | **Required** | GitHub App installation ID |

**Setup Guide:**
1. Create GitHub App: https://github.com/settings/apps/new
2. Grant permissions: Contents (read), Metadata (read), Workflows (read)
3. Generate private key, install app in org
4. Set credentials in Secret
5. Set `GITHUB_EVIDENCE_ENABLED=1` in ConfigMap

---

### **K8s Events Tool**

| Variable | Type | Default | Production | Description |
|----------|------|---------|------------|-------------|
| `CHAT_ALLOW_K8S_EVENTS` | ConfigMap | `true` | **`true`** | Enable K8s events chat tool (uses existing RBAC) |

**No additional setup needed** - uses existing service account RBAC.

---

## 🎯 Recommended Production Defaults

### **ConfigMap (`k8s/configmap.yaml`)**

```yaml
# ---- AWS Infrastructure Context (optional) ----
AWS_EVIDENCE_ENABLED: "0"  # Disabled by default (requires IAM setup)
AWS_CLOUDTRAIL_LOOKBACK_MINUTES: "30"  # Default lookback for precursor events
AWS_CLOUDTRAIL_MAX_EVENTS: "50"  # Max events in reports
CHAT_ALLOW_AWS_READ: "0"  # Disabled by default (requires IAM setup)
CHAT_AWS_REGION_ALLOWLIST: ""  # Empty = allow all regions

# ---- GitHub Code Change Context (optional) ----
GITHUB_EVIDENCE_ENABLED: "0"  # Disabled by default (requires GitHub App)
CHAT_ALLOW_GITHUB_READ: "0"  # Disabled by default (requires GitHub App)
CHAT_GITHUB_REPO_ALLOWLIST: ""  # Empty = allow all discovered repos
GITHUB_DEFAULT_ORG: ""  # Optional: default org for repo discovery

# ---- K8s Events Tool ----
CHAT_ALLOW_K8S_EVENTS: "1"  # Enabled by default (safe, read-only)
```

### **Secret (`k8s/secret.yaml`)**

```yaml
# GitHub App credentials (optional, base64-encoded)
GITHUB_APP_ID: "REPLACE_ME"
GITHUB_APP_PRIVATE_KEY: "REPLACE_ME"  # PEM format
GITHUB_APP_INSTALLATION_ID: "REPLACE_ME"
```

---

## 🔐 Safe-by-Default Philosophy

**✅ Enabled by default:**
- `CHAT_ALLOW_K8S_EVENTS=1` - Uses existing RBAC, no extra permissions

**❌ Disabled by default (opt-in):**
- `AWS_EVIDENCE_ENABLED=0` - Requires IAM role setup
- `CLOUDTRAIL` (part of AWS) - Requires CloudTrail:LookupEvents permission
- `CHAT_ALLOW_AWS_READ=0` - Requires same IAM as AWS evidence
- `GITHUB_EVIDENCE_ENABLED=0` - Requires GitHub App setup
- `CHAT_ALLOW_GITHUB_READ=0` - Requires GitHub App setup

**Rationale:**
- External integrations require explicit setup (IAM roles, GitHub Apps)
- Prevents accidental API calls without proper permissions
- Clear opt-in for teams that want each feature
- Follows principle of least privilege

---

## 📊 Feature Matrix

| Feature | Pipeline | Chat | IAM/Creds Required | Default |
|---------|----------|------|-------------------|---------|
| **K8s Events** | ❌ | ✅ | Existing RBAC | ✅ Enabled |
| **AWS Health Checks** | ✅ | ✅ | IAM role | ❌ Disabled |
| **CloudTrail Changes** | ✅ | ✅ | IAM role | ❌ Disabled |
| **GitHub Changes** | ✅ | ✅ | GitHub App | ❌ Disabled |

---

## 🚀 Enabling Features

### **Enable AWS Evidence (including CloudTrail):**

```bash
# 1. Create IAM role with required permissions (see above)
# 2. Attach to EKS service account
# 3. Update ConfigMap:
kubectl edit configmap tarka-config -n tarka
# Set: AWS_EVIDENCE_ENABLED: "1"

# 4. (Optional) Enable chat tools:
# Set: CHAT_ALLOW_AWS_READ: "1"

# 5. Restart pods
kubectl rollout restart deployment tarka-webhook -n tarka
kubectl rollout restart deployment tarka-worker -n tarka
```

### **Enable GitHub Evidence:**

```bash
# 1. Create GitHub App (see setup guide above)
# 2. Update Secret with credentials:
kubectl edit secret tarka -n tarka
# Set: GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, GITHUB_APP_INSTALLATION_ID

# 3. Update ConfigMap:
kubectl edit configmap tarka-config -n tarka
# Set: GITHUB_EVIDENCE_ENABLED: "1"

# 4. (Optional) Enable chat tools:
# Set: CHAT_ALLOW_GITHUB_READ: "1"

# 5. Restart pods
kubectl rollout restart deployment tarka-webhook -n tarka
kubectl rollout restart deployment tarka-worker -n tarka
```

---

## 📝 Migration from Old Variable Names

If you were using the old `CLOUDTRAIL_EVIDENCE_ENABLED` flag:

### **Before (deprecated):**
```yaml
CLOUDTRAIL_EVIDENCE_ENABLED: "1"
CLOUDTRAIL_LOOKBACK_MINUTES: "30"
CLOUDTRAIL_MAX_PIPELINE_EVENTS: "50"
```

### **After (current):**
```yaml
AWS_EVIDENCE_ENABLED: "1"  # CloudTrail is now part of AWS evidence
AWS_CLOUDTRAIL_LOOKBACK_MINUTES: "30"
AWS_CLOUDTRAIL_MAX_EVENTS: "50"
```

**Rationale:** CloudTrail is an AWS service and should be under the AWS umbrella, not a separate toggle. This simplifies configuration and reduces confusion.

---

## ✅ Verification

After enabling features, verify they work:

```bash
# Check logs for successful evidence collection
kubectl logs -f deployment/tarka-worker -n tarka | grep -i "cloudtrail\|github"

# Trigger a test alert and check the report
# AWS: Should see "### AWS" and "### CloudTrail / Infrastructure Changes" sections
# GitHub: Should see "### GitHub / Changes" section

# Test chat tools (if enabled)
# In console UI chat: "What AWS changes happened?" or "What GitHub changes happened?"
```

---

## 🔍 Troubleshooting

### **CloudTrail events not appearing:**
- ✅ Verify `AWS_EVIDENCE_ENABLED=1` (not the old `CLOUDTRAIL_EVIDENCE_ENABLED`)
- ✅ Check IAM role has `cloudtrail:LookupEvents` permission
- ✅ Verify CloudTrail is enabled in your AWS account
- ✅ Check CloudTrail has events in the time window (5-15 min lag is normal)
- ✅ Verify `AWS_REGION` or alert labels have correct region

### **GitHub evidence not appearing:**
- ✅ Verify `GITHUB_EVIDENCE_ENABLED=1`
- ✅ Check GitHub App credentials are correct in Secret
- ✅ Verify GitHub App is installed in the target org/repos
- ✅ Check GitHub App permissions (Contents, Workflows, Metadata)
- ✅ Verify pods have restarted after secret/configmap changes

---

## 📚 Related Documentation

- [CLAUDE.md](../CLAUDE.md) - Full project documentation
- [docs/guides/authentication.md](./guides/authentication.md) - Auth setup
- [docs/guides/github-app-setup.md](./guides/github-app-setup.md) - GitHub App setup (if exists)
- [docs/cloudtrail_integration_summary.md](./cloudtrail_integration_summary.md) - CloudTrail implementation details
