# Authentication Guide

The Tarka supports two authentication methods:

1. **OIDC/OAuth2** (SSO) - For enterprise identity providers
2. **Local username/password** - Admin fallback (always available)

Authentication is **always required** - there is no "disabled" mode.

---

## Quick Start

### Minimal Configuration (Local Auth Only)

For development or single-user deployments, only 3 environment variables are needed:

```bash
AUTH_SESSION_SECRET=<random-64-char-string>
ADMIN_INITIAL_USERNAME=admin
ADMIN_INITIAL_PASSWORD=<secure-password>
```

This provides username/password authentication with an initial admin account.

### With OIDC (Recommended for Teams)

Add OIDC configuration for SSO:

```bash
# Required
AUTH_SESSION_SECRET=<random-64-char-string>
AUTH_PUBLIC_BASE_URL=https://your-domain.com
ADMIN_INITIAL_USERNAME=admin
ADMIN_INITIAL_PASSWORD=<secure-password>

# OIDC configuration (3 variables)
OIDC_DISCOVERY_URL=https://your-provider.com/.well-known/openid-configuration
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=your-client-secret
```

---

## OIDC Provider Setup

The agent uses **generic OIDC/OAuth2** and works with any compliant provider:
- Google Workspace
- Okta
- Azure AD (Microsoft Entra ID)
- Auth0
- Keycloak
- And any other OIDC-compliant provider

### Google Workspace

1. **Create OAuth2 credentials** in [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
   - Application type: Web application
   - Authorized redirect URIs: `https://your-domain.com/api/auth/callback/oidc`

2. **Environment variables**:
   ```bash
   OIDC_DISCOVERY_URL=https://accounts.google.com/.well-known/openid-configuration
   OIDC_CLIENT_ID=xxx.apps.googleusercontent.com
   OIDC_CLIENT_SECRET=GOCSPX-xxx
   AUTH_PUBLIC_BASE_URL=https://your-domain.com
   ```

3. **Optional: Restrict to specific domain**:
   ```bash
   AUTH_ALLOWED_DOMAINS=yourcompany.com
   ```

### Okta

1. **Create OIDC application** in Okta admin console
   - Application type: Web
   - Sign-in redirect URIs: `https://your-domain.com/api/auth/callback/oidc`
   - Grant types: Authorization Code
   - Client authentication: Client secret

2. **Environment variables**:
   ```bash
   OIDC_DISCOVERY_URL=https://your-company.okta.com/.well-known/openid-configuration
   OIDC_CLIENT_ID=your-okta-client-id
   OIDC_CLIENT_SECRET=your-okta-client-secret
   AUTH_PUBLIC_BASE_URL=https://your-domain.com
   ```

### Azure AD (Microsoft Entra ID)

1. **Register application** in [Azure Portal](https://portal.azure.com/)
   - Redirect URI: `https://your-domain.com/api/auth/callback/oidc`
   - Create client secret

2. **Environment variables**:
   ```bash
   OIDC_DISCOVERY_URL=https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration
   OIDC_CLIENT_ID={application-client-id}
   OIDC_CLIENT_SECRET={client-secret-value}
   AUTH_PUBLIC_BASE_URL=https://your-domain.com
   ```

   Replace `{tenant-id}` with your Azure AD tenant ID.

### Auth0

1. **Create application** in Auth0 dashboard
   - Application type: Regular Web Application
   - Allowed Callback URLs: `https://your-domain.com/api/auth/callback/oidc`

2. **Environment variables**:
   ```bash
   OIDC_DISCOVERY_URL=https://your-domain.auth0.com/.well-known/openid-configuration
   OIDC_CLIENT_ID=your-auth0-client-id
   OIDC_CLIENT_SECRET=your-auth0-client-secret
   AUTH_PUBLIC_BASE_URL=https://your-domain.com
   ```

---

## Local Admin User Management

Local username/password authentication is **always available** as a fallback, even when OIDC is configured.

### Initial Admin User

On first startup, the agent creates an initial admin user if the `local_users` table is empty:

```bash
ADMIN_INITIAL_USERNAME=admin  # Default: "admin"
ADMIN_INITIAL_PASSWORD=<secure-password>
```

**Important**: The password is only used on first startup. To change it later, update the user in the database directly.

### User Management

Local users are stored in PostgreSQL (`local_users` table). User management is invite-only (no self-registration).

**Database schema**:
```sql
CREATE TABLE local_users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,  -- bcrypt
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT,  -- Email of admin who created this user
    last_login_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
```

To create additional users, insert directly into the database using bcrypt-hashed passwords (cost factor 12).

---

## Environment Variables Reference

### Required (All Deployments)

| Variable | Description | Example |
|----------|-------------|---------|
| `AUTH_SESSION_SECRET` | Secret key for signing session cookies (64+ chars) | Generated with `openssl rand -hex 32` |
| `ADMIN_INITIAL_USERNAME` | Initial admin username (default: "admin") | `admin` |
| `ADMIN_INITIAL_PASSWORD` | Initial admin password (required on first startup) | `<secure-password>` |

### OIDC Configuration (Optional)

| Variable | Description | Example |
|----------|-------------|---------|
| `OIDC_DISCOVERY_URL` | OIDC discovery endpoint URL | `https://accounts.google.com/.well-known/openid-configuration` |
| `OIDC_CLIENT_ID` | OAuth2 client ID | `xxx.apps.googleusercontent.com` |
| `OIDC_CLIENT_SECRET` | OAuth2 client secret | `GOCSPX-xxx` |
| `AUTH_PUBLIC_BASE_URL` | Public URL of your deployment (required for OIDC redirect) | `https://your-domain.com` |

### Optional Customization

| Variable | Description | Default |
|----------|-------------|---------|
| `OIDC_PROVIDER_NAME` | Display name for SSO provider | Auto-detected from discovery URL |
| `OIDC_PROVIDER_LOGO` | Logo URL for SSO button | Auto-detected based on provider |
| `AUTH_ALLOWED_DOMAINS` | Comma-separated list of allowed email domains for OIDC | None (all domains allowed) |
| `AUTH_SESSION_TTL_SECONDS` | Session expiration time in seconds | `43200` (12 hours) |
| `AUTH_COOKIE_SECURE` | Force secure cookies (HTTPS only) | Auto-detected from `AUTH_PUBLIC_BASE_URL` |

---

## Security Features

### Built-in Security Measures

1. **PKCE (Proof Key for Code Exchange)** - OIDC flow uses PKCE for additional security
2. **State/Nonce validation** - Prevents CSRF and replay attacks
3. **HttpOnly cookies** - Session cookies are HttpOnly, Secure, SameSite=lax
4. **Bcrypt password hashing** - Cost factor 12 (2^12 rounds)
5. **Rate limiting** - 5 failed login attempts per 5 minutes per username
6. **Constant-time comparison** - Password verification uses bcrypt's constant-time comparison
7. **Signed sessions** - Session cookies are cryptographically signed
8. **Domain enforcement** - Optional email domain restriction for OIDC

### What's NOT Included (Intentionally Simple)

- No password reset flow (admins can recreate users if needed)
- No 2FA (can be added later if needed)
- No email verification (invite-only, admin controls user creation)
- No self-registration (invite-only model)

---

## Kubernetes Deployment Example

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tarka-auth
type: Opaque
stringData:
  session-secret: "<generate-with-openssl-rand-hex-32>"
  admin-password: "<secure-initial-password>"
  oidc-client-secret: "<your-oidc-client-secret>"

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tarka-webhook
spec:
  template:
    spec:
      containers:
      - name: webhook
        image: your-registry/tarka:latest
        env:
        # Required
        - name: AUTH_SESSION_SECRET
          valueFrom:
            secretKeyRef:
              name: tarka-auth
              key: session-secret
        - name: ADMIN_INITIAL_USERNAME
          value: "admin"
        - name: ADMIN_INITIAL_PASSWORD
          valueFrom:
            secretKeyRef:
              name: tarka-auth
              key: admin-password

        # OIDC (optional)
        - name: OIDC_DISCOVERY_URL
          value: "https://accounts.google.com/.well-known/openid-configuration"
        - name: OIDC_CLIENT_ID
          value: "xxx.apps.googleusercontent.com"
        - name: OIDC_CLIENT_SECRET
          valueFrom:
            secretKeyRef:
              name: tarka-auth
              key: oidc-client-secret
        - name: AUTH_PUBLIC_BASE_URL
          value: "https://tarka.yourcompany.com"

        # Optional: Domain restriction
        - name: AUTH_ALLOWED_DOMAINS
          value: "yourcompany.com"
```

---

## Troubleshooting

### OIDC Login Fails

**Symptom**: Redirect to provider works, but callback fails

**Check**:
1. `AUTH_PUBLIC_BASE_URL` matches your actual deployment URL
2. Redirect URI in provider config matches `https://your-domain.com/api/auth/callback/oidc`
3. `OIDC_DISCOVERY_URL` is accessible from the pod
4. `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` are correct
5. Check provider logs for specific errors

### Local Login Fails

**Symptom**: "Invalid username or password" even with correct credentials

**Check**:
1. PostgreSQL is configured and migrations have run
2. Initial admin user was created (check logs: "Admin user initialization check completed")
3. Database connectivity is working
4. User is active (`is_active = TRUE` in database)

### Rate Limiting Triggered

**Symptom**: "Too many failed login attempts"

**Solution**: Wait 5 minutes, or reset the rate limiter by restarting the webhook pod (in-memory rate limiter)

### Session Cookie Not Set

**Symptom**: Login succeeds but immediately shows login page again

**Check**:
1. `AUTH_SESSION_SECRET` is set
2. `AUTH_COOKIE_SECURE` matches your deployment (HTTPS vs HTTP)
3. Browser is not blocking cookies
4. No reverse proxy is stripping cookies

---

## Migration from Old Google-Only Setup

If you're migrating from the old Google-specific authentication:

**Old environment variables** (deprecated):
```bash
CONSOLE_AUTH_MODE=oidc
GOOGLE_OAUTH_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxx
```

**New environment variables** (use these instead):
```bash
# No CONSOLE_AUTH_MODE needed - auto-detected!
OIDC_DISCOVERY_URL=https://accounts.google.com/.well-known/openid-configuration
OIDC_CLIENT_ID=xxx.apps.googleusercontent.com  # Same value
OIDC_CLIENT_SECRET=GOCSPX-xxx  # Same value

# New required variables
ADMIN_INITIAL_USERNAME=admin
ADMIN_INITIAL_PASSWORD=<secure-password>
```

**Breaking changes**:
- `CONSOLE_AUTH_MODE` environment variable removed (auth mode is auto-detected)
- `GOOGLE_OAUTH_CLIENT_ID` → `OIDC_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET` → `OIDC_CLIENT_SECRET`
- Added required `OIDC_DISCOVERY_URL`
- `disabled` mode removed - authentication is always required
- HTTP Basic Auth removed - use local username/password instead

---

## API Endpoints

The following auth endpoints are provided:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/login/oidc` | GET | Initiate OIDC login flow |
| `/api/auth/callback/oidc` | GET | OIDC callback handler |
| `/api/auth/login/local` | POST | Local username/password login |
| `/api/auth/logout` | POST | Clear session cookie |
| `/api/auth/me` | GET | Get current user info |
| `/api/auth/mode` | GET | Get authentication configuration |

**Example: Local login**
```bash
curl -X POST https://your-domain.com/api/auth/login/local \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-password"}' \
  --cookie-jar cookies.txt

# Use session cookie for subsequent requests
curl https://your-domain.com/api/v1/runs \
  --cookie cookies.txt
```

---

## AWS IAM Role Setup

For AWS infrastructure context (EC2, EBS, ELB, RDS health checks), the agent requires read-only AWS API access via IAM role.

### IAM Policy (Read-Only)

Create an IAM policy with the following permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstanceStatus",
        "ec2:DescribeInstances",
        "ec2:DescribeVolumeStatus",
        "ec2:DescribeVolumes",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeNatGateways",
        "ec2:DescribeVpcEndpoints",
        "elasticloadbalancing:DescribeLoadBalancers",
        "elasticloadbalancing:DescribeTargetHealth",
        "rds:DescribeDBInstances",
        "ecr:DescribeImages",
        "ecr:DescribeImageScanFindings",
        "ecr:GetRepositoryPolicy"
      ],
      "Resource": "*"
    }
  ]
}
```

**Note**: All permissions are `Describe*` or `Get*` (read-only). No mutation operations are granted.

### Attach IAM Policy to Pod Service Account

**EKS (IRSA - IAM Roles for Service Accounts)**:

1. Create IAM role with trust policy for your EKS cluster:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": {
           "Federated": "arn:aws:iam::<account-id>:oidc-provider/oidc.eks.<region>.amazonaws.com/id/<cluster-id>"
         },
         "Action": "sts:AssumeRoleWithWebIdentity",
         "Condition": {
           "StringEquals": {
             "oidc.eks.<region>.amazonaws.com/id/<cluster-id>:sub": "system:serviceaccount:tarka:tarka"
           }
         }
       }
     ]
   }
   ```

2. Attach the read-only IAM policy to this role

3. Annotate Kubernetes ServiceAccount:
   ```yaml
   apiVersion: v1
   kind: ServiceAccount
   metadata:
     name: tarka
     namespace: tarka
     annotations:
       eks.amazonaws.com/role-arn: arn:aws:iam::<account-id>:role/tarka-role
   ```

4. Reference ServiceAccount in Deployment:
   ```yaml
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: tarka
   spec:
     template:
       spec:
         serviceAccountName: tarka
         containers:
         - name: agent
           env:
           - name: AWS_REGION
             value: "us-east-1"
           - name: AWS_EVIDENCE_ENABLED
             value: "1"
           - name: CHAT_ALLOW_AWS_READ
             value: "1"
   ```

**GKE (Workload Identity)**:

1. Create IAM service account and bind to Kubernetes service account (follow GKE Workload Identity documentation)

2. Grant IAM permissions to the service account

3. Annotate Kubernetes ServiceAccount:
   ```yaml
   apiVersion: v1
   kind: ServiceAccount
   metadata:
     name: tarka
     namespace: tarka
     annotations:
       iam.gke.io/gcp-service-account: tarka@<project-id>.iam.gserviceaccount.com
   ```

**Self-managed Kubernetes**:

- Use EC2 instance profile on nodes (least secure, not recommended)
- Or deploy [kube2iam](https://github.com/jtblin/kube2iam) / [kiam](https://github.com/uswitch/kiam) for pod-level IAM roles

### Environment Variables

```bash
# Enable AWS evidence collection in investigation pipeline
AWS_EVIDENCE_ENABLED=1

# Enable AWS chat tools for interactive queries
CHAT_ALLOW_AWS_READ=1

# Optional: Restrict to specific regions
CHAT_AWS_REGION_ALLOWLIST=us-east-1,us-west-2

# Optional: Override default region
AWS_REGION=us-east-1
```

### Verify Setup

1. Deploy with IAM role attached
2. Trigger an alert for a pod running on an EC2 instance
3. Check the investigation report for the **"AWS"** section
4. Verify it shows EC2/EBS status

Example report section:
```markdown
### AWS

- **Region:** `us-east-1`

**EC2 Instances:**
- ✅ **i-abc123:** state=running, system=ok, instance=ok

**EBS Volumes:**
- ✅ **vol-xyz789:** status=ok, type=gp3, iops=3000
```

### Troubleshooting

**"AccessDenied" errors**:
- Verify IAM policy is attached to role
- Check trust policy allows your service account
- Ensure ServiceAccount annotation is correct

**No AWS section in reports**:
- Set `AWS_EVIDENCE_ENABLED=1`
- Verify alert has AWS metadata (instance ID, volume ID, etc.)
- Check agent logs for AWS provider errors

---

## GitHub App Authentication

For GitHub code change context (commits, workflow runs, docs), the agent requires a GitHub App for authentication.

See the detailed [GitHub App Setup Guide](github-app-setup.md) for step-by-step instructions.

**Quick summary**:

1. Create GitHub App in your organization settings
2. Set permissions: Contents (read), Actions (read), Metadata (read)
3. Install app on repositories
4. Generate private key
5. Configure environment variables:
   ```bash
   GITHUB_APP_ID=123456
   GITHUB_APP_INSTALLATION_ID=78901234
   GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n..."
   GITHUB_EVIDENCE_ENABLED=1
   CHAT_ALLOW_GITHUB_READ=1
   ```

**Why GitHub App over Personal Access Token?**
- Scoped to specific repositories
- Short-lived tokens (1h TTL, auto-refresh)
- Not tied to individual users (survives employee turnover)
- Better audit trail

---

## Security Best Practices

### Secrets Management

**Recommended**:
- Store `AUTH_SESSION_SECRET`, `OIDC_CLIENT_SECRET`, `ADMIN_INITIAL_PASSWORD`, `GITHUB_APP_PRIVATE_KEY` in Kubernetes Secrets
- Use sealed secrets, external secrets operator, or cloud-native secret managers (AWS Secrets Manager, GCP Secret Manager, Azure Key Vault)
- Rotate secrets periodically (90 days for admin passwords, GitHub private keys)

**Example with Kubernetes Secrets**:
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tarka-auth
  namespace: tarka
type: Opaque
stringData:
  session-secret: <random-64-char-string>
  oidc-client-secret: <oidc-secret>
  admin-password: <secure-password>
  github-private-key: |
    -----BEGIN RSA PRIVATE KEY-----
    ...
    -----END RSA PRIVATE KEY-----
```

```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
      - name: agent
        env:
        - name: AUTH_SESSION_SECRET
          valueFrom:
            secretKeyRef:
              name: tarka-auth
              key: session-secret
        - name: OIDC_CLIENT_SECRET
          valueFrom:
            secretKeyRef:
              name: tarka-auth
              key: oidc-client-secret
        - name: ADMIN_INITIAL_PASSWORD
          valueFrom:
            secretKeyRef:
              name: tarka-auth
              key: admin-password
        - name: GITHUB_APP_PRIVATE_KEY
          valueFrom:
            secretKeyRef:
              name: tarka-auth
              key: github-private-key
```

### Network Security

- Deploy behind an internal load balancer (not public internet)
- Use TLS/HTTPS (terminated at load balancer or ingress)
- Configure network policies to restrict pod egress/ingress
- Use private VPC endpoints for AWS API calls (reduce NAT costs, improve security)

### Access Control

- Enable OIDC for centralized identity management
- Use `AUTH_ALLOWED_DOMAINS` to restrict SSO to your organization
- Review RBAC permissions regularly
- Audit session cookies (short TTL, HTTP-only, secure flag)

### Monitoring

- Enable authentication audit logs (OIDC provider logs)
- Monitor failed login attempts
- Alert on suspicious activity (e.g., admin password changes)
- Track AWS/GitHub API usage (rate limits, cost)

---

## Reference: All Authentication Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AUTH_SESSION_SECRET` | Yes | - | Random 64-char string for signing session cookies |
| `ADMIN_INITIAL_USERNAME` | Yes | `admin` | Initial admin username |
| `ADMIN_INITIAL_PASSWORD` | Yes | - | Initial admin password (hashed on first startup) |
| `AUTH_PUBLIC_BASE_URL` | If using OIDC | - | Public URL of deployment (e.g., `https://tarka.yourcompany.com`) |
| `OIDC_DISCOVERY_URL` | If using OIDC | - | OIDC provider discovery URL |
| `OIDC_CLIENT_ID` | If using OIDC | - | OAuth2 client ID |
| `OIDC_CLIENT_SECRET` | If using OIDC | - | OAuth2 client secret |
| `AUTH_ALLOWED_DOMAINS` | No | - | Comma-separated list of allowed email domains for OIDC (e.g., `yourcompany.com`) |
| `AWS_REGION` | If using AWS | - | AWS region for API calls |
| `AWS_EVIDENCE_ENABLED` | No | `false` | Enable AWS evidence collection in pipeline |
| `CHAT_ALLOW_AWS_READ` | No | `false` | Enable AWS chat tools |
| `CHAT_AWS_REGION_ALLOWLIST` | No | - | Comma-separated list of allowed AWS regions |
| `GITHUB_APP_ID` | If using GitHub | - | GitHub App ID |
| `GITHUB_APP_INSTALLATION_ID` | If using GitHub | - | GitHub App installation ID |
| `GITHUB_APP_PRIVATE_KEY` | If using GitHub | - | GitHub App private key (PEM format) |
| `GITHUB_EVIDENCE_ENABLED` | No | `false` | Enable GitHub evidence collection in pipeline |
| `CHAT_ALLOW_GITHUB_READ` | No | `false` | Enable GitHub chat tools |
| `CHAT_GITHUB_REPO_ALLOWLIST` | No | - | Comma-separated list of allowed GitHub repos |
| `GITHUB_DEFAULT_ORG` | No | - | Default GitHub organization for repo discovery |
