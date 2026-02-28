"""Pod-not-healthy evidence collector."""

from __future__ import annotations

from agent.collectors.pod_baseline import _require_pod_target, collect_pod_baseline
from agent.core.models import Investigation
from agent.providers.k8s_provider import get_service_account_info


def collect_pod_not_healthy(investigation: Investigation) -> None:
    investigation.target.playbook = "pod_not_healthy"
    target = _require_pod_target(investigation, "pod_not_healthy")
    if target is None:
        return
    collect_pod_baseline(investigation, events_limit=20)

    # Optional: attach image pull diagnostics for ImagePullBackOff/ErrImagePull (deterministic).
    try:
        from agent.image_pull import (
            classify_pull_error,
            ecr_describe_image,
            extract_image_from_message,
            parse_image_ref,
        )

        pod_info = investigation.evidence.k8s.pod_info if isinstance(investigation.evidence.k8s.pod_info, dict) else {}
        container_statuses = pod_info.get("container_statuses") if isinstance(pod_info, dict) else None
        waiting_msg = None
        waiting_reason = None
        waiting_container = None
        if isinstance(container_statuses, list):
            for cs in container_statuses:
                if not isinstance(cs, dict):
                    continue
                state = cs.get("state") if isinstance(cs.get("state"), dict) else {}
                waiting = state.get("waiting") if isinstance(state.get("waiting"), dict) else None
                if not isinstance(waiting, dict):
                    continue
                r = str(waiting.get("reason") or "")
                if r in ("ImagePullBackOff", "ErrImagePull"):
                    waiting_reason = r
                    waiting_container = cs.get("name")
                    waiting_msg = str(waiting.get("message") or "")
                    break

        if waiting_reason:
            # Best-effort image ref: prefer waiting message; fallback to pod spec container image.
            image = extract_image_from_message(waiting_msg or "")
            if not image and isinstance(pod_info, dict):
                for c in pod_info.get("containers") or []:
                    if not isinstance(c, dict):
                        continue
                    if waiting_container and c.get("name") != waiting_container:
                        continue
                    img = c.get("image")
                    if isinstance(img, str) and img.strip():
                        image = img.strip()
                        break

            img_ref = parse_image_ref(image or "")
            bucket, bucket_ev = classify_pull_error(waiting_msg or "")

            ns = investigation.target.namespace or ""
            sa_name = (pod_info.get("service_account_name") if isinstance(pod_info, dict) else None) or None
            sa_info = None
            if sa_name and ns:
                try:
                    sa_info = get_service_account_info(ns, str(sa_name))
                except Exception:
                    sa_info = None

            ecr_check = None
            if img_ref.is_ecr and img_ref.ecr_region and img_ref.repository:
                ecr_check = ecr_describe_image(
                    region=img_ref.ecr_region,
                    repository=img_ref.repository,
                    tag=img_ref.tag,
                    digest=img_ref.digest,
                    registry_id=img_ref.ecr_registry_id,
                )

            investigation.evidence.k8s.image_pull_diagnostics = {
                "container": waiting_container,
                "waiting_reason": waiting_reason,
                "waiting_message": (waiting_msg or "")[:400],
                "error_bucket": bucket,
                "error_evidence": bucket_ev,
                "image": img_ref.raw or (image or None),
                "registry_host": img_ref.registry_host,
                "repo": img_ref.repository,
                "tag": img_ref.tag,
                "digest": img_ref.digest,
                "service_account_name": sa_name,
                "service_account_image_pull_secrets": (
                    (sa_info or {}).get("image_pull_secrets") if isinstance(sa_info, dict) else None
                ),
                "ecr_check": ecr_check,
            }
    except Exception:
        # Never block on optional diagnostics.
        pass


__all__ = ["collect_pod_not_healthy"]
