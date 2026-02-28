"""Kubernetes API client for fetching pod information and related read-only context."""

import threading
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

_core_v1_api = None
_apps_v1_api = None
_batch_v1_api = None
_config_loaded = False
_init_lock = threading.Lock()


@runtime_checkable
class K8sProvider(Protocol):
    def get_pod_info(self, pod_name: str, namespace: str) -> Dict[str, Any]: ...

    def get_pod_conditions(self, pod_name: str, namespace: str) -> List[Dict[str, Any]]: ...

    def get_pod_events(self, pod_name: str, namespace: str, limit: int = 20) -> List[Dict[str, Any]]: ...

    def get_pod_owner_chain(self, pod_name: str, namespace: str) -> Dict[str, Any]: ...

    def get_workload_rollout_status(self, *, namespace: str, kind: str, name: str) -> Dict[str, Any]: ...

    def get_service_account_info(self, namespace: str, name: str) -> Dict[str, Any]: ...

    def get_events(
        self,
        *,
        namespace: str,
        resource_type: Optional[str] = None,
        resource_name: Optional[str] = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]: ...

    def list_pods(self, namespace: str, label_selector: Optional[str] = None) -> List[Dict[str, Any]]: ...

    def read_pod_log(
        self,
        pod_name: str,
        namespace: str,
        container: Optional[str] = None,
        previous: bool = False,
        tail_lines: int = 200,
    ) -> Optional[str]: ...


class DefaultK8sProvider:
    def get_pod_info(self, pod_name: str, namespace: str) -> Dict[str, Any]:
        return get_pod_info(pod_name, namespace)

    def get_pod_conditions(self, pod_name: str, namespace: str) -> List[Dict[str, Any]]:
        return get_pod_conditions(pod_name, namespace)

    def get_pod_events(self, pod_name: str, namespace: str, limit: int = 20) -> List[Dict[str, Any]]:
        return get_pod_events(pod_name, namespace, limit=limit)

    def get_pod_owner_chain(self, pod_name: str, namespace: str) -> Dict[str, Any]:
        return get_pod_owner_chain(pod_name, namespace)

    def get_workload_rollout_status(self, *, namespace: str, kind: str, name: str) -> Dict[str, Any]:
        return get_workload_rollout_status(namespace=namespace, kind=kind, name=name)

    def get_service_account_info(self, namespace: str, name: str) -> Dict[str, Any]:
        return get_service_account_info(namespace, name)

    def get_events(
        self,
        *,
        namespace: str,
        resource_type: Optional[str] = None,
        resource_name: Optional[str] = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        return get_events(namespace=namespace, resource_type=resource_type, resource_name=resource_name, limit=limit)

    def list_pods(self, namespace: str, label_selector: Optional[str] = None) -> List[Dict[str, Any]]:
        return list_pods(namespace=namespace, label_selector=label_selector)

    def read_pod_log(
        self,
        pod_name: str,
        namespace: str,
        container: Optional[str] = None,
        previous: bool = False,
        tail_lines: int = 200,
    ) -> Optional[str]:
        return read_pod_log(
            pod_name=pod_name, namespace=namespace, container=container, previous=previous, tail_lines=tail_lines
        )


def get_k8s_provider() -> K8sProvider:
    """Seam for swapping provider implementations later (e.g., MCP-backed)."""
    return DefaultK8sProvider()


def _get_core_v1():
    """
    Return a cached CoreV1Api client.

    We intentionally cache both:
    - config loading (in-cluster or kubeconfig)
    - the API client object

    This avoids repeated expensive initialization on every request/playbook call.
    """
    global _core_v1_api, _config_loaded

    if _core_v1_api is not None:
        return _core_v1_api

    with _init_lock:
        if _core_v1_api is not None:
            return _core_v1_api

        # Import lazily so this project can still run in minimal environments where
        # the kubernetes client (and its transitive deps) aren't available.
        try:
            from kubernetes import client, config
        except Exception as import_err:
            raise Exception(f"Kubernetes client not available: {import_err}")

        if not _config_loaded:
            # Load kubeconfig (works for both in-cluster and local)
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            _config_loaded = True

        _core_v1_api = client.CoreV1Api()
        return _core_v1_api


def _get_apps_v1():
    """Return a cached AppsV1Api client (thread-safe lazy init)."""
    global _apps_v1_api, _config_loaded
    if _apps_v1_api is not None:
        return _apps_v1_api

    with _init_lock:
        if _apps_v1_api is not None:
            return _apps_v1_api
        try:
            from kubernetes import client, config
        except Exception as import_err:
            raise Exception(f"Kubernetes client not available: {import_err}")

        if not _config_loaded:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            _config_loaded = True

        _apps_v1_api = client.AppsV1Api()
        return _apps_v1_api


def _get_batch_v1():
    """Return a cached BatchV1Api client (thread-safe lazy init)."""
    global _batch_v1_api, _config_loaded
    if _batch_v1_api is not None:
        return _batch_v1_api

    with _init_lock:
        if _batch_v1_api is not None:
            return _batch_v1_api
        try:
            from kubernetes import client, config
        except Exception as import_err:
            raise Exception(f"Kubernetes client not available: {import_err}")

        if not _config_loaded:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            _config_loaded = True

        _batch_v1_api = client.BatchV1Api()
        return _batch_v1_api


def get_pod_info(pod_name: str, namespace: str) -> Dict[str, Any]:
    """
    Fetch pod information from Kubernetes API.

    Args:
        pod_name: Name of the pod
        namespace: Kubernetes namespace

    Returns:
        Dictionary containing pod information (metadata, spec, status, etc.)
    """
    try:
        v1 = _get_core_v1()

        # Fetch pod
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)

        # Extract relevant information
        pod_info = {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "uid": pod.metadata.uid,
            "creation_timestamp": (
                pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None
            ),
            "phase": pod.status.phase,
            "status_reason": getattr(pod.status, "reason", None),
            "status_message": getattr(pod.status, "message", None),
            "node_name": pod.spec.node_name,
            # Auth/image pull wiring (read-only metadata)
            "service_account_name": getattr(pod.spec, "service_account_name", None),
            "image_pull_secrets": [
                getattr(s, "name", None) for s in (getattr(pod.spec, "image_pull_secrets", None) or [])
            ],
            "containers": [],
            "resource_requests": {},
            "resource_limits": {},
            # Best-effort runtime status details (useful for generic triage / default playbook)
            "container_statuses": [],
        }

        # Extract container information and resource requests/limits
        for container in pod.spec.containers:
            container_info = {
                "name": container.name,
                "image": container.image,
            }
            pod_info["containers"].append(container_info)

            # Extract resource requests and limits
            resources = container.resources
            if resources:
                if resources.requests:
                    cpu_request = resources.requests.get("cpu")
                    if cpu_request:
                        pod_info["resource_requests"][container.name] = {"cpu": cpu_request}
                    mem_request = resources.requests.get("memory")
                    if mem_request:
                        pod_info["resource_requests"].setdefault(container.name, {})["memory"] = mem_request

                if resources.limits:
                    cpu_limit = resources.limits.get("cpu")
                    if cpu_limit:
                        pod_info["resource_limits"][container.name] = {"cpu": cpu_limit}
                    mem_limit = resources.limits.get("memory")
                    if mem_limit:
                        pod_info["resource_limits"].setdefault(container.name, {})["memory"] = mem_limit

        # ContainerStatuses: readiness/restarts/state hints (read-only)
        try:
            for cs in pod.status.container_statuses or []:
                state = cs.state.to_dict() if getattr(cs, "state", None) else None
                last_state = cs.last_state.to_dict() if getattr(cs, "last_state", None) else None
                pod_info["container_statuses"].append(
                    {
                        "name": cs.name,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count,
                        "image": cs.image,
                        "image_id": cs.image_id,
                        "container_id": cs.container_id,
                        "started": getattr(cs, "started", None),
                        "state": state,
                        "last_state": last_state,
                    }
                )
        except Exception:
            # Don't fail pod info due to status formatting differences
            pass

        return pod_info

    except Exception as e:
        # If the kubernetes client is installed, preserve ApiException details.
        try:
            from kubernetes.client.rest import ApiException  # type: ignore

            if isinstance(e, ApiException):
                raise Exception(f"Kubernetes API error: {e.reason} - {e.body}")
        except Exception:
            pass
        raise Exception(f"Failed to fetch pod info: {str(e)}")


def get_service_account_info(namespace: str, name: str) -> Dict[str, Any]:
    """
    Read a ServiceAccount (read-only) and return minimal metadata useful for triage.

    NOTE: Per privacy/security policy, we do NOT read Secret contents here.
    """
    if not namespace or not name:
        raise Exception("ServiceAccount name/namespace required")
    try:
        v1 = _get_core_v1()
        sa = v1.read_namespaced_service_account(name=name, namespace=namespace)
        pull_secrets = []
        for s in getattr(sa, "image_pull_secrets", None) or []:
            pull_secrets.append(getattr(s, "name", None))

        # Extract annotations (critical for IRSA/IAM role detection)
        metadata = getattr(sa, "metadata", None)
        annotations = {}
        if metadata:
            raw_annotations = getattr(metadata, "annotations", None)
            if raw_annotations and isinstance(raw_annotations, dict):
                annotations = dict(raw_annotations)

        return {
            "name": getattr(metadata, "name", None) if metadata else None,
            "namespace": getattr(metadata, "namespace", None) if metadata else None,
            "annotations": annotations,  # Include annotations for IAM role extraction
            "image_pull_secrets": [x for x in pull_secrets if x],
            "automount_service_account_token": getattr(sa, "automount_service_account_token", None),
        }
    except Exception as e:
        raise Exception(f"Failed to fetch ServiceAccount: {str(e)}")


def get_pod_conditions(pod_name: str, namespace: str) -> List[Dict[str, Any]]:
    """
    Read pod status conditions (read-only).

    Returns a list of condition dicts:
      {type,status,reason,message,last_transition_time}
    """
    try:
        v1 = _get_core_v1()
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)

        conditions = []
        for c in pod.status.conditions or []:
            conditions.append(
                {
                    "type": c.type,
                    "status": c.status,
                    "reason": getattr(c, "reason", None),
                    "message": getattr(c, "message", None),
                    "last_transition_time": (
                        c.last_transition_time.isoformat() if getattr(c, "last_transition_time", None) else None
                    ),
                }
            )
        return conditions
    except Exception as e:
        raise Exception(f"Failed to fetch pod conditions: {str(e)}")


def get_pod_events(pod_name: str, namespace: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    List recent Kubernetes Events for a Pod (read-only).

    Returns up to `limit` events (most recent first) with:
      {type,reason,message,count,first_timestamp,last_timestamp,event_time,source,reporting_component}
    """
    try:
        v1 = _get_core_v1()

        # Field selector to scope events to this pod.
        field_selector = ",".join(
            [
                "involvedObject.kind=Pod",
                f"involvedObject.name={pod_name}",
            ]
        )

        ev_list = v1.list_namespaced_event(
            namespace=namespace,
            field_selector=field_selector,
        )

        events: List[Dict[str, Any]] = []
        for ev in ev_list.items or []:
            events.append(
                {
                    "type": ev.type,
                    "reason": ev.reason,
                    "message": ev.message,
                    "count": ev.count,
                    "first_timestamp": ev.first_timestamp.isoformat() if getattr(ev, "first_timestamp", None) else None,
                    "last_timestamp": ev.last_timestamp.isoformat() if getattr(ev, "last_timestamp", None) else None,
                    "event_time": ev.event_time.isoformat() if getattr(ev, "event_time", None) else None,
                    "source": getattr(ev, "source", None).to_dict() if getattr(ev, "source", None) else None,
                    "reporting_component": getattr(ev, "reporting_component", None),
                }
            )

        # Sort most recent first (prefer last_timestamp, then event_time, then first_timestamp)
        def _ts_key(e: Dict[str, Any]) -> str:
            return e.get("last_timestamp") or e.get("event_time") or e.get("first_timestamp") or ""

        events.sort(key=_ts_key, reverse=True)
        return events[: max(0, limit)]
    except Exception as e:
        raise Exception(f"Failed to fetch pod events: {str(e)}")


def get_pod_owner_chain(pod_name: str, namespace: str, max_depth: int = 5) -> Dict[str, Any]:
    """
    Resolve the controller/owner chain for a Pod (read-only).

    Common chains:
      Pod -> ReplicaSet -> Deployment
      Pod -> StatefulSet
      Pod -> DaemonSet
      Pod -> Job
    """
    try:
        v1 = _get_core_v1()
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        pod_labels_raw = getattr(getattr(pod, "metadata", None), "labels", None)
        pod_labels: Dict[str, Any] = {}
        if isinstance(pod_labels_raw, dict):
            pod_labels = dict(pod_labels_raw)
        else:
            try:
                pod_labels = dict(pod_labels_raw or {})
            except Exception:
                pod_labels = {}

        def _pick_label(d: Dict[str, Any], keys: List[str]) -> Any:
            for k in keys:
                v = d.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    return v
            return None

        owners: List[Dict[str, Any]] = []

        def _owner_ref_to_dict(ref: Any) -> Dict[str, Any]:
            return {
                "kind": getattr(ref, "kind", None),
                "name": getattr(ref, "name", None),
                "uid": getattr(ref, "uid", None),
                "controller": bool(getattr(ref, "controller", False)),
            }

        def _workload_labels(*, kind: str, name: str) -> Dict[str, Any]:
            """
            Best-effort fetch of workload labels. We intentionally keep this small and
            never raise so owner-chain remains resilient.
            """
            kind_norm = (kind or "").strip()
            if not kind_norm or not name:
                return {}
            try:
                if kind_norm in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"):
                    apps = _get_apps_v1()
                    if kind_norm == "Deployment":
                        obj = apps.read_namespaced_deployment(name=name, namespace=namespace)
                    elif kind_norm == "StatefulSet":
                        obj = apps.read_namespaced_stateful_set(name=name, namespace=namespace)
                    elif kind_norm == "DaemonSet":
                        obj = apps.read_namespaced_daemon_set(name=name, namespace=namespace)
                    else:
                        obj = apps.read_namespaced_replica_set(name=name, namespace=namespace)
                    meta = getattr(obj, "metadata", None)
                    labels = getattr(meta, "labels", None) if meta is not None else None
                    if isinstance(labels, dict):
                        return labels
                    # Some client variants return dict-like objects
                    try:
                        return dict(labels or {})
                    except Exception:
                        return {}
                if kind_norm == "Job":
                    batch = _get_batch_v1()
                    obj = batch.read_namespaced_job(name=name, namespace=namespace)
                    meta = getattr(obj, "metadata", None)
                    labels = getattr(meta, "labels", None) if meta is not None else None
                    if isinstance(labels, dict):
                        return labels
                    try:
                        return dict(labels or {})
                    except Exception:
                        return {}
                return {}
            except Exception:
                return {}

        current: Optional[Tuple[str, str]] = None
        # Prefer controller ownerRef if present
        for ref in pod.metadata.owner_references or []:
            if getattr(ref, "controller", False):
                current = (ref.kind, ref.name)
                owners.append(_owner_ref_to_dict(ref))
                break
        if current is None:
            for ref in pod.metadata.owner_references or []:
                owners.append(_owner_ref_to_dict(ref))
            # No controller ref; return what we have.
            # Keep a minimal subset of pod labels to avoid bloating payloads.
            pod_labels_compact = {
                "team": _pick_label(pod_labels, ["team", "owner", "squad", "app.kubernetes.io/team"]),
                "environment": _pick_label(
                    pod_labels, ["environment", "env", "tf_env", "app.kubernetes.io/environment"]
                ),
            }
            return {
                "namespace": namespace,
                "pod": pod_name,
                "owners": owners,
                "workload": None,
                "pod_labels": pod_labels_compact,
            }

        # Walk up known controller types
        depth = 0
        workload: Optional[Dict[str, str]] = None
        kind, name = current

        while kind and name and depth < max_depth:
            depth += 1

            if kind == "ReplicaSet":
                apps = _get_apps_v1()
                rs = apps.read_namespaced_replica_set(name=name, namespace=namespace)
                # ReplicaSet may be owned by a Deployment
                parent = None
                for ref in rs.metadata.owner_references or []:
                    if getattr(ref, "controller", False):
                        parent = ref
                        owners.append(_owner_ref_to_dict(ref))
                        break
                if parent and parent.kind and parent.name:
                    kind, name = parent.kind, parent.name
                    continue
                rs_name = getattr(getattr(rs, "metadata", None), "name", None) or name
                workload = {
                    "kind": "ReplicaSet",
                    "name": rs_name,
                    "labels": _workload_labels(kind="ReplicaSet", name=rs_name),
                }
                break

            if kind == "Deployment":
                workload = {
                    "kind": "Deployment",
                    "name": name,
                    "labels": _workload_labels(kind="Deployment", name=name),
                }
                break

            if kind == "StatefulSet":
                workload = {
                    "kind": "StatefulSet",
                    "name": name,
                    "labels": _workload_labels(kind="StatefulSet", name=name),
                }
                break

            if kind == "DaemonSet":
                workload = {"kind": "DaemonSet", "name": name, "labels": _workload_labels(kind="DaemonSet", name=name)}
                break

            if kind == "Job":
                workload = {"kind": "Job", "name": name, "labels": _workload_labels(kind="Job", name=name)}
                break

            # Unknown type: stop at current
            workload = {"kind": kind, "name": name, "labels": _workload_labels(kind=kind, name=name)}
            break

        pod_labels_compact = {
            "team": _pick_label(pod_labels, ["team", "owner", "squad", "app.kubernetes.io/team"]),
            "environment": _pick_label(pod_labels, ["environment", "env", "tf_env", "app.kubernetes.io/environment"]),
        }
        return {
            "namespace": namespace,
            "pod": pod_name,
            "owners": owners,
            "workload": workload,
            "pod_labels": pod_labels_compact,
        }
    except Exception as e:
        raise Exception(f"Failed to resolve pod owner chain: {str(e)}")


def get_workload_rollout_status(namespace: str, kind: str, name: str) -> Dict[str, Any]:
    """Fetch rollout/status summary for common workload kinds (read-only)."""
    try:
        apps = _get_apps_v1()
        kind_norm = (kind or "").strip()

        def _conditions(obj: Any) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for c in getattr(getattr(obj, "status", None), "conditions", None) or []:
                out.append(
                    {
                        "type": getattr(c, "type", None),
                        "status": getattr(c, "status", None),
                        "reason": getattr(c, "reason", None),
                        "message": getattr(c, "message", None),
                        "last_update_time": (
                            getattr(c, "last_update_time", None).isoformat()
                            if getattr(c, "last_update_time", None)
                            else None
                        ),
                        "last_transition_time": (
                            getattr(c, "last_transition_time", None).isoformat()
                            if getattr(c, "last_transition_time", None)
                            else None
                        ),
                    }
                )
            return out

        def _images(template: Any) -> List[Dict[str, Any]]:
            imgs: List[Dict[str, Any]] = []
            containers = getattr(getattr(getattr(template, "spec", None), "containers", None), "__iter__", None)
            if containers is None:
                return imgs
            for c in template.spec.containers or []:
                imgs.append({"name": getattr(c, "name", None), "image": getattr(c, "image", None)})
            return imgs

        if kind_norm == "Deployment":
            dep = apps.read_namespaced_deployment(name=name, namespace=namespace)
            return {
                "kind": "Deployment",
                "name": dep.metadata.name,
                "generation": getattr(dep.metadata, "generation", None),
                "observed_generation": getattr(dep.status, "observed_generation", None),
                "replicas": getattr(dep.status, "replicas", None),
                "updated_replicas": getattr(dep.status, "updated_replicas", None),
                "ready_replicas": getattr(dep.status, "ready_replicas", None),
                "available_replicas": getattr(dep.status, "available_replicas", None),
                "unavailable_replicas": getattr(dep.status, "unavailable_replicas", None),
                "revision": (dep.metadata.annotations or {}).get("deployment.kubernetes.io/revision"),
                "images": _images(dep.spec.template),
                "conditions": _conditions(dep),
                "creation_timestamp": (
                    dep.metadata.creation_timestamp.isoformat()
                    if getattr(dep.metadata, "creation_timestamp", None)
                    else None
                ),
            }

        if kind_norm == "StatefulSet":
            sts = apps.read_namespaced_stateful_set(name=name, namespace=namespace)
            return {
                "kind": "StatefulSet",
                "name": sts.metadata.name,
                "generation": getattr(sts.metadata, "generation", None),
                "observed_generation": getattr(sts.status, "observed_generation", None),
                "replicas": getattr(sts.status, "replicas", None),
                "ready_replicas": getattr(sts.status, "ready_replicas", None),
                "current_replicas": getattr(sts.status, "current_replicas", None),
                "updated_replicas": getattr(sts.status, "updated_replicas", None),
                "images": _images(sts.spec.template),
                "conditions": _conditions(sts),
                "creation_timestamp": (
                    sts.metadata.creation_timestamp.isoformat()
                    if getattr(sts.metadata, "creation_timestamp", None)
                    else None
                ),
            }

        if kind_norm == "DaemonSet":
            ds = apps.read_namespaced_daemon_set(name=name, namespace=namespace)
            return {
                "kind": "DaemonSet",
                "name": ds.metadata.name,
                "generation": getattr(ds.metadata, "generation", None),
                "observed_generation": getattr(ds.status, "observed_generation", None),
                "desired_number_scheduled": getattr(ds.status, "desired_number_scheduled", None),
                "current_number_scheduled": getattr(ds.status, "current_number_scheduled", None),
                "number_ready": getattr(ds.status, "number_ready", None),
                "updated_number_scheduled": getattr(ds.status, "updated_number_scheduled", None),
                "number_available": getattr(ds.status, "number_available", None),
                "images": _images(ds.spec.template),
                "conditions": _conditions(ds),
                "creation_timestamp": (
                    ds.metadata.creation_timestamp.isoformat()
                    if getattr(ds.metadata, "creation_timestamp", None)
                    else None
                ),
            }

        if kind_norm == "Job":
            batch = _get_batch_v1()
            job = batch.read_namespaced_job(name=name, namespace=namespace)
            return {
                "kind": "Job",
                "name": job.metadata.name,
                "active": getattr(job.status, "active", None),
                "succeeded": getattr(job.status, "succeeded", None),
                "failed": getattr(job.status, "failed", None),
                "start_time": (
                    getattr(job.status, "start_time", None).isoformat()
                    if getattr(job.status, "start_time", None)
                    else None
                ),
                "completion_time": (
                    getattr(job.status, "completion_time", None).isoformat()
                    if getattr(job.status, "completion_time", None)
                    else None
                ),
                "creation_timestamp": (
                    job.metadata.creation_timestamp.isoformat()
                    if getattr(job.metadata, "creation_timestamp", None)
                    else None
                ),
            }

        return {"kind": kind_norm or "Unknown", "name": name, "note": "unsupported_workload_kind"}
    except Exception as e:
        raise Exception(f"Failed to fetch rollout status: {str(e)}")


def get_events(
    *,
    namespace: str,
    resource_type: Optional[str] = None,
    resource_name: Optional[str] = None,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """
    List recent Kubernetes Events with flexible resource filtering (read-only).

    Args:
        namespace: Kubernetes namespace (required for all except node events)
        resource_type: Optional resource type (pod, deployment, statefulset, daemonset, job, node, namespace)
        resource_name: Optional resource name (if omitted, returns namespace-wide events)
        limit: Maximum number of events to return (default 30)

    Returns:
        List of events (most recent first) with:
          {type, reason, message, count, first_timestamp, last_timestamp, event_time,
           source, reporting_component, involved_object}
    """
    try:
        v1 = _get_core_v1()

        # Build field selector based on resource type and name
        field_selectors = []

        if resource_type and resource_name:
            # Specific resource events (pod, deployment, etc.)
            resource_type_norm = resource_type.strip().lower()

            # Map common resource types to K8s API kinds
            kind_map = {
                "pod": "Pod",
                "deployment": "Deployment",
                "statefulset": "StatefulSet",
                "daemonset": "DaemonSet",
                "job": "Job",
                "replicaset": "ReplicaSet",
                "node": "Node",
                "namespace": "Namespace",
                "service": "Service",
                "configmap": "ConfigMap",
                "secret": "Secret",
                "persistentvolumeclaim": "PersistentVolumeClaim",
                "pvc": "PersistentVolumeClaim",
            }

            kind = kind_map.get(resource_type_norm, resource_type.strip())
            field_selectors.append(f"involvedObject.kind={kind}")
            field_selectors.append(f"involvedObject.name={resource_name}")
        elif resource_type:
            # All events for a resource type (e.g., all pods)
            resource_type_norm = resource_type.strip().lower()
            kind_map = {
                "pod": "Pod",
                "deployment": "Deployment",
                "statefulset": "StatefulSet",
                "daemonset": "DaemonSet",
                "job": "Job",
                "replicaset": "ReplicaSet",
                "node": "Node",
                "namespace": "Namespace",
                "service": "Service",
                "configmap": "ConfigMap",
                "secret": "Secret",
                "persistentvolumeclaim": "PersistentVolumeClaim",
                "pvc": "PersistentVolumeClaim",
            }
            kind = kind_map.get(resource_type_norm, resource_type.strip())
            field_selectors.append(f"involvedObject.kind={kind}")

        # Join field selectors
        field_selector = ",".join(field_selectors) if field_selectors else None

        # Fetch events
        if field_selector:
            ev_list = v1.list_namespaced_event(namespace=namespace, field_selector=field_selector)
        else:
            # Namespace-wide events
            ev_list = v1.list_namespaced_event(namespace=namespace)

        events: List[Dict[str, Any]] = []
        for ev in ev_list.items or []:
            involved_obj = getattr(ev, "involved_object", None)
            events.append(
                {
                    "type": ev.type,
                    "reason": ev.reason,
                    "message": ev.message,
                    "count": ev.count,
                    "first_timestamp": ev.first_timestamp.isoformat() if getattr(ev, "first_timestamp", None) else None,
                    "last_timestamp": ev.last_timestamp.isoformat() if getattr(ev, "last_timestamp", None) else None,
                    "event_time": ev.event_time.isoformat() if getattr(ev, "event_time", None) else None,
                    "source": getattr(ev, "source", None).to_dict() if getattr(ev, "source", None) else None,
                    "reporting_component": getattr(ev, "reporting_component", None),
                    "involved_object": (
                        {
                            "kind": getattr(involved_obj, "kind", None),
                            "name": getattr(involved_obj, "name", None),
                            "namespace": getattr(involved_obj, "namespace", None),
                            "uid": getattr(involved_obj, "uid", None),
                        }
                        if involved_obj
                        else None
                    ),
                }
            )

        # Sort most recent first (prefer last_timestamp, then event_time, then first_timestamp)
        def _ts_key(e: Dict[str, Any]) -> str:
            return e.get("last_timestamp") or e.get("event_time") or e.get("first_timestamp") or ""

        events.sort(key=_ts_key, reverse=True)
        return events[: max(0, limit)]
    except Exception as e:
        raise Exception(f"Failed to fetch events: {str(e)}")


def list_pods(namespace: str, label_selector: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List pods in a namespace with optional label selector filtering (read-only).

    Args:
        namespace: Kubernetes namespace
        label_selector: Optional label selector (e.g., "job-name=my-job", "app=frontend")

    Returns:
        List of pod metadata dicts with:
          {name, namespace, uid, creationTimestamp, labels, phase, ...}
    """
    try:
        v1 = _get_core_v1()

        if label_selector:
            pod_list = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        else:
            pod_list = v1.list_namespaced_pod(namespace=namespace)

        pods: List[Dict[str, Any]] = []
        for pod in pod_list.items or []:
            pods.append(
                {
                    "metadata": {
                        "name": getattr(getattr(pod, "metadata", None), "name", None),
                        "namespace": getattr(getattr(pod, "metadata", None), "namespace", None),
                        "uid": getattr(getattr(pod, "metadata", None), "uid", None),
                        "creationTimestamp": (
                            getattr(getattr(pod, "metadata", None), "creation_timestamp", None).isoformat()
                            if getattr(getattr(pod, "metadata", None), "creation_timestamp", None)
                            else None
                        ),
                        "labels": dict(getattr(getattr(pod, "metadata", None), "labels", None) or {}),
                    },
                    "status": {
                        "phase": getattr(getattr(pod, "status", None), "phase", None),
                    },
                }
            )

        return pods
    except Exception as e:
        raise Exception(f"Failed to list pods: {str(e)}")


def read_pod_log(
    pod_name: str,
    namespace: str,
    container: Optional[str] = None,
    previous: bool = False,
    tail_lines: int = 200,
) -> Optional[str]:
    """Read logs from a pod container (read-only, best-effort).

    Args:
        pod_name: Name of the pod
        namespace: Kubernetes namespace
        container: Container name (optional, defaults to first container)
        previous: If True, read logs from the previous terminated container instance
        tail_lines: Number of lines from the end to return

    Returns:
        Raw log text, or None if logs are unavailable (pod deleted, no previous instance, etc.)
    """
    try:
        v1 = _get_core_v1()
        kwargs = {
            "name": pod_name,
            "namespace": namespace,
            "previous": previous,
            "tail_lines": tail_lines,
        }
        if container:
            kwargs["container"] = container
        return v1.read_namespaced_pod_log(**kwargs)
    except Exception:
        return None
