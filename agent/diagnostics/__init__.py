"""Diagnostic modules (universal failure modes).

This package provides the core abstraction that replaces "playbooks for all errors":
- modules for universal failure modes (K8s-first)
- deterministic hypothesis generation + next tests

Playbooks remain as evidence collectors and can be used as a compatibility layer.
"""

from .base import DiagnosticModule
from .registry import DiagnosticRegistry, get_default_registry

__all__ = ["DiagnosticModule", "DiagnosticRegistry", "get_default_registry"]
