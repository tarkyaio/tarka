"""Back-compat wrapper: k8s context gatherer now lives in `agent.collectors`."""

from agent.collectors.k8s_context import gather_pod_context

__all__ = ["gather_pod_context"]
