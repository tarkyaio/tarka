"""Report generator (investigation-first).

`Investigation` is the single source of truth. Rendering is deterministic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from agent.core.models import Investigation
from agent.report_deterministic import render_deterministic_report


def render_report(investigation: Investigation, *, generated_at: Optional[datetime] = None) -> str:
    """
    Render a Markdown report from a investigation.
    """
    return render_deterministic_report(investigation, generated_at=generated_at)
