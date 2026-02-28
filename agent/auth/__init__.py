"""
Authentication helpers for the Console API.

Design goals:
- Provider-agnostic (Google now; GitHub later).
- Server-enforced auth for public deployments.
- Cookie-based session (HttpOnly) for same-origin UI.
"""
