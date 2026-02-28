"""Local filesystem storage for development (fallback when S3 not configured)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union


@dataclass
class LocalStorage:
    """Local filesystem storage compatible with S3Storage interface."""

    base_dir: str = "./investigations"

    def __post_init__(self) -> None:
        """Ensure base directory exists."""
        self.base_dir = os.path.abspath(self.base_dir)
        Path(self.base_dir).mkdir(parents=True, exist_ok=True)

    def _path(self, rel_key: str) -> Path:
        """Convert relative key to absolute file path."""
        rel_key = rel_key.lstrip("/")
        return Path(self.base_dir) / rel_key

    def key(self, rel_key: str) -> str:
        """
        Return the storage key (for compatibility with S3Storage interface).

        For local storage, this is just the relative path.
        """
        return rel_key.lstrip("/")

    def exists(self, rel_key: str) -> bool:
        """Return True if the file exists."""
        return self._path(rel_key).exists()

    def head_metadata(self, rel_key: str) -> Tuple[bool, Optional[datetime]]:
        """
        Return (exists, last_modified) for a file.

        Compatible with S3Storage interface.
        """
        path = self._path(rel_key)
        if not path.exists():
            return False, None

        try:
            mtime = path.stat().st_mtime
            return True, datetime.fromtimestamp(mtime)
        except Exception:
            return True, None

    def put_markdown(self, rel_key: str, body: str) -> None:
        """Write markdown content to file."""
        path = self._path(rel_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def put_json(self, rel_key: str, body: Union[str, Dict[str, Any]]) -> None:
        """Write JSON content to file."""
        path = self._path(rel_key)
        path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(body, str):
            payload = body
        else:
            payload = json.dumps(body, sort_keys=True, indent=2)

        path.write_text(payload, encoding="utf-8")
