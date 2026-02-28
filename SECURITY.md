# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | Yes                |

## Reporting a Vulnerability

If you discover a security vulnerability in Tarka, please report it privately via email:

**Email**: auti.dinesh3@gmail.com

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You should receive an acknowledgment within 48 hours. We will work with you to understand the issue and coordinate a fix before any public disclosure.

## Security Design

Tarka is designed as **read-only infrastructure tooling**:

- All Kubernetes operations use read-only API calls (`get`, `list`, `watch`)
- All Prometheus/metrics queries are read-only
- The agent never mutates cluster state, scales workloads, or deletes resources
- Chat actions (restart, scale) require explicit policy enablement and user confirmation

This read-only design significantly limits the blast radius of any potential vulnerability.

## Scope

The following are in scope for security reports:
- Authentication/authorization bypass
- Sensitive data exposure in reports or logs
- Injection vulnerabilities in query construction (PromQL, LogQL)
- Dependency vulnerabilities with a viable exploit path

The following are out of scope:
- Vulnerabilities in upstream dependencies without a demonstrated exploit
- Issues requiring physical access to the deployment environment
- Social engineering attacks
