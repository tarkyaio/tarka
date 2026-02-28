# Case Memory & Skills

**Status**: ✅ Implemented (enabled via environment variables)

The Tarka can learn from past incidents through case-based reasoning, storing investigations and extracting reusable skills.

## Overview

**Case memory** provides:
- **Case history**: Every investigation becomes a searchable record with artifacts in S3 and metadata in PostgreSQL
- **Similarity search**: Find past incidents matching current alert (family, cluster, namespace, workload)
- **Skill library**: Distilled runbooks that match context and inject suggestions
- **Chat integration**: Tool-using assistant can query past cases during investigation

## Architecture

```
Investigation → Caseize → PostgreSQL (metadata + embeddings)
                       → S3 (full investigation JSON)

During Investigation:
  Similar Cases → Hypothesis Confidence Boosting
  Skills → Suggested Actions
  Chat → Memory Retrieval Tool
```

## Core Components

### Case Storage (`agent/memory/case_index.py`)

**Case vs Run**:
- **Case**: Logical grouping spanning time (flaps/re-fires/multiple investigations for same issue)
- **Run**: One investigation execution (one `Investigation` snapshot + report)

**PostgreSQL schema**:
- `cases`: Case summary with optional closure fields (resolved timestamp, resolution notes)
- `investigation_runs`: Per-run snapshots with S3 keys + compact analysis JSON
- `skills`: Versioned skills (`draft`/`active`/`retired`) with predicates and templates
- `skill_feedback`: On-call feedback records (helpful/unhelpful ratings)

### Similarity Search (`agent/memory/case_retrieval.py`)

Finds similar past incidents using:
- Alert family match (exact)
- Cluster match (exact)
- Namespace or workload match (weighted)
- Temporal decay (recent cases ranked higher)
- Embeddings (optional: pgvector for semantic similarity)

**Usage in pipeline**:
```python
from agent.memory.case_retrieval import find_similar_cases

similar = find_similar_cases(
    family="pod_not_healthy",
    cluster="prod",
    namespace="payments",
    workload="checkout-api",
    limit=5
)
```

### Skills Extraction (`agent/memory/skills.py`)

**Skills** are distilled patterns extracted from resolved cases:
- **Predicate**: When does this skill apply? (JSON match on alert labels/evidence)
- **Template**: What to suggest? (Markdown template with placeholders)
- **Versioning**: Draft → Active → Retired lifecycle

**Example skill**:
```yaml
name: "restart-on-oom-with-low-limit"
when:
  family: "oom_killed"
  memory_limit_mb: {"lt": 512}
suggest: |
  Memory limit is very low ({memory_limit_mb}Mi). Consider:
  1. Increase to 1Gi: `kubectl set resources deployment/{workload} -c {container} --limits=memory=1Gi`
  2. Monitor for 1h to verify OOMKills stop
```

### Caseization (`agent/memory/caseize.py`)

Converts `Investigation` objects into storable cases:
- Extract metadata (fingerprint, family, target, labels)
- Compute embeddings for similarity search (optional)
- Write to PostgreSQL with S3 artifact links
- Group under existing case or create new case

### Chat Integration (`agent/chat/tools.py`)

Memory tools available in chat:
- `memory.similar_cases`: Find past incidents matching current investigation
- `memory.skills`: Search skill library for matching patterns

**Policy gate**: Enabled when `CHAT_ALLOW_MEMORY_READ=true`

## Configuration

### Environment Variables

```bash
# Enable case memory features
MEMORY_ENABLED=1

# PostgreSQL connection
POSTGRES_HOST=postgres.tarka.svc.cluster.local
POSTGRES_PORT=5432
POSTGRES_DB=tarka
POSTGRES_USER=tarka
POSTGRES_PASSWORD=<secret>
# Or use connection string:
# POSTGRES_DSN=postgresql://user:pass@host:5432/db

# Auto-migrate schema on startup (dev only)
DB_AUTO_MIGRATE=1  # Production: run migrations explicitly

# Optional: Vector embeddings for semantic search
# Requires pgvector extension
MEMORY_USE_EMBEDDINGS=1
```

### Database Setup

**Development** (auto-migrate):
```bash
export DB_AUTO_MIGRATE=1
python main.py --serve-webhook  # Auto-migrates on startup
```

**Production** (explicit migration):
```bash
# Run migrations before deployment
python -m agent.memory.migrate

# Then deploy with auto-migrate disabled
export DB_AUTO_MIGRATE=0
kubectl apply -f k8s/deployment.yaml
```

**Migrations** are located in `agent/memory/migrations/`:
- Versioned SQL files (e.g., `001_init.sql`, `002_add_skills.sql`)
- Tracked in `schema_migrations` table
- Applied with Postgres advisory lock (safe for concurrent deployments)

### PostgreSQL with pgvector

For semantic similarity search using embeddings:

```yaml
# k8s/postgres-pgvector.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: tarka
spec:
  serviceName: postgres
  replicas: 1
  template:
    spec:
      containers:
      - name: postgres
        image: pgvector/pgvector:pg16
        env:
        - name: POSTGRES_DB
          value: tarka
        - name: POSTGRES_USER
          value: tarka
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: postgres-secret
              key: password
        volumeMounts:
        - name: data
          mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 100Gi
```

## Usage

### In Pipeline

Case memory integrates automatically when `MEMORY_ENABLED=1`:

1. **Before diagnostics**: Retrieve similar cases to boost hypothesis confidence
2. **During verdict**: Check if similar cases had specific discriminators
3. **In report**: Add "## Memory" section with similar cases and matched skills
4. **After report**: Caseize and store current investigation for future use

### In Chat

Memory tools are available in the Console UI chat:

**Find similar cases**:
```
> Show me similar incidents to this one

[Agent searches case database and returns past cases with:
 - Alert family and labels
 - Resolution notes
 - Time to resolution
 - Links to original reports]
```

**Search skills**:
```
> Are there any known skills for OOMKilled with low memory limits?

[Agent queries skill library and returns matching skills with suggested actions]
```

## Memory Section in Reports

When `MEMORY_ENABLED=1`, reports include:

```markdown
## Memory

### Similar Cases (3 found)

1. **2025-01-15 14:23 UTC** - `pod_not_healthy` in `prod/payments/checkout-api`
   - Discriminator: CrashLoopBackOff (OOMKilled)
   - Resolution: Increased memory limit to 2Gi
   - Time to resolution: 12 minutes
   - [View report](s3://bucket/investigations/2025-01-15/abc123-pod_not_healthy.json)

2. **2025-01-10 09:45 UTC** - `pod_not_healthy` in `prod/payments/checkout-api`
   - Discriminator: ImagePullBackOff
   - Resolution: Fixed image tag in deployment
   - Time to resolution: 8 minutes

### Matched Skills (1)

**restart-on-oom-with-low-limit** (active)
- Applies when: OOMKilled + memory limit < 512Mi
- Suggests: Increase memory limit to 1Gi, monitor for 1h
```

## Skill Feedback

On-call engineers can provide feedback on skill suggestions:

**Via API** (Console UI):
```bash
POST /api/v1/feedback/skills
{
  "run_id": "abc123",
  "skill_name": "restart-on-oom-with-low-limit",
  "helpful": true,
  "notes": "Worked perfectly, OOMKills stopped after limit increase"
}
```

**Via CLI**:
```bash
python main.py --feedback-skill \
  --run-id abc123 \
  --skill restart-on-oom-with-low-limit \
  --helpful true \
  --notes "Worked perfectly"
```

Feedback is stored in PostgreSQL and can be used to:
- Retire unhelpful skills
- Promote frequently helpful skills
- Train future skill extraction models

## Data Retention

**S3 artifacts**:
- Configure lifecycle policies on S3 bucket (e.g., 90-day retention)
- Reports are self-contained JSON/Markdown files

**PostgreSQL**:
- Case metadata retained indefinitely by default
- Configure TTL policies for old cases if needed
- Recommended: Archive to cold storage after 1 year

## Implementation Files

- [`agent/memory/caseize.py`](../../agent/memory/caseize.py): Investigation → Case conversion
- [`agent/memory/case_index.py`](../../agent/memory/case_index.py): PostgreSQL storage
- [`agent/memory/case_retrieval.py`](../../agent/memory/case_retrieval.py): Similarity search
- [`agent/memory/skills.py`](../../agent/memory/skills.py): Skill matching and extraction
- [`agent/memory/migrate.py`](../../agent/memory/migrate.py): Database migrations
- [`agent/memory/migrations/`](../../agent/memory/migrations/): SQL migration files
- [`agent/chat/tools.py`](../../agent/chat/tools.py): Memory tools for chat (`memory.similar_cases`, `memory.skills`)

## Future Enhancements

- **Skill governance**: Ownership, review/approval, staged rollout
- **Evaluation harness**: KPIs and replay tests on stored investigations
- **Action execution**: Move from suggest-only to approved action execution
- **Advanced grouping**: Better case grouping under label churn/flapping
- **Retention policies**: Automated archival and deletion workflows
