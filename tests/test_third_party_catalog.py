"""
Unit tests for third-party catalog validation.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def test_third_party_catalog_exists():
    """Third-party catalog file exists."""
    catalog_path = Path("config/third-party-catalog.yaml")
    assert catalog_path.exists()


def test_third_party_catalog_valid_yaml():
    """Third-party catalog is valid YAML."""
    catalog_path = Path("config/third-party-catalog.yaml")

    with open(catalog_path) as f:
        catalog = yaml.safe_load(f)

    assert catalog is not None
    assert "third_party_services" in catalog


def test_third_party_catalog_has_common_services():
    """Third-party catalog includes common infrastructure services."""
    catalog_path = Path("config/third-party-catalog.yaml")

    with open(catalog_path) as f:
        catalog = yaml.safe_load(f)

    services = catalog["third_party_services"]

    # Check for key infrastructure components
    assert "coredns" in services
    assert "cert-manager" in services
    assert "ingress-nginx" in services
    assert "prometheus" in services
    assert "argocd" in services


def test_third_party_catalog_entries_have_required_fields():
    """All catalog entries have required github_repo field."""
    catalog_path = Path("config/third-party-catalog.yaml")

    with open(catalog_path) as f:
        catalog = yaml.safe_load(f)

    services = catalog["third_party_services"]

    for service_name, service_config in services.items():
        assert "github_repo" in service_config, f"Service {service_name} missing github_repo"
        assert "/" in service_config["github_repo"], f"Service {service_name} has invalid repo format"


def test_third_party_catalog_repos_are_mostly_unique():
    """Most repos should be unique (some monorepos are allowed)."""
    catalog_path = Path("config/third-party-catalog.yaml")

    with open(catalog_path) as f:
        catalog = yaml.safe_load(f)

    services = catalog["third_party_services"]
    repos = [svc["github_repo"] for svc in services.values()]

    # Allow some monorepos (e.g., kubernetes/autoscaler has multiple components)
    # But most should be unique
    unique_ratio = len(set(repos)) / len(repos)
    assert unique_ratio > 0.9, f"Too many duplicate repos: {unique_ratio:.2%} unique"


def test_third_party_catalog_optional_fields():
    """Optional fields are properly formatted when present."""
    catalog_path = Path("config/third-party-catalog.yaml")

    with open(catalog_path) as f:
        catalog = yaml.safe_load(f)

    services = catalog["third_party_services"]

    for service_name, service_config in services.items():
        # docs_url should be a valid URL if present
        if "docs_url" in service_config:
            docs_url = service_config["docs_url"]
            assert docs_url.startswith("http://") or docs_url.startswith(
                "https://"
            ), f"Service {service_name} has invalid docs_url: {docs_url}"

        # category should be a string if present
        if "category" in service_config:
            assert isinstance(service_config["category"], str), f"Service {service_name} has non-string category"


def test_third_party_catalog_categories():
    """Catalog uses consistent category values."""
    catalog_path = Path("config/third-party-catalog.yaml")

    with open(catalog_path) as f:
        catalog = yaml.safe_load(f)

    services = catalog["third_party_services"]
    categories = set()

    for service_config in services.values():
        if "category" in service_config:
            categories.add(service_config["category"])

    # Common categories
    expected_categories = {
        "infrastructure",
        "operator",
        "security",
        "observability",
        "cicd",
        "storage",
        "autoscaling",
        "networking",
        "database",
        "serverless",
        "backup",
        "multitenancy",
        "testing",
        "messaging",
    }

    assert categories.issubset(expected_categories), f"Unexpected categories: {categories - expected_categories}"
