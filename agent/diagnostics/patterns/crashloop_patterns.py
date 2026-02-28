"""Crashloop-specific error patterns (part of extensible pattern library).

These patterns detect common CrashLoopBackOff failure modes from parsed log errors.
Covers: dependency connectivity, missing config, port conflicts, application OOM,
permission issues, and database connection failures.
"""

from agent.diagnostics.log_pattern_matcher import LogPattern

# Dependency connection failure (DNS / connection refused)
CRASHLOOP_DEPENDENCY_CONNECTION = LogPattern(
    pattern_id="crashloop_dependency_connection",
    title="Dependency connection failure (connection refused / DNS)",
    patterns=[
        r"Connection refused",
        r"ECONNREFUSED",
        r"dial tcp.*connection refused",
        r"no such host",
        r"Name or service not known",
        r"getaddrinfo ENOTFOUND",
        r"Could not resolve host",
    ],
    confidence=85,
    why_template="Application cannot connect to dependency '{host}' (connection refused or DNS failure)",
    next_tests=[
        "Check if the dependency service is running:",
        "kubectl -n {namespace} get svc | grep -i {host}",
        "",
        "Test DNS resolution from within the cluster:",
        "kubectl -n {namespace} run -it --rm dns-test --image=busybox -- nslookup {host}",
        "",
        "Check network policies that might block egress:",
        "kubectl -n {namespace} get networkpolicy -o yaml",
    ],
    context_extractors={
        "host": r"(?:dial tcp |connect to |connecting to |resolve host |ENOTFOUND )([^\s:]+)",
    },
)

# Missing configuration file or environment variable
CRASHLOOP_CONFIG_MISSING = LogPattern(
    pattern_id="crashloop_config_missing",
    title="Missing configuration file or environment variable",
    patterns=[
        r"FileNotFoundError",
        r"No such file or directory.*\.(?:yaml|yml|json|env|conf|properties|toml|ini|cfg)",
        r"missing required.*config",
        r"ENOENT.*config",
        r"required key.*not set",
        r"required environment variable.*not set",
        r"KeyError:.*[A-Z_]{3,}",
    ],
    confidence=80,
    why_template="Application fails to start due to missing configuration file or environment variable",
    next_tests=[
        "Check ConfigMap and Secret mounts on the pod:",
        "kubectl -n {namespace} describe pod {pod} | grep -A5 -i 'volumes\\|mounts'",
        "",
        "List ConfigMaps and Secrets in the namespace:",
        "kubectl -n {namespace} get cm,secret",
        "",
        "Check if referenced ConfigMap/Secret keys exist:",
        "kubectl -n {namespace} get cm -o yaml | grep -i 'data:'",
    ],
    context_extractors={},
)

# Port bind failure (address already in use)
CRASHLOOP_PORT_BIND_FAILURE = LogPattern(
    pattern_id="crashloop_port_bind_failure",
    title="Port bind failure (address already in use)",
    patterns=[
        r"bind.*address already in use",
        r"EADDRINUSE",
        r"listen tcp.*bind",
        r"port.*already.*in use",
    ],
    confidence=90,
    why_template="Application cannot bind to port (address already in use)",
    next_tests=[
        "Check containerPort spec in the pod definition:",
        "kubectl -n {namespace} get pod {pod} -o jsonpath='{{.spec.containers[*].ports}}'",
        "",
        "Check if another container in the same pod uses the same port:",
        "kubectl -n {namespace} describe pod {pod} | grep -i port",
        "",
        "Verify hostPort is not conflicting with other pods on the same node:",
        "kubectl -n {namespace} get pod {pod} -o wide",
    ],
    context_extractors={},
)

# Application-level OOM (heap exhaustion before K8s OOMKill)
CRASHLOOP_OOM_APPLICATION = LogPattern(
    pattern_id="crashloop_oom_application",
    title="Application out of memory (heap exhaustion)",
    patterns=[
        r"OutOfMemoryError",
        r"JavaScript heap out of memory",
        r"Cannot allocate memory",
        r"ENOMEM",
        r"runtime: out of memory",
        r"MemoryError",
        r"std::bad_alloc",
    ],
    confidence=85,
    why_template="Application running out of memory (heap exhaustion before OOMKill)",
    next_tests=[
        "Check memory limits and requests for the container:",
        "kubectl -n {namespace} get pod {pod} -o jsonpath='{{.spec.containers[*].resources}}'",
        "",
        "Check memory usage over time:",
        'quantile_over_time(0.95, container_memory_working_set_bytes{{namespace="{namespace}",pod="{pod}",container!="POD",image!=""}}[30m])',
        "",
        "For JVM apps, check -Xmx setting; for Node.js, check --max-old-space-size",
    ],
    context_extractors={},
)

# Permission denied / filesystem access
CRASHLOOP_PERMISSION_DENIED = LogPattern(
    pattern_id="crashloop_permission_denied",
    title="Permission denied (filesystem or security)",
    patterns=[
        r"Permission denied",
        r"EACCES",
        r"Operation not permitted",
        r"read-only file system",
    ],
    confidence=80,
    why_template="Application lacks filesystem or security permissions",
    next_tests=[
        "Check securityContext and volume mounts:",
        "kubectl -n {namespace} get pod {pod} -o jsonpath='{{.spec.containers[*].securityContext}}'",
        "",
        "Check if volumes are mounted read-only:",
        "kubectl -n {namespace} describe pod {pod} | grep -A3 -i 'mount'",
        "",
        "Check fsGroup and runAsUser settings:",
        "kubectl -n {namespace} get pod {pod} -o jsonpath='{{.spec.securityContext}}'",
    ],
    context_extractors={},
)

# Database connection failure
CRASHLOOP_DATABASE_CONNECTION = LogPattern(
    pattern_id="crashloop_database_connection",
    title="Database connection failure",
    patterns=[
        r"could not connect to server.*PostgreSQL",
        r"Access denied for user.*MySQL",
        r"Cannot connect to Redis",
        r"MongoNetworkError",
        r"ETIMEDOUT.*:(?:5432|3306|6379|27017)",
        r"OperationalError.*(?:could not connect|Connection refused)",
        r"FATAL:.*password authentication failed",
        r"no pg_hba\.conf entry",
    ],
    confidence=80,
    why_template="Application cannot connect to database '{db_type}'",
    next_tests=[
        "Check if the database service is reachable from the pod's namespace:",
        "kubectl -n {namespace} get svc | grep -iE 'postgres|mysql|redis|mongo'",
        "",
        "Verify database credentials secret exists and is mounted:",
        "kubectl -n {namespace} get secret | grep -iE 'db|database|postgres|mysql|redis|mongo'",
        "",
        "Test connectivity to the database port:",
        "kubectl -n {namespace} run -it --rm db-test --image=busybox -- nc -zv <db-host> <db-port>",
    ],
    context_extractors={
        "db_type": r"(PostgreSQL|MySQL|Redis|MongoDB|Mongo)",
    },
)


# Export all crashloop patterns
CRASHLOOP_PATTERNS = [
    CRASHLOOP_DEPENDENCY_CONNECTION,
    CRASHLOOP_CONFIG_MISSING,
    CRASHLOOP_PORT_BIND_FAILURE,
    CRASHLOOP_OOM_APPLICATION,
    CRASHLOOP_PERMISSION_DENIED,
    CRASHLOOP_DATABASE_CONNECTION,
]
