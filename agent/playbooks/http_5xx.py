"""Back-compat wrapper: HTTP 5xx collector now lives in `agent.collectors`."""

from __future__ import annotations

from agent.collectors.http_5xx import collect_http_5xx
from agent.core.models import Investigation


def investigate_http_5xx_playbook(investigation: Investigation) -> None:
    collect_http_5xx(investigation)


__all__ = ["investigate_http_5xx_playbook"]
