"""Tool-using chat (UI-first).

This module provides a policy-gated chat runtime that can:
- read case/run SSOT (analysis_json)
- call safe, read-only tools to gather more evidence
- return a cited, on-call friendly response
"""
