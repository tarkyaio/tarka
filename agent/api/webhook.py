"""
Alertmanager webhook server.

Receives Alertmanager webhook notifications in-cluster and writes one investigation
report per (identity + family + 4h bucket) to S3 with HEAD-before-PUT dedupe.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from agent.core.dedup import compute_dedup_key, compute_rollout_workload_key, utcnow
from agent.core.search_query import parse_search_query
from agent.pipeline.pipeline import run_investigation
from agent.providers.alertmanager_provider import extract_pod_info_from_alert
from agent.queue.base import AlertJob
from agent.report import render_report
from agent.storage.s3_store import S3Storage

logger = logging.getLogger(__name__)

_storage_cache: Dict[Tuple[str, str], S3Storage] = {}
_storage_lock = threading.Lock()


def _aj_load(analysis_json: Any) -> Dict[str, Any]:
    if analysis_json is None:
        return {}
    if isinstance(analysis_json, dict):
        return analysis_json
    if isinstance(analysis_json, str):
        try:
            v = json.loads(analysis_json)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


def _aj_get_str(analysis_json: Any, path: List[str]) -> Optional[str]:
    cur: Any = _aj_load(analysis_json)
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    if cur is None:
        return None
    s = str(cur).strip()
    return s or None


def _aj_get_int(analysis_json: Any, path: List[str]) -> Optional[int]:
    s = _aj_get_str(analysis_json, path)
    if s is None:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _run_view_fields_from_analysis_json(analysis_json: Any) -> Dict[str, Any]:
    """
    Single source of truth (strict): all shared UI fields come from analysis_json only.
    Missing fields must remain null (UI shows '—').
    """
    return {
        "severity": _aj_get_str(analysis_json, ["analysis", "verdict", "severity"]),
        "classification": _aj_get_str(analysis_json, ["analysis", "verdict", "classification"]),
        "primary_driver": _aj_get_str(analysis_json, ["analysis", "verdict", "primary_driver"]),
        "one_liner": _aj_get_str(analysis_json, ["analysis", "verdict", "one_liner"]),
        "impact_score": _aj_get_int(analysis_json, ["analysis", "scores", "impact_score"]),
        "confidence_score": _aj_get_int(analysis_json, ["analysis", "scores", "confidence_score"]),
        "noise_score": _aj_get_int(analysis_json, ["analysis", "scores", "noise_score"]),
        "team": _aj_get_str(analysis_json, ["target", "team"]),
        "family": _aj_get_str(analysis_json, ["analysis", "features", "family"]),
    }


def _coalesce_severity(_raw_severity: Optional[str], analysis_json: Any) -> Optional[str]:
    # Back-compat shim: use strict SSoT extraction (no legacy fallback).
    return _aj_get_str(analysis_json, ["analysis", "verdict", "severity"])


def _get_storage(bucket: str, prefix: str) -> S3Storage:
    """
    Return a cached S3Storage instance.

    Webhook requests can be frequent; constructing boto3 clients / sessions repeatedly is
    wasteful. We key the cache by (bucket, normalized_prefix).
    """
    norm_prefix = (prefix or "").strip("/")
    key = (bucket, norm_prefix)

    cached = _storage_cache.get(key)
    if cached is not None:
        return cached

    with _storage_lock:
        cached = _storage_cache.get(key)
        if cached is not None:
            return cached
        storage = S3Storage(bucket=bucket, prefix=norm_prefix)
        _storage_cache[key] = storage
        return storage


def _sanitize_path_component(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def _get_allowlist() -> Optional[List[str]]:
    raw = os.getenv("ALERTNAME_ALLOWLIST", "").strip()
    if not raw:
        return None
    return [x.strip() for x in raw.split(",") if x.strip()]


def _fallback_fingerprint(labels: Dict[str, Any]) -> str:
    """
    Compute a stable fingerprint when webhook payload doesn't include one.

    This is NOT Alertmanager's fingerprint, but is stable across identical labelsets.
    """
    # Ensure stable ordering.
    payload = json.dumps(labels or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_webhook_alert(webhook_alert: Dict[str, Any], parent_status: Optional[str]) -> Dict[str, Any]:
    """
    Convert Alertmanager webhook alert object into our internal alert shape.

    Alertmanager webhook format:
    - Individual alerts don't have a "status" field
    - Status is determined by presence of "endsAt" (resolved) vs only "startsAt" (firing)
    - Top-level notification has a "status" field that can be used as fallback
    - For firing alerts: startsAt is set, endsAt is missing/null/empty
    - For resolved alerts: both startsAt and endsAt are set with valid timestamps
    """
    labels = webhook_alert.get("labels", {}) or {}
    annotations = webhook_alert.get("annotations", {}) or {}

    starts_at = webhook_alert.get("startsAt") or webhook_alert.get("starts_at")
    ends_at = webhook_alert.get("endsAt") or webhook_alert.get("ends_at")

    def _is_nonempty_ends_at(v: Any) -> bool:
        """
        Alertmanager can include an `endsAt` field even for firing alerts, sometimes as a
        "zero time" placeholder like 0001-01-01T00:00:00Z. Treat those as empty.
        """
        if v is None:
            return False
        try:
            s = str(v).strip()
        except Exception:
            return False
        if not s:
            return False
        # Common Alertmanager placeholder for "not ended".
        if s.startswith("0001-01-01"):
            return False
        return True

    # Determine status (per-alert fields win; parent status is fallback only).
    # 1. If endsAt is a real timestamp (not empty/placeholder), alert is resolved
    # 2. Else if startsAt exists, alert is firing
    # 3. Else fall back to parent_status if provided
    # 4. Else fall back to explicit status field if present, otherwise "unknown"
    if _is_nonempty_ends_at(ends_at):
        status = "resolved"
    elif starts_at:
        status = "firing"
    elif parent_status in ("firing", "resolved"):
        status = parent_status
    else:
        status = webhook_alert.get("status") or "unknown"

    fingerprint = webhook_alert.get("fingerprint") or _fallback_fingerprint(labels)

    return {
        "fingerprint": fingerprint,
        "labels": labels,
        "annotations": annotations,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "generator_url": webhook_alert.get("generatorURL") or webhook_alert.get("generator_url", ""),
        "status": {"state": status},
    }


@dataclass
class WebhookStats:
    received: int = 0
    processed_firing: int = 0
    skipped_resolved: int = 0
    skipped_allowlist: int = 0
    skipped_no_pod_target: int = 0
    skipped_already_exists: int = 0
    stored_new: int = 0
    errors: int = 0


def process_alerts(
    alerts: List[Dict[str, Any]],
    *,
    time_window: str,
    storage: S3Storage,
    allowlist: Optional[List[str]],
    parent_status: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[WebhookStats, List[str]]:
    """
    Core processing logic for a webhook notification.

    Returns (stats, created_keys).
    """
    stats = WebhookStats(received=len(alerts))
    created: List[str] = []
    seen_keys: set[str] = set()
    now_utc = now or utcnow()
    env_cluster = (os.getenv("CLUSTER_NAME") or "").strip() or None
    refresh_seconds = 60 * 60  # 1h refresh gate for rollout-noisy alerts (workload-level)

    for raw in alerts:
        try:
            logger.debug(
                "Processing raw alert: keys=%s, labels=%s",
                list(raw.keys()) if isinstance(raw, dict) else "not a dict",
                raw.get("labels", {}).keys() if isinstance(raw, dict) else "N/A",
            )
            logger.debug(
                "Raw alert startsAt=%s, endsAt=%s, parent_status=%s",
                raw.get("startsAt"),
                raw.get("endsAt"),
                parent_status,
            )
            alert = _normalize_webhook_alert(raw, parent_status=parent_status)
            labels = alert.get("labels", {}) or {}
            alertname = labels.get("alertname") or "Unknown"

            # Ignore resolved alerts (process only firing).
            state = (alert.get("status", {}) or {}).get("state") or "unknown"
            if state != "firing":
                logger.info(
                    "Skipping resolved alert: %s (state=%s, startsAt=%s, endsAt=%s, parent_status=%s)",
                    alertname,
                    state,
                    alert.get("starts_at"),
                    alert.get("ends_at"),
                    parent_status,
                )
                stats.skipped_resolved += 1
                continue
            stats.processed_firing += 1
            logger.info("Processing firing alert: %s", alertname)

            if allowlist is not None and str(alertname) not in allowlist:
                logger.info("Skipping alert %s: not in allowlist", alertname)
                stats.skipped_allowlist += 1
                continue

            fingerprint = alert.get("fingerprint") or ""
            # Rollout-noisy alerts: derive workload identity (Deployment/StatefulSet) via K8s ownerReferences
            # and apply a freshness gate (1h) before doing any heavy work.
            rollout_key: Optional[str] = None
            rollout_rel_key: Optional[str] = None
            if str(alertname) in ("KubernetesPodNotHealthy", "KubernetesContainerOomKiller"):
                pod_target = extract_pod_info_from_alert(alert)
                if pod_target and pod_target.get("pod") and pod_target.get("namespace"):
                    try:
                        from agent.providers.k8s_provider import get_pod_owner_chain

                        oc = get_pod_owner_chain(str(pod_target["pod"]), str(pod_target["namespace"]))
                        rollout_key = compute_rollout_workload_key(
                            alertname=str(alertname),
                            labels=labels if isinstance(labels, dict) else {},
                            owner_chain=oc if isinstance(oc, dict) else {},
                            env_cluster=env_cluster,
                            include_container=(str(alertname) == "KubernetesContainerOomKiller"),
                        )
                        if rollout_key:
                            rollout_rel_key = f"{_sanitize_path_component(str(alertname))}/{rollout_key}.md"
                    except Exception:
                        rollout_key = None
                        rollout_rel_key = None

            # If we have a rollout-stable key, use it for in-payload dedupe + S3 freshness gating.
            if rollout_key and rollout_rel_key:
                if rollout_key in seen_keys:
                    stats.skipped_already_exists += 1
                    continue
                seen_keys.add(rollout_key)
                try:
                    exists, last_modified = storage.head_metadata(rollout_rel_key)
                    if exists and last_modified is not None:
                        age_s = (now_utc - last_modified).total_seconds()
                        if age_s < refresh_seconds:
                            logger.info(
                                "Skipping rollout-noisy alert %s: report is fresh (<%ss) key=%s age=%.0fs",
                                alertname,
                                refresh_seconds,
                                rollout_rel_key,
                                age_s,
                            )
                            stats.skipped_already_exists += 1
                            continue
                except Exception:
                    # If we can't determine freshness, fall back to running (conservative).
                    pass
            else:
                dedup = compute_dedup_key(
                    alertname=str(alertname),
                    labels=labels if isinstance(labels, dict) else {},
                    fingerprint=str(fingerprint),
                    now=now_utc,
                    env_cluster=env_cluster,
                    bucket_hours=4,
                )
                if dedup in seen_keys:
                    # De-dupe within a single notification payload.
                    logger.debug("Skipping duplicate alert in same payload: %s (dedup=%s)", alertname, str(dedup)[:8])
                    stats.skipped_already_exists += 1
                    continue
                seen_keys.add(dedup)

            # Best-effort target extraction (pod-scoped alerts). Do NOT skip if missing;
            # base triage + scenarios should still produce a useful report for non-pod alerts.
            pod_target = extract_pod_info_from_alert(alert)
            if not pod_target:
                logger.info(
                    "Proceeding without pod target for alert %s (labels=%s); generating base triage report.",
                    alertname,
                    list(labels.keys()),
                )

            rel_key = rollout_rel_key or f"{_sanitize_path_component(str(alertname))}/{dedup}.md"

            # S3 idempotency:
            # - Default behavior: skip if the object already exists (HEAD-before-PUT).
            # - Rollout-noisy refresh behavior: we already applied a 1h freshness gate above, so if we got here
            #   we intentionally allow overwriting the existing object.
            if not rollout_rel_key:
                if storage.exists(rel_key):
                    logger.info("Skipping alert %s: report already exists in S3 (key=%s)", alertname, rel_key)
                    stats.skipped_already_exists += 1
                    continue

            # Investigate and write report.
            # Special case: KubeJobFailed alerts have incorrect pod label (points to kube-state-metrics scraper)
            # Instead, log the actual Job name from job_name label.
            if str(alertname) == "KubeJobFailed":
                alert_labels = alert.get("labels", {}) if isinstance(alert, dict) else {}
                job_name = alert_labels.get("job_name")
                if job_name:
                    logger.info("Investigating alert %s for Job %s", alertname, job_name)
                else:
                    logger.info("Investigating alert %s (no job_name in labels)", alertname)
            elif pod_target:
                logger.info(
                    "Investigating alert %s for pod %s/%s", alertname, pod_target["namespace"], pod_target["pod"]
                )
            else:
                logger.info("Investigating alert %s (no pod identity in labels)", alertname)

            # Optional LangSmith tracing for alert processing.
            #
            # Note: the `/alerts` endpoint below is enqueue-only; traces will appear when a worker
            # consumes jobs and calls this function.
            callbacks = None
            trace_cfg: Dict[str, Any] = {}
            try:
                from agent.graphs.tracing import build_invoke_config

                meta = {
                    "alertname": str(alertname),
                    "fingerprint": str(fingerprint)[:12],
                    "time_window": str(time_window),
                    "cluster": str(env_cluster or ""),
                }
                trace_cfg = build_invoke_config(
                    kind="alert",
                    run_name=f"alert:{alertname}:{str(fingerprint)[:8] or 'nofp'}",
                    metadata=meta,
                )
                callbacks = trace_cfg.get("callbacks")
            except Exception:
                callbacks = None
                trace_cfg = {}

            def _invoke_traced_step(step_name: str, fn):
                if not callbacks:
                    return fn()
                try:
                    # Skip noisy infrastructure steps from LangSmith traces.
                    from agent.graphs.tracing import should_trace_run_name

                    if not should_trace_run_name(str(step_name)):
                        return fn()
                except Exception:
                    # Never break the webhook on tracing controls.
                    pass
                try:
                    from langchain_core.runnables import RunnableLambda  # type: ignore[import-not-found]

                    cfg = {"callbacks": callbacks, "run_name": str(step_name)}
                    return RunnableLambda(lambda _x: fn()).invoke({}, config=cfg)
                except Exception:
                    return fn()

            # Create one top-level run per alert (when tracing enabled).
            def _process_one_alert():
                # Run investigation-first pipeline (non-breaking wrapper around existing playbooks today).
                inv = _invoke_traced_step(
                    "run_investigation", lambda: run_investigation(alert=alert, time_window=time_window)
                )
                # LangGraph-based RCA loop (tool-using, evidence-grounded).
                from agent.graphs.rca import maybe_attach_rca

                _invoke_traced_step(
                    "rca_graph",
                    lambda: maybe_attach_rca(
                        alert=alert, time_window=time_window, investigation=inv, parent_callbacks=callbacks
                    ),
                )
                md = _invoke_traced_step("render_report", lambda: render_report(inv))
                _invoke_traced_step("s3_put_report_md", lambda: storage.put_markdown(rel_key, md))
                return inv, md

            if callbacks:
                try:
                    from langchain_core.runnables import RunnableLambda  # type: ignore[import-not-found]

                    investigation, report_md = RunnableLambda(lambda _x: _process_one_alert()).invoke(
                        {}, config=trace_cfg
                    )
                except Exception:
                    investigation, report_md = _process_one_alert()
            else:
                investigation, report_md = _process_one_alert()

            # Store a JSON evidence investigation alongside the report.
            try:
                rel_json = rel_key.replace(".md", ".json")
                _invoke_traced_step(
                    "s3_put_investigation_json",
                    lambda: storage.put_json(rel_json, investigation.model_dump(mode="json")),
                )
            except Exception:
                # Never fail the webhook on investigation serialization/storage.
                pass
            # Best-effort: index this run into Postgres for memory/search.
            try:
                from agent.memory.case_index import index_investigation_run

                ok, msg, res = _invoke_traced_step(
                    "postgres_index_run",
                    lambda: index_investigation_run(
                        investigation=investigation,
                        s3_report_key=storage.key(rel_key),
                        s3_investigation_key=storage.key(rel_key.replace(".md", ".json")),
                        report_text=report_md,
                    ),
                )
                if ok and res is not None:
                    logger.info(
                        "Indexed run in Postgres: case_id=%s run_id=%s match_reason=%s",
                        res.case_id,
                        res.run_id,
                        res.case_match_reason,
                    )
                elif not ok:
                    logger.info("Postgres indexing skipped: %s", msg)
            except Exception as e:
                # Never fail the webhook on DB indexing.
                logger.warning("Postgres indexing failed (non-fatal): %s", str(e))
            logger.info("Successfully stored report for alert %s to S3: %s", alertname, storage.key(rel_key))

            stats.stored_new += 1
            created.append(storage.key(rel_key))
        except Exception:
            stats.errors += 1
            logger.exception("Error processing webhook alert: %s", raw.get("labels", {}).get("alertname", "Unknown"))
            continue

    return stats, created


app = FastAPI(title="Tarka Alertmanager webhook")


# ---- Console authentication (public UI hardening) ----
_OAUTH_COOKIE_PATH = "/api/auth"
_OAUTH_TTL_SECONDS = 10 * 60


def _oauth_cookie_kwargs(cfg, *, key: str, value: str, max_age: int) -> dict:
    return {
        "key": key,
        "value": value,
        "max_age": max_age,
        "httponly": True,
        "secure": bool(getattr(cfg, "cookie_secure", False)),
        "samesite": "lax",
        "path": _OAUTH_COOKIE_PATH,
    }


def _oauth_cookie_clear_kwargs(cfg, *, key: str) -> dict:
    return _oauth_cookie_kwargs(cfg, key=key, value="", max_age=0)


def _public_base_url(cfg) -> str:
    base = (getattr(cfg, "public_base_url", None) or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=500, detail="AUTH_PUBLIC_BASE_URL is required for OIDC")
    return base


def _is_public_path(path: str) -> bool:
    # Health checks + webhook receiver must remain callable without Console auth.
    if path in ("/healthz", "/alerts"):
        return True
    # Auth login/callback endpoints must be reachable without a session.
    if path.startswith("/api/auth/login/") or path.startswith("/api/auth/callback/"):
        return True
    # Allow logout even if the cookie is already missing/invalid.
    if path == "/api/auth/logout":
        return True
    # Mode discovery is used by the UI to conditionally render auth options.
    if path == "/api/auth/mode":
        return True
    return False


class FeedbackRequest(BaseModel):
    case_id: Optional[str] = None
    run_id: Optional[str] = None
    skill_id: Optional[str] = None
    outcome: str
    notes: Optional[str] = None
    actor: Optional[str] = None


@app.on_event("startup")
def _startup_maybe_migrate_db() -> None:
    """
    Optional dev behavior: auto-apply DB migrations when DB_AUTO_MIGRATE=1.

    This should never prevent the webhook server from starting; failures are logged.
    """
    try:
        from agent.memory.migrate import maybe_auto_migrate

        did_attempt, msg = maybe_auto_migrate()
        if did_attempt:
            logger.info("DB migrations: %s", msg)
    except Exception as e:
        logger.warning("DB migrations: startup auto-migrate failed: %s", str(e))

    # Always log memory/db config at startup (helps debug why indexing is/no-op).
    try:
        from agent.memory.config import load_memory_config

        cfg = load_memory_config()
        # Avoid logging secrets; host/db/user are fine.
        logger.info(
            "Memory config: enabled=%s db_auto_migrate=%s postgres_host=%s postgres_db=%s postgres_user=%s",
            cfg.memory_enabled,
            cfg.db_auto_migrate,
            cfg.postgres_host,
            cfg.postgres_db,
            cfg.postgres_user,
        )
    except Exception as e:
        logger.info("Memory config: unavailable (%s)", str(e))


@app.on_event("startup")
def _startup_initialize_admin_user() -> None:
    """
    Initialize admin user on first startup if configured.
    This should never prevent the webhook server from starting; failures are logged.
    """
    try:
        from agent.auth.config import load_auth_config
        from agent.auth.local import initialize_admin_user

        cfg = load_auth_config()
        if cfg.admin_initial_username and cfg.admin_initial_password:
            conn = _get_db_connection()
            if conn:
                try:
                    initialize_admin_user(conn, cfg.admin_initial_username, cfg.admin_initial_password)
                    logger.info("Admin user initialization check completed")
                finally:
                    conn.close()
            else:
                logger.warning("Cannot initialize admin user: database not configured")
    except Exception as e:
        logger.warning("Admin user initialization failed: %s", str(e))


@app.on_event("startup")
async def _startup_jetstream_warmup() -> None:
    """
    Enqueue-only receiver: fail fast if JetStream is unreachable.
    """
    from agent.queue.nats_jetstream import get_client_from_env

    client = await get_client_from_env()
    await client.warmup()
    logger.info("JetStream warmup OK (enqueue-only mode)")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming HTTP requests."""
    start_time = time.time()
    logger.debug("%s %s", request.method, request.url.path)
    try:
        path = request.url.path or ""

        # Fast path: public endpoints should not require (or even import) auth dependencies.
        # This also keeps unit tests lightweight when auth extras are not installed.
        if request.method == "OPTIONS" or _is_public_path(path):
            response = await call_next(request)
            process_time = time.time() - start_time
            logger.debug("%s %s - %d (%.3fs)", request.method, request.url.path, response.status_code, process_time)
            return response

        # Console auth enforcement (public UI hardening).
        # Fail closed: anything not explicitly public requires auth.
        from agent.auth.deps import authenticate_request

        # Best-effort: attach user identity for handlers that want it.
        user = authenticate_request(request)
        if user is not None:
            request.state.user = user

        # Always require authentication (no "disabled" mode).
        if user is None:
            # IMPORTANT: do not emit `WWW-Authenticate` because browsers will show a
            # username/password modal that conflicts with the in-app login UI.
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        response = await call_next(request)
        process_time = time.time() - start_time
        logger.debug("%s %s - %d (%.3fs)", request.method, request.url.path, response.status_code, process_time)
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.exception("%s %s - ERROR after %.3fs: %s", request.method, request.url.path, process_time, str(e))
        raise


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/api/auth/login/oidc")
async def auth_login_oidc(next_path: str = Query("/", alias="next")):
    """Initiate OIDC login flow (generic, works with any OIDC provider)."""
    from agent.auth.config import load_auth_config
    from agent.auth.oidc import build_authorize_url, pkce_challenge
    from agent.auth.util import random_token, sanitize_next_path

    cfg = load_auth_config()
    if not cfg.oidc_enabled:
        raise HTTPException(status_code=403, detail="OIDC auth is not enabled")

    base = _public_base_url(cfg)
    redirect_uri = f"{base}/api/auth/callback/oidc"

    state = random_token(32)
    nonce = random_token(32)
    verifier = random_token(32)  # 43+ chars (base64url) -> valid PKCE verifier
    challenge = pkce_challenge(verifier)
    safe_next = sanitize_next_path(next_path)

    # Hint the account chooser for single-domain setups (not a security boundary).
    # Note: This is Google-specific but harmless for other providers.
    hd = cfg.allowed_domains[0] if len(cfg.allowed_domains) == 1 else None
    url = build_authorize_url(
        cfg,
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
        hd=hd,
    )

    resp = RedirectResponse(url=url, status_code=302)
    resp.headers["Cache-Control"] = "no-store"
    resp.set_cookie(**_oauth_cookie_kwargs(cfg, key="tarka_oauth_state", value=state, max_age=_OAUTH_TTL_SECONDS))
    resp.set_cookie(**_oauth_cookie_kwargs(cfg, key="tarka_oauth_nonce", value=nonce, max_age=_OAUTH_TTL_SECONDS))
    resp.set_cookie(**_oauth_cookie_kwargs(cfg, key="tarka_oauth_verifier", value=verifier, max_age=_OAUTH_TTL_SECONDS))
    resp.set_cookie(**_oauth_cookie_kwargs(cfg, key="tarka_oauth_next", value=safe_next, max_age=_OAUTH_TTL_SECONDS))
    return resp


@app.get("/api/auth/callback/oidc")
async def auth_callback_oidc(request: Request, code: str = Query(...), state: str = Query(...)):
    """Handle OIDC callback after user authenticates with provider."""
    from agent.auth.config import load_auth_config
    from agent.auth.models import AuthUser
    from agent.auth.oidc import exchange_code_for_tokens, validate_id_token
    from agent.auth.session import encode_session, session_cookie_kwargs
    from agent.auth.util import sanitize_next_path

    cfg = load_auth_config()
    if not cfg.oidc_enabled:
        raise HTTPException(status_code=403, detail="OIDC auth is not enabled")

    base = _public_base_url(cfg)
    redirect_uri = f"{base}/api/auth/callback/oidc"

    cookie_state = (request.cookies.get("tarka_oauth_state") or "").strip()
    cookie_nonce = (request.cookies.get("tarka_oauth_nonce") or "").strip()
    cookie_verifier = (request.cookies.get("tarka_oauth_verifier") or "").strip()
    cookie_next = sanitize_next_path(request.cookies.get("tarka_oauth_next"))

    if not cookie_state or cookie_state != (state or "").strip():
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    if not cookie_nonce or not cookie_verifier:
        raise HTTPException(status_code=400, detail="Missing OAuth verifier/nonce")

    tokens = exchange_code_for_tokens(cfg, redirect_uri=redirect_uri, code=code, code_verifier=cookie_verifier)
    id_token = str(tokens.get("id_token") or "").strip()
    if not id_token:
        raise HTTPException(status_code=400, detail="Missing id_token in token response")

    claims = validate_id_token(cfg, id_token=id_token, expected_nonce=cookie_nonce)
    email = str(claims.get("email") or "").strip().lower()
    name = str(claims.get("name") or "").strip() or None
    picture = str(claims.get("picture") or "").strip() or None
    if "@" not in email:
        raise HTTPException(status_code=403, detail="Missing email claim")

    # Workspace policy: allow only configured domains (if specified).
    if cfg.allowed_domains:
        domain = email.split("@", 1)[1].lower()
        if domain not in set(cfg.allowed_domains):
            raise HTTPException(status_code=403, detail="Account domain not allowed")

    user = AuthUser(provider="oidc", email=email, name=name, picture=picture)
    session_value = encode_session(cfg, user)
    if not session_value:
        raise HTTPException(status_code=500, detail="Session signing is not configured (AUTH_SESSION_SECRET)")

    resp = RedirectResponse(url=cookie_next, status_code=302)
    resp.headers["Cache-Control"] = "no-store"
    resp.set_cookie(**session_cookie_kwargs(cfg, session_value))
    # Clear OAuth cookies.
    resp.set_cookie(**_oauth_cookie_clear_kwargs(cfg, key="tarka_oauth_state"))
    resp.set_cookie(**_oauth_cookie_clear_kwargs(cfg, key="tarka_oauth_nonce"))
    resp.set_cookie(**_oauth_cookie_clear_kwargs(cfg, key="tarka_oauth_verifier"))
    resp.set_cookie(**_oauth_cookie_clear_kwargs(cfg, key="tarka_oauth_next"))
    return resp


@app.post("/api/auth/login/local")
def auth_login_local(request: Request, credentials: Dict[str, str]) -> JSONResponse:
    """
    Local username/password authentication.
    Rate-limited to prevent brute force attacks.
    """
    from agent.auth.config import load_auth_config
    from agent.auth.local import authenticate_local
    from agent.auth.rate_limit import get_rate_limiter
    from agent.auth.session import encode_session, session_cookie_kwargs

    cfg = load_auth_config()
    if not cfg.local_enabled:
        raise HTTPException(status_code=403, detail="Local auth is not enabled")

    username = (credentials.get("username") or "").strip()
    password = credentials.get("password") or ""

    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing username or password")

    # Rate limiting by username
    rate_limiter = get_rate_limiter()
    allowed, remaining = rate_limiter.check_and_increment(username)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed login attempts. Please try again later. ({remaining} attempts remaining)",
        )

    # Get database connection
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not configured")

    try:
        # Authenticate user
        user = authenticate_local(conn, username, password)
        if not user:
            raise HTTPException(
                status_code=401, detail=f"Invalid username or password ({remaining} attempts remaining)"
            )

        # Reset rate limiter on successful login
        rate_limiter.reset(username)

        # Create session
        session_value = encode_session(cfg, user)
        if not session_value:
            raise HTTPException(status_code=500, detail="Session signing is not configured (AUTH_SESSION_SECRET)")

        resp = JSONResponse(
            content={
                "ok": True,
                "user": {
                    "provider": user.provider,
                    "email": user.email,
                    "name": user.name,
                    "username": user.username,
                },
            }
        )
        resp.headers["Cache-Control"] = "no-store"
        resp.set_cookie(**session_cookie_kwargs(cfg, session_value))
        return resp
    finally:
        if conn:
            conn.close()


@app.post("/api/auth/logout")
async def auth_logout() -> JSONResponse:
    from agent.auth.config import load_auth_config
    from agent.auth.session import clear_session_cookie_kwargs

    cfg = load_auth_config()
    resp = JSONResponse(content={"ok": True})
    resp.headers["Cache-Control"] = "no-store"
    resp.set_cookie(**clear_session_cookie_kwargs(cfg))
    return resp


@app.get("/api/auth/me")
async def auth_me(request: Request) -> Dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {
        "ok": True,
        "user": {
            "provider": user.provider,
            "email": user.email,
            "name": user.name,
            "picture": getattr(user, "picture", None),
            "username": getattr(user, "username", None),
        },
    }


@app.get("/api/auth/mode")
async def auth_mode() -> Dict[str, Any]:
    """
    Expose the configured auth mode so the UI can render the correct login options.
    This endpoint is intentionally public; it returns no secrets.
    """
    from agent.auth.config import load_auth_config

    cfg = load_auth_config()
    result: Dict[str, Any] = {
        "ok": True,
        "oidcEnabled": cfg.oidc_enabled,
        "localEnabled": cfg.local_enabled,  # Always true
    }

    # Add provider metadata if OIDC is enabled
    if cfg.oidc_enabled:
        try:
            from agent.auth.oidc import get_provider_metadata

            metadata = get_provider_metadata(cfg)
            result["oidcProvider"] = {
                "name": metadata["name"],
                "logo": metadata["logo"],
                "loginUrl": "/api/auth/login/oidc",
            }
        except Exception as e:
            # If we can't fetch metadata, OIDC is effectively disabled
            logger.warning("Failed to get OIDC provider metadata: %s", str(e))
            result["oidcEnabled"] = False

    return result


@app.post("/feedback")
async def feedback(req: FeedbackRequest) -> Dict[str, Any]:
    """
    Record human feedback on a suggestion/skill.

    This endpoint is optional; it requires Postgres configuration.
    """
    try:
        from agent.memory.feedback import record_skill_feedback

        ok, msg = record_skill_feedback(
            case_id=req.case_id,
            run_id=req.run_id,
            skill_id=req.skill_id,
            outcome=req.outcome,
            notes=req.notes,
            actor=req.actor,
        )
        if not ok:
            raise HTTPException(status_code=503, detail=msg)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error recording feedback")
        raise HTTPException(status_code=500, detail=str(e))


# ---- Case resolution / feedback loop (policy-agnostic; requires Postgres) ----


class CaseResolveRequest(BaseModel):
    resolution_category: str
    resolution_summary: str
    postmortem_link: Optional[str] = None


class CaseReopenRequest(BaseModel):
    reason: Optional[str] = None


def _update_case_resolution(
    conn,
    *,
    case_id: str,
    status: str,
    resolution_category: Optional[str],
    resolution_summary: Optional[str],
    postmortem_link: Optional[str],
) -> Tuple[bool, str]:
    """
    Update case status + resolution fields. Best-effort; never raises.
    """
    try:
        cid = str(case_id or "").strip()
        if not cid:
            return False, "case_id_required"
        st = (status or "").strip().lower()
        if st not in ("open", "closed"):
            return False, "invalid_status"
        # For closed cases, require a category+summary (keeps memory useful).
        if st == "closed":
            if not (resolution_category and str(resolution_category).strip()):
                return False, "resolution_category_required"
            if not (resolution_summary and str(resolution_summary).strip()):
                return False, "resolution_summary_required"

        # NOTE: use now() so DB server time is canonical.
        if st == "closed":
            conn.execute(
                """
                UPDATE cases
                SET status = 'closed',
                    updated_at = now(),
                    resolved_at = now(),
                    resolution_category = %s,
                    resolution_summary = %s,
                    postmortem_link = %s
                WHERE case_id::text = %s
                """,
                (
                    str(resolution_category or "").strip(),
                    str(resolution_summary or "").strip(),
                    str(postmortem_link or "").strip() or None,
                    cid,
                ),
            )
        else:
            # Reopen: keep a history in incident tooling; here we clear resolution fields for simplicity.
            conn.execute(
                """
                UPDATE cases
                SET status = 'open',
                    updated_at = now(),
                    resolved_at = NULL,
                    resolution_category = NULL,
                    resolution_summary = NULL,
                    postmortem_link = NULL
                WHERE case_id::text = %s
                """,
                (cid,),
            )
        return True, "ok"
    except Exception as e:
        return False, f"db_error:{type(e).__name__}"


@app.post("/api/v1/cases/{case_id}/resolve")
async def resolve_case(case_id: str, req: CaseResolveRequest) -> Dict[str, Any]:
    """
    Mark a case as resolved (closed) and store resolution feedback for memory/learning.
    """
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Postgres not configured")
    try:
        ok, msg = _update_case_resolution(
            conn,
            case_id=case_id,
            status="closed",
            resolution_category=req.resolution_category,
            resolution_summary=req.resolution_summary,
            postmortem_link=req.postmortem_link,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/v1/cases/{case_id}/reopen")
async def reopen_case(case_id: str, _req: CaseReopenRequest) -> Dict[str, Any]:
    """
    Reopen a case (clears resolution fields).
    """
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Postgres not configured")
    try:
        ok, msg = _update_case_resolution(
            conn,
            case_id=case_id,
            status="open",
            resolution_category=None,
            resolution_summary=None,
            postmortem_link=None,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return {"ok": True}
    finally:
        conn.close()


def _safe_float(value: Any) -> Optional[float]:
    """Safely convert a value to float, returning None if conversion fails."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _inbox_driver_label(
    primary_driver: Optional[str], family: Optional[str], alertname: Optional[str]
) -> Optional[str]:
    """
    Human label for the case "driver" used in short inbox titles.

    Prefer `primary_driver`, then `family`, then fall back to a cleaned `alertname`.
    """
    key = (primary_driver or "").strip().lower() or (family or "").strip().lower()
    if key:
        mapping = {
            "crashloop": "Crashloop",
            "cpu_throttling": "CPU throttle",
            "http_5xx": "HTTP 5xx",
            "oom_killed": "OOMKilled",
            "memory_pressure": "Memory pressure",
            "target_down": "Target down",
            "k8s_rollout_health": "Rollout",
        }
        if key in mapping:
            return mapping[key]
        # Best-effort: make snake_case readable
        pretty = re.sub(r"[_\-]+", " ", key).strip()
        if pretty:
            return pretty[:1].upper() + pretty[1:]

    raw = (alertname or "").strip()
    if not raw:
        return None
    # Clean alertname into a short readable phrase (avoid punctuation noise).
    cleaned = re.sub(r"[_\-]+", " ", raw)
    cleaned = re.sub(r"[^A-Za-z0-9 ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _short_inbox_title(
    *,
    primary_driver: Optional[str],
    family: Optional[str],
    alertname: Optional[str],
    service: Optional[str],
    namespace: Optional[str],
    cluster: Optional[str],
    max_words: int = 4,
) -> Optional[str]:
    """
    Create a **3–4 word** inbox title using Driver + Target.

    Target semantics match the existing UI fallback: service -> namespace -> cluster.
    """
    driver = _inbox_driver_label(primary_driver, family, alertname)
    target = (service or "").strip() or (namespace or "").strip() or (cluster or "").strip()

    base = " ".join([x for x in [driver, target] if x]).strip()
    if not base:
        return None

    words = [w for w in base.split() if w]
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def _get_db_connection():
    """Get a Postgres connection, or return None if not configured."""
    try:
        import psycopg  # type: ignore[import-not-found]

        from agent.memory.config import build_postgres_dsn, load_memory_config

        cfg = load_memory_config()
        dsn = build_postgres_dsn(cfg)
        if not dsn:
            return None
        return psycopg.connect(dsn)
    except Exception as e:
        logger.debug("Failed to connect to Postgres: %s", str(e))
        return None


def _apply_inbox_hybrid_search(cte_conditions: List[str], cte_params: List[Any], q: str) -> None:
    """
    Apply hybrid search to the case/runs CTE filter list.

    Semantics:
    - key:value filters (ns/pod/deploy/svc/cluster/alert): AND across keys, OR across repeated values
    - free-text tokens: AND across tokens; each token is OR across a set of searchable fields
    """
    parsed = parse_search_query(q)

    # key:value filters
    # NOTE: We use ILIKE '%v%' for flexible matching (consistent with free-text tokens).
    filter_fields: Dict[str, List[str]] = {
        "namespace": ["c.namespace", "r.namespace"],
        "pod": ["r.pod"],
        "workload": ["c.workload_name", "r.workload_name"],
        "service": ["c.service", "r.service"],
        "cluster": ["c.cluster", "r.cluster"],
        "alertname": ["r.alertname"],
    }
    for key, values in parsed.filters.items():
        fields = filter_fields.get(key) or []
        if not fields:
            continue
        ors: List[str] = []
        for v in values:
            if not v:
                continue
            like = f"%{v}%"
            per_value_ors: List[str] = []
            for f in fields:
                per_value_ors.append(f"{f} ILIKE %s")
                cte_params.append(like)
            ors.append("(" + " OR ".join(per_value_ors) + ")")
        if ors:
            cte_conditions.append("(" + " OR ".join(ors) + ")")

    # free-text tokens (AND across tokens)
    token_fields: List[str] = [
        # Case fields
        "c.case_id::text",
        "c.cluster",
        "c.namespace",
        "c.workload_kind",
        "c.workload_name",
        "c.service",
        "c.instance",
        # Run fields
        "r.alertname",
        "r.cluster",
        "r.namespace",
        "r.pod",
        "r.container",
        "r.workload_kind",
        "r.workload_name",
        "r.service",
        "r.instance",
        # Strict SSoT fields (analysis_json)
        "r.analysis_json #>> '{analysis,verdict,one_liner}'",
        "r.analysis_json #>> '{analysis,features,family}'",
        "r.analysis_json #>> '{analysis,verdict,primary_driver}'",
    ]
    for tok in parsed.tokens:
        like = f"%{tok}%"
        ors: List[str] = []
        for f in token_fields:
            ors.append(f"{f} ILIKE %s")
            cte_params.append(like)
        if ors:
            cte_conditions.append("(" + " OR ".join(ors) + ")")


@app.get("/api/v1/cases")
async def list_cases(
    status: str = Query("open", description="Filter by status (open, closed, all)"),
    q: str = Query("", description="Search query"),
    service: str = Query("", description="Filter by service"),
    classification: str = Query("", description="Filter by classification"),
    family: str = Query("", description="Filter by family"),
    team: str = Query("", description="Filter by team"),
    limit: int = Query(50, ge=1, le=1000, description="Limit results"),
    offset: int = Query(0, ge=0, description="Offset results"),
) -> Dict[str, Any]:
    """
    List cases with filtering and pagination.
    """
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Postgres not configured")

    try:
        # Build WHERE clause for CTE (filters both cases and runs)
        cte_conditions = []
        cte_params: List[Any] = []

        if status and status.lower() != "all":
            cte_conditions.append("c.status = %s")
            cte_params.append(status.lower())

        if service:
            cte_conditions.append("c.service = %s")
            cte_params.append(service)

        if classification:
            cte_conditions.append(
                "LOWER(NULLIF(r.analysis_json #>> '{analysis,verdict,classification}', '')) = LOWER(%s)"
            )
            cte_params.append(classification)

        if family:
            cte_conditions.append("LOWER(NULLIF(r.analysis_json #>> '{analysis,features,family}', '')) = LOWER(%s)")
            cte_params.append(family)

        if team:
            cte_conditions.append("LOWER(NULLIF(r.analysis_json #>> '{target,team}', '')) = LOWER(%s)")
            cte_params.append(team)

        if q:
            _apply_inbox_hybrid_search(cte_conditions, cte_params, q)

        cte_where = " AND " + " AND ".join(cte_conditions) if cte_conditions else ""

        # Get latest run for each case
        query = f"""
            WITH latest_runs AS (
                SELECT DISTINCT ON (r.case_id)
                    r.case_id,
                    r.run_id,
                    r.created_at as run_created_at,
                    r.alertname,
                    NULLIF(r.analysis_json #>> '{{analysis,verdict,severity}}', '') as severity,
                    r.cluster,
                    r.namespace,
                    r.service,
                    r.instance,
                    NULLIF(r.analysis_json #>> '{{analysis,features,family}}', '') as family,
                    NULLIF(r.analysis_json #>> '{{analysis,verdict,classification}}', '') as classification,
                    NULLIF(r.analysis_json #>> '{{analysis,verdict,primary_driver}}', '') as primary_driver,
                    NULLIF(r.analysis_json #>> '{{analysis,verdict,one_liner}}', '') as one_liner,
                    NULLIF(r.analysis_json #>> '{{analysis,scores,impact_score}}', '')::int as impact_score,
                    NULLIF(r.analysis_json #>> '{{analysis,scores,confidence_score}}', '')::int as confidence_score,
                    NULLIF(r.analysis_json #>> '{{analysis,scores,noise_score}}', '')::int as noise_score,
                    NULLIF(r.analysis_json #>> '{{target,team}}', '') as team,
                    NULLIF(r.analysis_json #>> '{{analysis,enrichment,label}}', '') as enrichment_summary
                FROM investigation_runs r
                INNER JOIN cases c ON r.case_id = c.case_id
                WHERE 1=1 {cte_where}
                ORDER BY r.case_id, r.created_at DESC
            )
            SELECT
                c.case_id::text,
                c.status as case_status,
                c.created_at::text as case_created_at,
                c.updated_at::text as case_updated_at,
                lr.run_id::text,
                lr.run_created_at::text,
                lr.alertname,
                lr.severity as severity,
                lr.cluster,
                lr.namespace,
                lr.service,
                lr.instance,
                lr.family,
                lr.classification,
                lr.primary_driver,
                lr.one_liner,
                lr.impact_score,
                lr.confidence_score,
                lr.noise_score,
                lr.team,
                lr.enrichment_summary
            FROM cases c
            INNER JOIN latest_runs lr ON c.case_id = lr.case_id
            ORDER BY c.updated_at DESC
            LIMIT %s OFFSET %s
        """
        query_params = cte_params + [limit, offset]

        rows = conn.execute(query, query_params).fetchall()

        # Count total and by status - need to apply same filters as CTE
        count_query = f"""
            SELECT
                COUNT(DISTINCT c.case_id) as total,
                COUNT(DISTINCT CASE WHEN c.status = 'open' THEN c.case_id END) as open_count,
                COUNT(DISTINCT CASE WHEN c.status = 'closed' THEN c.case_id END) as closed_count
            FROM cases c
            INNER JOIN investigation_runs r ON r.case_id = c.case_id
            WHERE 1=1 {cte_where}
        """
        count_row = conn.execute(count_query, cte_params).fetchone()

        items = []
        for row in rows:
            one_liner = str(row[15]) if row[15] else None
            alertname = str(row[6]) if row[6] else None
            title = _short_inbox_title(
                primary_driver=str(row[14]) if row[14] else None,
                family=str(row[12]) if row[12] else None,
                alertname=alertname,
                service=str(row[10]) if row[10] else None,
                namespace=str(row[9]) if row[9] else None,
                cluster=str(row[8]) if row[8] else None,
                max_words=4,
            )
            items.append(
                {
                    "case_id": str(row[0]) if row[0] else None,
                    "case_status": str(row[1]) if row[1] else None,
                    "case_created_at": str(row[2]) if row[2] else None,
                    "case_updated_at": str(row[3]) if row[3] else None,
                    "run_id": str(row[4]) if row[4] else None,
                    "run_created_at": str(row[5]) if row[5] else None,
                    "alertname": alertname,
                    "severity": str(row[7]) if row[7] else None,
                    "cluster": str(row[8]) if row[8] else None,
                    "namespace": str(row[9]) if row[9] else None,
                    "service": str(row[10]) if row[10] else None,
                    "instance": str(row[11]) if row[11] else None,
                    "family": str(row[12]) if row[12] else None,
                    "classification": str(row[13]) if row[13] else None,
                    "primary_driver": str(row[14]) if row[14] else None,
                    "title": title,
                    "one_liner": one_liner,
                    "impact_score": row[16] if row[16] is not None else None,
                    "confidence_score": row[17] if row[17] is not None else None,
                    "noise_score": row[18] if row[18] is not None else None,
                    "team": str(row[19]) if row[19] else None,
                    "enrichment_summary": str(row[20]) if row[20] else None,
                }
            )

        total = int(count_row[0]) if count_row and count_row[0] else 0
        open_count = int(count_row[1]) if count_row and count_row[1] else 0
        closed_count = int(count_row[2]) if count_row and count_row[2] else 0

        return {
            "total": total,
            "counts": {
                "open": open_count,
                "closed": closed_count,
                "total": total,
            },
            "items": items,
        }
    except Exception as e:
        logger.exception("Error listing cases")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        conn.close()


@app.get("/api/v1/cases/facets")
async def case_facets(
    status: str = Query("open", description="Filter by status (open, closed, all)"),
    q: str = Query("", description="Search query"),
    service: str = Query("", description="Filter by service"),
    classification: str = Query("", description="Filter by classification"),
    family: str = Query("", description="Filter by family"),
) -> Dict[str, Any]:
    """
    Facets for filters (non-paginated).

    Note: This returns distinct values across the full matching set, not only the current page.
    SSoT: derived strictly from `analysis_json`.
    """
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Postgres not configured")

    try:
        cte_conditions: List[str] = []
        cte_params: List[Any] = []

        if status and status.lower() != "all":
            cte_conditions.append("c.status = %s")
            cte_params.append(status.lower())

        if service:
            cte_conditions.append("c.service = %s")
            cte_params.append(service)

        if classification:
            cte_conditions.append(
                "LOWER(NULLIF(r.analysis_json #>> '{analysis,verdict,classification}', '')) = LOWER(%s)"
            )
            cte_params.append(classification)

        if family:
            cte_conditions.append("LOWER(NULLIF(r.analysis_json #>> '{analysis,features,family}', '')) = LOWER(%s)")
            cte_params.append(family)

        if q:
            _apply_inbox_hybrid_search(cte_conditions, cte_params, q)

        cte_where = " AND " + " AND ".join(cte_conditions) if cte_conditions else ""

        # Distinct teams across all matching cases (strict SSoT: target.team only).
        rows = conn.execute(
            f"""
            SELECT DISTINCT LOWER(NULLIF(r.analysis_json #>> '{{target,team}}', '')) as team
            FROM investigation_runs r
            INNER JOIN cases c ON r.case_id = c.case_id
            WHERE 1=1 {cte_where}
            ORDER BY team ASC
            """,
            tuple(cte_params),
        ).fetchall()

        teams: List[str] = []
        for (t,) in rows:
            if t:
                teams.append(str(t))

        return {"teams": teams}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting case facets")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        conn.close()


@app.get("/api/v1/cases/{case_id}")
async def get_case(
    case_id: str,
    runs_limit: int = Query(25, ge=1, le=100, description="Limit number of runs returned"),
) -> Dict[str, Any]:
    """
    Get case details including all runs.
    """
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Postgres not configured")

    try:
        # Get case
        inc_row = conn.execute(
            """
            SELECT
                case_id::text,
                case_key,
                status,
                created_at::text,
                updated_at::text,
                cluster,
                target_type,
                namespace,
                workload_kind,
                workload_name,
                service,
                instance,
                family,
                primary_driver,
                latest_one_liner,
                s3_report_key,
                s3_investigation_key,
                resolved_at::text,
                resolution_summary,
                resolution_category,
                postmortem_link
            FROM cases
            WHERE case_id::text = %s
            """,
            (case_id,),
        ).fetchone()

        if not inc_row:
            raise HTTPException(status_code=404, detail="Case not found")

        # Get runs
        run_rows = conn.execute(
            """
            SELECT
                run_id::text,
                created_at::text,
                alert_fingerprint,
                alertname,
                severity,
                starts_at,
                normalized_state,
                target_type,
                cluster,
                namespace,
                pod,
                container,
                workload_kind,
                workload_name,
                service,
                instance,
                family,
                classification,
                primary_driver,
                one_liner,
                reason_codes,
                s3_report_key,
                s3_investigation_key,
                analysis_json,
                report_text,
                case_match_reason
            FROM investigation_runs
            WHERE case_id::text = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (case_id, runs_limit),
        ).fetchall()

        runs = []
        for row in run_rows:
            analysis_json = row[23] if row[23] else None
            view = _run_view_fields_from_analysis_json(analysis_json)
            runs.append(
                {
                    "run_id": str(row[0]) if row[0] else None,
                    "created_at": str(row[1]) if row[1] else None,
                    "alert_fingerprint": str(row[2]) if row[2] else None,
                    "alertname": str(row[3]) if row[3] else None,
                    "severity": view.get("severity"),
                    "starts_at": str(row[5]) if row[5] else None,
                    "normalized_state": str(row[6]) if row[6] else None,
                    "target_type": str(row[7]) if row[7] else None,
                    "cluster": str(row[8]) if row[8] else None,
                    "namespace": str(row[9]) if row[9] else None,
                    "pod": str(row[10]) if row[10] else None,
                    "container": str(row[11]) if row[11] else None,
                    "workload_kind": str(row[12]) if row[12] else None,
                    "workload_name": str(row[13]) if row[13] else None,
                    "service": str(row[14]) if row[14] else None,
                    "instance": str(row[15]) if row[15] else None,
                    "family": view.get("family"),
                    "classification": view.get("classification"),
                    "primary_driver": view.get("primary_driver"),
                    "one_liner": view.get("one_liner"),
                    "impact_score": view.get("impact_score"),
                    "confidence_score": view.get("confidence_score"),
                    "noise_score": view.get("noise_score"),
                    "team": view.get("team"),
                    "reason_codes": list(row[20]) if row[20] else None,
                    "s3_report_key": str(row[21]) if row[21] else None,
                    "s3_investigation_key": str(row[22]) if row[22] else None,
                    "analysis_json": analysis_json,
                    "report_text": str(row[24]) if row[24] else None,
                    "case_match_reason": str(row[25]) if row[25] else None,
                }
            )

        case_obj = {
            "case_id": str(inc_row[0]) if inc_row[0] else None,
            "case_key": str(inc_row[1]) if inc_row[1] else None,
            "status": str(inc_row[2]) if inc_row[2] else None,
            "created_at": str(inc_row[3]) if inc_row[3] else None,
            "updated_at": str(inc_row[4]) if inc_row[4] else None,
            "cluster": str(inc_row[5]) if inc_row[5] else None,
            "target_type": str(inc_row[6]) if inc_row[6] else None,
            "namespace": str(inc_row[7]) if inc_row[7] else None,
            "workload_kind": str(inc_row[8]) if inc_row[8] else None,
            "workload_name": str(inc_row[9]) if inc_row[9] else None,
            "service": str(inc_row[10]) if inc_row[10] else None,
            "instance": str(inc_row[11]) if inc_row[11] else None,
            "family": str(inc_row[12]) if inc_row[12] else None,
            "primary_driver": str(inc_row[13]) if inc_row[13] else None,
            "latest_one_liner": str(inc_row[14]) if inc_row[14] else None,
            "s3_report_key": str(inc_row[15]) if inc_row[15] else None,
            "s3_investigation_key": str(inc_row[16]) if inc_row[16] else None,
            "resolved_at": str(inc_row[17]) if inc_row[17] else None,
            "resolution_summary": str(inc_row[18]) if inc_row[18] else None,
            "resolution_category": str(inc_row[19]) if inc_row[19] else None,
            "postmortem_link": str(inc_row[20]) if inc_row[20] else None,
        }

        return {
            "case": case_obj,
            "runs": runs,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting case")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        conn.close()


@app.get("/api/v1/cases/{case_id}/memory")
async def get_case_memory(case_id: str, limit: int = Query(5, ge=1, le=20)) -> Dict[str, Any]:
    """
    Live memory panel for the Case UI.

    Controlled solely by MEMORY_ENABLED=1. Returns best-effort similar cases + matched skills.
    """
    # Gate by MEMORY_ENABLED (no policy coupling).
    try:
        from agent.memory.config import load_memory_config

        cfg = load_memory_config()
        if not cfg.memory_enabled:
            return {"ok": True, "enabled": False, "similar_cases": [], "skills": [], "errors": []}
    except Exception:
        # If config can't be loaded, treat as disabled.
        return {
            "ok": True,
            "enabled": False,
            "similar_cases": [],
            "skills": [],
            "errors": ["memory_config_unavailable"],
        }

    conn = _get_db_connection()
    if not conn:
        return {"ok": True, "enabled": True, "similar_cases": [], "skills": [], "errors": ["postgres_not_configured"]}

    errors: List[str] = []
    try:
        # Load latest run analysis_json for the case.
        row = conn.execute(
            """
            SELECT analysis_json
            FROM investigation_runs
            WHERE case_id::text = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (case_id,),
        ).fetchone()
        if not row:
            return {"ok": True, "enabled": True, "similar_cases": [], "skills": [], "errors": ["no_runs_for_case"]}

        analysis_json = row[0] if row[0] else None
        if analysis_json is None:
            return {"ok": True, "enabled": True, "similar_cases": [], "skills": [], "errors": ["analysis_json_missing"]}
        if not isinstance(analysis_json, dict):
            try:
                analysis_json = json.loads(str(analysis_json))
            except Exception:
                analysis_json = {}

        # Build a minimal Investigation (same helper as chat uses).
        try:
            from agent.chat.tools import _build_investigation_from_analysis_json

            inv = _build_investigation_from_analysis_json(analysis_json)
        except Exception as e:
            return {
                "ok": True,
                "enabled": True,
                "similar_cases": [],
                "skills": [],
                "errors": [f"build_investigation_failed:{type(e).__name__}"],
            }

        # Similar cases
        similar_items: List[Dict[str, Any]] = []
        try:
            from agent.memory.case_retrieval import find_similar_runs

            ok, msg, sims = find_similar_runs(inv, limit=int(limit))
            if ok:
                for s in sims or []:
                    similar_items.append(
                        {
                            "case_id": s.case_id,
                            "run_id": s.run_id,
                            "created_at": s.created_at,
                            "one_liner": s.one_liner,
                            "s3_report_key": getattr(s, "s3_report_key", None),
                            "resolution_category": getattr(s, "resolution_category", None),
                            "resolution_summary": getattr(s, "resolution_summary", None),
                            "postmortem_link": getattr(s, "postmortem_link", None),
                        }
                    )
            else:
                errors.append(f"similar_cases_unavailable:{msg}")
        except Exception as e:
            emsg = str(e) if e is not None else ""
            errors.append(f"similar_cases_exception:{type(e).__name__}:{emsg[:160]}")

        # Skills
        skills_items: List[Dict[str, Any]] = []
        try:
            from agent.memory.skills import match_skills

            ok, msg, matches = match_skills(inv, max_matches=int(limit))
            if ok:
                for m in matches or []:
                    skills_items.append(
                        {
                            "name": m.skill.name,
                            "version": m.skill.version,
                            "rendered": m.rendered,
                            "match_reason": getattr(m, "match_reason", "matched"),
                        }
                    )
            else:
                errors.append(f"skills_unavailable:{msg}")
        except Exception as e:
            emsg = str(e) if e is not None else ""
            errors.append(f"skills_exception:{type(e).__name__}:{emsg[:160]}")

        return {"ok": True, "enabled": True, "similar_cases": similar_items, "skills": skills_items, "errors": errors}
    finally:
        conn.close()


@app.get("/api/v1/investigation-runs/{run_id}")
async def get_investigation_run(run_id: str) -> Dict[str, Any]:
    """
    Get run details.
    """
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Postgres not configured")

    try:
        row = conn.execute(
            """
            SELECT
                run_id::text,
                case_id::text,
                created_at::text,
                alert_fingerprint,
                alertname,
                severity,
                starts_at,
                normalized_state,
                target_type,
                cluster,
                namespace,
                pod,
                container,
                workload_kind,
                workload_name,
                service,
                instance,
                family,
                classification,
                primary_driver,
                one_liner,
                reason_codes,
                s3_report_key,
                s3_investigation_key,
                analysis_json,
                report_text,
                case_match_reason
            FROM investigation_runs
            WHERE run_id::text = %s
            """,
            (run_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Run not found")

        analysis_json = row[24] if row[24] else None
        view = _run_view_fields_from_analysis_json(analysis_json)
        run = {
            "run_id": str(row[0]) if row[0] else None,
            "case_id": str(row[1]) if row[1] else None,
            "created_at": str(row[2]) if row[2] else None,
            "alert_fingerprint": str(row[3]) if row[3] else None,
            "alertname": str(row[4]) if row[4] else None,
            "severity": view.get("severity"),
            "starts_at": str(row[6]) if row[6] else None,
            "normalized_state": str(row[7]) if row[7] else None,
            "target_type": str(row[8]) if row[8] else None,
            "cluster": str(row[9]) if row[9] else None,
            "namespace": str(row[10]) if row[10] else None,
            "pod": str(row[11]) if row[11] else None,
            "container": str(row[12]) if row[12] else None,
            "workload_kind": str(row[13]) if row[13] else None,
            "workload_name": str(row[14]) if row[14] else None,
            "service": str(row[15]) if row[15] else None,
            "instance": str(row[16]) if row[16] else None,
            "family": view.get("family"),
            "classification": view.get("classification"),
            "primary_driver": view.get("primary_driver"),
            "one_liner": view.get("one_liner"),
            "impact_score": view.get("impact_score"),
            "confidence_score": view.get("confidence_score"),
            "noise_score": view.get("noise_score"),
            "team": view.get("team"),
            "reason_codes": list(row[21]) if row[21] else None,
            "s3_report_key": str(row[22]) if row[22] else None,
            "s3_investigation_key": str(row[23]) if row[23] else None,
            "analysis_json": analysis_json,
            "report_text": str(row[25]) if row[25] else None,
            "case_match_reason": str(row[26]) if row[26] else None,
        }

        return {"run": run}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting investigation run")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        conn.close()


# ---- Tool-using chat (policy gated) ----


class ChatConfigResponse(BaseModel):
    enabled: bool
    allow_promql: bool
    allow_k8s_read: bool
    allow_logs_query: bool
    allow_argocd_read: bool
    allow_report_rerun: bool
    allow_memory_read: bool
    max_steps: int
    max_tool_calls: int


class ActionConfigResponse(BaseModel):
    enabled: bool
    require_approval: bool
    allow_execute: bool
    action_type_allowlist: Optional[List[str]] = None
    max_actions_per_case: int = 25


class ChatThreadSendRequest(BaseModel):
    # If empty, the endpoint acts as a "get thread + messages" convenience.
    message: Optional[str] = None
    # Optional: for case threads, allow pinning context to a specific run.
    run_id: Optional[str] = None
    # How many messages to return in the response (tail). Server enforces caps.
    limit: int = 50


def _require_user_key(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if not user or not getattr(user, "email", None):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return str(user.email).strip().lower()


@app.get("/api/v1/chat/config")
async def chat_config() -> Dict[str, Any]:
    from agent.authz.policy import load_chat_policy

    p = load_chat_policy()
    return ChatConfigResponse(
        enabled=p.enabled,
        allow_promql=p.allow_promql,
        allow_k8s_read=p.allow_k8s_read,
        allow_logs_query=p.allow_logs_query,
        allow_argocd_read=p.allow_argocd_read,
        allow_report_rerun=p.allow_report_rerun,
        allow_memory_read=p.allow_memory_read,
        max_steps=p.max_steps,
        max_tool_calls=p.max_tool_calls,
    ).model_dump(mode="json")


@app.get("/api/v1/actions/config")
async def actions_config() -> Dict[str, Any]:
    from agent.authz.policy import load_action_policy

    p = load_action_policy()
    allow = sorted(list(p.action_type_allowlist)) if p.action_type_allowlist else None
    return ActionConfigResponse(
        enabled=p.enabled,
        require_approval=p.require_approval,
        allow_execute=p.allow_execute,
        action_type_allowlist=allow,
        max_actions_per_case=p.max_actions_per_case,
    ).model_dump(mode="json")


class CaseActionProposeRequest(BaseModel):
    run_id: Optional[str] = None
    hypothesis_id: Optional[str] = None
    action_type: str
    title: str
    risk: Optional[str] = None
    preconditions: List[str] = []
    execution_payload: Dict[str, Any] = {}
    actor: Optional[str] = None


class CaseActionTransitionRequest(BaseModel):
    actor: Optional[str] = None
    notes: Optional[str] = None


@app.get("/api/v1/cases/{case_id}/actions")
async def list_case_actions(case_id: str, limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    from agent.memory.actions import list_case_actions

    ok, msg, items = list_case_actions(case_id=case_id, limit=int(limit))
    if not ok:
        raise HTTPException(status_code=503, detail=msg)
    out = []
    for a in items:
        out.append(a.__dict__)
    return {"ok": True, "items": out}


@app.post("/api/v1/cases/{case_id}/actions/propose")
async def propose_case_action(case_id: str, req: CaseActionProposeRequest) -> Dict[str, Any]:
    from agent.authz.policy import load_action_policy
    from agent.memory.actions import create_case_action

    p = load_action_policy()
    if not p.enabled:
        raise HTTPException(status_code=403, detail="Action proposals are disabled")
    atype = (req.action_type or "").strip().lower()
    if p.action_type_allowlist is not None and atype not in p.action_type_allowlist:
        raise HTTPException(status_code=403, detail="Action type not allowed")

    ok, msg, action_id = create_case_action(
        case_id=case_id,
        run_id=req.run_id,
        hypothesis_id=req.hypothesis_id,
        action_type=req.action_type,
        title=req.title,
        risk=req.risk,
        preconditions=req.preconditions or [],
        execution_payload=req.execution_payload or {},
        proposed_by=req.actor,
    )
    if not ok:
        raise HTTPException(status_code=503, detail=msg)
    return {"ok": True, "action_id": action_id}


@app.post("/api/v1/cases/{case_id}/actions/{action_id}/approve")
async def approve_case_action(case_id: str, action_id: str, req: CaseActionTransitionRequest) -> Dict[str, Any]:
    from agent.authz.policy import load_action_policy
    from agent.memory.actions import transition_case_action

    p = load_action_policy()
    if not p.enabled:
        raise HTTPException(status_code=403, detail="Actions are disabled")
    ok, msg = transition_case_action(
        case_id=case_id, action_id=action_id, status="approved", actor=req.actor, notes=req.notes
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True}


@app.post("/api/v1/cases/{case_id}/actions/{action_id}/reject")
async def reject_case_action(case_id: str, action_id: str, req: CaseActionTransitionRequest) -> Dict[str, Any]:
    from agent.authz.policy import load_action_policy
    from agent.memory.actions import transition_case_action

    p = load_action_policy()
    if not p.enabled:
        raise HTTPException(status_code=403, detail="Actions are disabled")
    ok, msg = transition_case_action(
        case_id=case_id, action_id=action_id, status="rejected", actor=req.actor, notes=req.notes
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True}


@app.post("/api/v1/cases/{case_id}/actions/{action_id}/execute")
async def execute_case_action(case_id: str, action_id: str, req: CaseActionTransitionRequest) -> Dict[str, Any]:
    from agent.authz.policy import load_action_policy
    from agent.memory.actions import transition_case_action

    p = load_action_policy()
    if not p.enabled:
        raise HTTPException(status_code=403, detail="Actions are disabled")
    if not p.allow_execute:
        raise HTTPException(status_code=403, detail="Action execution is disabled")
    ok, msg = transition_case_action(
        case_id=case_id, action_id=action_id, status="executed", actor=req.actor, notes=req.notes
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True}


@app.post("/api/v1/cases/{case_id}/chat")
async def case_chat(case_id: str, req: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tool-using chat endpoint for the case detail page.

    Request body:
      { run_id: string, message: string, history: [{role,content}] }
    """
    from agent.authz.policy import load_action_policy, load_chat_policy
    from agent.chat.runtime import run_chat
    from agent.chat.types import ChatRequest, ChatResponse

    policy = load_chat_policy()
    action_policy = load_action_policy()
    if not policy.enabled:
        raise HTTPException(status_code=403, detail="Chat is disabled")

    try:
        creq = ChatRequest.model_validate(req)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid chat request")

    # Load run analysis_json (SSOT) for the given run_id and ensure it belongs to this case.
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Postgres not configured")

    try:
        row = conn.execute(
            """
            SELECT
              case_id::text,
              analysis_json
            FROM investigation_runs
            WHERE run_id::text = %s
            """,
            (creq.run_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Run not found")
        case_id_db = str(row[0] or "")
        if case_id_db != str(case_id):
            raise HTTPException(status_code=404, detail="Run not found for case")
        analysis_json = row[1] if row[1] else None
        if analysis_json is None:
            raise HTTPException(status_code=404, detail="Run analysis not available")
        if not isinstance(analysis_json, dict):
            # psycopg may decode jsonb as dict; if not, best-effort parse
            try:
                analysis_json = json.loads(str(analysis_json))
            except Exception:
                analysis_json = {}

        # Run blocking chat operation in thread pool to avoid blocking event loop
        import asyncio

        res = await asyncio.to_thread(
            run_chat,
            policy=policy,
            action_policy=action_policy,
            analysis_json=analysis_json,
            user_message=creq.message,
            history=creq.history,
            case_id=str(case_id),
            run_id=str(creq.run_id),
        )
        out = ChatResponse(reply=res.reply, tool_events=res.tool_events, updated_analysis=res.updated_analysis)
        return out.model_dump(mode="json")
    finally:
        conn.close()


# ---- Threaded chat (server-persisted, per user) ----


@app.get("/api/v1/chat/threads")
async def chat_threads(request: Request, limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """
    List chat threads for the current user (includes the user's Global thread).
    """
    from agent.memory.chat import get_or_create_global_thread, list_threads

    user_key = _require_user_key(request)
    # Ensure the global thread exists so the UI always has something to attach to.
    try:
        get_or_create_global_thread(user_key=user_key)
    except Exception:
        pass

    ok, msg, items = list_threads(user_key=user_key, limit=int(limit))
    if not ok:
        raise HTTPException(status_code=503, detail=msg)
    out = []
    for it in items:
        t = it.thread
        out.append(
            {
                "thread_id": t.thread_id,
                "kind": t.kind,
                "case_id": t.case_id,
                "title": t.title,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
                "last_message_at": t.last_message_at,
                "last_message": it.last_message,
            }
        )
    return {"ok": True, "items": out}


@app.get("/api/v1/chat/threads/{thread_id}")
async def chat_thread_get(
    request: Request,
    thread_id: str,
    limit: int = Query(50, ge=1, le=200),
    before_seq: Optional[int] = Query(None, ge=1),
) -> Dict[str, Any]:
    """
    Get a thread + message tail (paginated by seq).
    """
    from agent.memory.chat import get_thread, list_messages

    user_key = _require_user_key(request)
    ok, _msg, thr = get_thread(user_key=user_key, thread_id=thread_id)
    if not ok or thr is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    okm, msgm, msgs = list_messages(user_key=user_key, thread_id=thread_id, limit=int(limit), before_seq=before_seq)
    if not okm:
        raise HTTPException(status_code=503, detail=msgm)
    return {
        "ok": True,
        "thread": thr.__dict__,
        "messages": [m.__dict__ for m in msgs],
    }


def _format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """Format data as Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _thread_send_stream(*, request: Request, thread_id: str, raw: Dict[str, Any]):
    """
    Streaming chat endpoint using Server-Sent Events.
    Replaces blocking implementation with progressive UX.
    """
    from agent.authz.policy import load_action_policy, load_chat_policy
    from agent.chat.global_runtime_streaming import run_global_chat_stream
    from agent.chat.runtime_streaming import run_chat_stream
    from agent.chat.types import ChatMessage as ChatMsg
    from agent.memory.chat import append_message, get_thread, insert_tool_events, list_messages

    user_key = _require_user_key(request)

    # Validate thread
    okt, _msgt, thr = get_thread(user_key=user_key, thread_id=thread_id)
    if not okt or thr is None:
        yield _format_sse_event("error", {"error": "Thread not found"})
        return

    # Parse request
    try:
        sreq = ChatThreadSendRequest.model_validate(raw)
    except Exception:
        yield _format_sse_event("error", {"error": "Invalid chat request"})
        return

    msg = str(sreq.message or "").strip()
    if not msg:
        # Empty message = just initialize/return thread info (for UI initialization)
        okm, _msgm, msgs = list_messages(user_key=user_key, thread_id=thread_id, limit=50)
        messages_out = []
        if okm:
            for m in msgs:
                messages_out.append({"role": m.role, "content": m.content, "created_at": m.created_at, "seq": m.seq})

        yield _format_sse_event(
            "init",
            {
                "thread": {
                    "thread_id": thr.thread_id,
                    "kind": thr.kind,
                    "case_id": thr.case_id,
                    "title": thr.title,
                },
                "messages": messages_out,
            },
        )
        return

    # Load policies
    policy = load_chat_policy()
    action_policy = load_action_policy()
    if not policy.enabled:
        yield _format_sse_event("error", {"error": "Chat is disabled by policy"})
        return

    # Append user message
    oku, msgu, user_msg = append_message(user_key=user_key, thread_id=thread_id, role="user", content=msg)
    if not oku or user_msg is None:
        yield _format_sse_event("error", {"error": msgu or "Failed to save message"})
        return

    # Load chat history
    okh, _msgh, hist_rows = list_messages(user_key=user_key, thread_id=thread_id, limit=12, before_seq=user_msg.seq)
    history: List[ChatMsg] = []
    if okh:
        history = [ChatMsg(role=("user" if m.role == "user" else "assistant"), content=m.content) for m in hist_rows]

    # Accumulate reply and events for persistence
    reply_parts = []
    tool_events_data = []

    try:
        if thr.kind == "case":
            # Case chat stream
            case_id = str(thr.case_id or "").strip()
            if not case_id:
                yield _format_sse_event("error", {"error": "Invalid case thread"})
                return

            # Load analysis JSON
            conn = _get_db_connection()
            if not conn:
                yield _format_sse_event("error", {"error": "Postgres not configured"})
                return

            try:
                run_id = (str(sreq.run_id).strip() if sreq.run_id else None) or None
                if run_id:
                    row = conn.execute(
                        """
                        SELECT case_id::text, analysis_json
                        FROM investigation_runs
                        WHERE run_id::text = %s
                        """,
                        (run_id,),
                    ).fetchone()
                    if not row:
                        yield _format_sse_event("error", {"error": "Run not found"})
                        return
                    if str(row[0] or "") != case_id:
                        yield _format_sse_event("error", {"error": "Run not found for case"})
                        return
                    analysis_json = row[1]
                else:
                    row = conn.execute(
                        """
                        SELECT analysis_json
                        FROM investigation_runs
                        WHERE case_id::text = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (case_id,),
                    ).fetchone()
                    analysis_json = row[0] if row else None

                if analysis_json is None:
                    yield _format_sse_event("error", {"error": "Run analysis not available"})
                    return

                if not isinstance(analysis_json, dict):
                    try:
                        analysis_json = json.loads(str(analysis_json))
                    except Exception:
                        analysis_json = {}

                # Stream chat events
                async for event in run_chat_stream(
                    policy=policy,
                    action_policy=action_policy,
                    analysis_json=analysis_json,
                    user_message=msg,
                    history=history,
                    case_id=case_id,
                    run_id=run_id,
                ):
                    # Format and emit SSE
                    data = {"content": event.content}
                    if event.tool:
                        data["tool"] = event.tool
                    if event.metadata:
                        data["metadata"] = event.metadata

                    yield _format_sse_event(event.event_type, data)

                    # Accumulate for persistence
                    if event.event_type == "token":
                        reply_parts.append(event.content)
                    elif event.event_type == "done":
                        tool_events_data = event.metadata.get("tool_events", [])

            finally:
                conn.close()

        else:
            # Global chat stream
            async for event in run_global_chat_stream(
                policy=policy,
                user_message=msg,
                history=history,
            ):
                # Format and emit SSE
                data = {"content": event.content}
                if event.tool:
                    data["tool"] = event.tool
                if event.metadata:
                    data["metadata"] = event.metadata

                yield _format_sse_event(event.event_type, data)

                # Accumulate for persistence
                if event.event_type == "token":
                    reply_parts.append(event.content)
                elif event.event_type == "done":
                    tool_events_data = event.metadata.get("tool_events", [])

        # Persist complete message
        reply = "".join(reply_parts) or "—"
        oka, msga, asst_msg = append_message(user_key=user_key, thread_id=thread_id, role="assistant", content=reply)
        if not oka or asst_msg is None:
            logger.error(f"Failed to save assistant message: {msga}")
            # Don't emit error - stream already completed successfully

        # Persist tool events
        if asst_msg and tool_events_data:
            try:
                insert_tool_events(
                    user_key=user_key,
                    thread_id=thread_id,
                    message_id=asst_msg.message_id,
                    tool_events=tool_events_data,
                )
            except Exception as e:
                logger.error(f"Failed to save tool events: {e}")

    except Exception as e:
        logger.exception("Stream error")
        yield _format_sse_event("error", {"error": str(e)})


@app.post("/api/v1/chat/threads/{thread_id}/send")
async def chat_thread_send(request: Request, thread_id: str, req: Dict[str, Any]) -> StreamingResponse:
    """Streaming chat endpoint using Server-Sent Events."""
    return StreamingResponse(
        _thread_send_stream(request=request, thread_id=thread_id, raw=req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@app.post("/api/v1/chat/threads/global")
async def chat_thread_global(request: Request, req: Dict[str, Any]) -> StreamingResponse:
    """Streaming global chat endpoint."""
    from agent.memory.chat import get_or_create_global_thread

    user_key = _require_user_key(request)
    ok, msg, thr = get_or_create_global_thread(user_key=user_key)
    if not ok or thr is None:
        raise HTTPException(status_code=503, detail=msg)

    return StreamingResponse(
        _thread_send_stream(request=request, thread_id=thr.thread_id, raw=req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/v1/chat/threads/case/{case_id}")
async def chat_thread_case(request: Request, case_id: str, req: Dict[str, Any]) -> StreamingResponse:
    """Streaming case chat endpoint."""
    from agent.memory.chat import get_or_create_case_thread

    user_key = _require_user_key(request)
    ok, msg, thr = get_or_create_case_thread(user_key=user_key, case_id=case_id)
    if not ok or thr is None:
        raise HTTPException(status_code=503, detail=msg)

    return StreamingResponse(
        _thread_send_stream(request=request, thread_id=thr.thread_id, raw=req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/alerts")
async def alerts(request: Request) -> JSONResponse:
    try:
        logger.info("Received webhook request from %s", request.client.host if request.client else "unknown")

        payload = await request.json()
        logger.debug("Webhook payload keys: %s", list(payload.keys()) if isinstance(payload, dict) else "not a dict")

        if not isinstance(payload, dict):
            logger.error("Invalid JSON payload: expected dict, got %s", type(payload).__name__)
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        alerts_list = payload.get("alerts", [])
        if not isinstance(alerts_list, list):
            logger.error("Payload field 'alerts' is not a list: %s", type(alerts_list).__name__)
            raise HTTPException(status_code=400, detail="Payload field 'alerts' must be a list")

        logger.info("Processing %d alert(s) from webhook", len(alerts_list))

        time_window = os.getenv("TIME_WINDOW", "1h").strip() or "1h"
        parent_status = payload.get("status")
        parent_status_s = parent_status if isinstance(parent_status, str) else None
        allowlist = _get_allowlist()
        if allowlist:
            logger.info("Using alertname allowlist: %s", allowlist)

        # Enqueue-only: fast-ack by publishing jobs to JetStream and returning 202 quickly.
        from agent.core.dedup import compute_queue_msg_id_for_workload_hour, compute_utc_hour_bucket_label
        from agent.queue.nats_jetstream import (
            compute_fingerprint_fallback,
            compute_msg_id_from_dedup_key,
            get_client_from_env,
        )

        client = await get_client_from_env()
        seen_keys: set[str] = set()
        now_utc = utcnow()
        env_cluster = (os.getenv("CLUSTER_NAME") or "").strip() or None
        enqueued = 0
        skipped_resolved = 0
        skipped_allowlist = 0
        skipped_duplicate = 0
        errors = 0

        for raw in alerts_list:
            try:
                alert_norm = _normalize_webhook_alert(raw, parent_status=parent_status_s)
                labels = alert_norm.get("labels", {}) or {}
                alertname = labels.get("alertname") or "Unknown"

                state = (alert_norm.get("status", {}) or {}).get("state") or "unknown"
                if state != "firing":
                    skipped_resolved += 1
                    continue
                if allowlist is not None and str(alertname) not in allowlist:
                    skipped_allowlist += 1
                    continue

                fp = str(alert_norm.get("fingerprint") or "")
                if not fp:
                    fp = compute_fingerprint_fallback(labels if isinstance(labels, dict) else {})

                # For rollout-noisy alerts, use workload-derived identity + 1h bucket for queue-level dedupe.
                msg_id: Optional[str] = None
                a = str(alertname)
                if a in (
                    "KubernetesPodNotHealthy",
                    "KubernetesPodNotHealthyCritical",
                    "KubernetesContainerOomKiller",
                ):
                    pod_target = extract_pod_info_from_alert(alert_norm)
                    if pod_target and pod_target.get("pod") and pod_target.get("namespace"):
                        try:
                            from agent.providers.k8s_provider import get_pod_owner_chain

                            oc = get_pod_owner_chain(str(pod_target["pod"]), str(pod_target["namespace"]))
                            wk = compute_rollout_workload_key(
                                alertname=a,
                                labels=labels if isinstance(labels, dict) else {},
                                owner_chain=oc if isinstance(oc, dict) else {},
                                env_cluster=env_cluster,
                                include_container=(a == "KubernetesContainerOomKiller"),
                            )
                            if wk:
                                hb = compute_utc_hour_bucket_label(now=now_utc)
                                msg_id = compute_queue_msg_id_for_workload_hour(workload_key=wk, hour_bucket=hb)
                        except Exception:
                            msg_id = None

                # Fallback: existing dedup key strategy (stable across label churn, 4h bucket).
                if not msg_id:
                    dedup = compute_dedup_key(
                        alertname=str(alertname),
                        labels=labels if isinstance(labels, dict) else {},
                        fingerprint=fp,
                        now=now_utc,
                        env_cluster=env_cluster,
                        bucket_hours=4,
                    )
                    msg_id = compute_msg_id_from_dedup_key(dedup)

                if msg_id in seen_keys:
                    skipped_duplicate += 1
                    continue
                seen_keys.add(msg_id)

                job = AlertJob(alert=raw, time_window=time_window, parent_status=parent_status_s)
                await client.enqueue(job, msg_id=msg_id)
                enqueued += 1
            except Exception:
                errors += 1
                logger.exception("Error enqueueing webhook alert job")
                continue

        return JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "mode": "enqueue",
                "received": len(alerts_list),
                "enqueued": enqueued,
                "skipped_resolved": skipped_resolved,
                "skipped_allowlist": skipped_allowlist,
                "skipped_duplicate": skipped_duplicate,
                "errors": errors,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error processing webhook request")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


def run(host: str = "0.0.0.0", port: int = 8080) -> None:
    import uvicorn

    # Configure logging for the application
    log_level = os.getenv("LOG_LEVEL", "info").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    # Map Python logging levels to uvicorn log levels
    uvicorn_log_level = (
        log_level.lower() if log_level.lower() in ["critical", "error", "warning", "info", "debug", "trace"] else "info"
    )

    logger.info("Starting webhook server on %s:%d (log_level=%s)", host, port, log_level)
    uvicorn.run(app, host=host, port=port, log_level=uvicorn_log_level)
