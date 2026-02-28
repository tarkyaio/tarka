# Contributing to Tarka

Thank you for your interest in contributing to Tarka! This guide will help you get started.

## Development Setup

### Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/) for dependency management
- Docker (for local development services and integration tests)
- Node.js 20+ (for UI development and e2e tests)

### Getting Started

```bash
# Clone the repo
git clone https://github.com/tarkyaio/tarka.git
cd tarka

# Install Python dependencies
poetry install

# Run unit tests to verify your setup
make test
```

See the [Local Development Guide](docs/guides/local-development.md) for running the full stack locally.

## Making Changes

### Workflow

1. **Fork** the repository and create a branch from `main`
2. **Make your changes** with clear, focused commits
3. **Add tests** for any new functionality
4. **Run the test suite** to ensure nothing is broken
5. **Submit a pull request** against `main`

### Running Tests

```bash
# Unit tests (fast, no external dependencies)
make test

# Full CI suite (pre-commit + unit + integration + e2e)
make test-ci

# Coverage report
make coverage
```

### Code Style

This project uses automated formatting and linting. Run before committing:

```bash
# Format code
make format

# Check formatting without modifying files
make format-check

# Run all pre-commit hooks
make pre-commit
```

Tools configured in `pyproject.toml`:
- **Black** (line length: 120) for code formatting
- **isort** (Black profile) for import sorting
- **flake8** (line length: 120) for linting
- **mypy** (strict mode) for type checking

### Commit Messages

Write clear commit messages that explain **why** a change was made:

```
feat(playbooks): add node-pressure playbook for disk/memory pressure alerts

fix(diagnostics): correct confidence score when multiple hypotheses match

docs: update quickstart guide with LLM provider setup
```

Use conventional prefixes: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`.

## What to Contribute

### Good First Issues

Look for issues labeled `good first issue` in the issue tracker.

### Areas of Interest

- **New playbooks** for additional alert types (see [Adding Playbooks](docs/guides/extending-playbooks.md))
- **New diagnostic modules** for failure mode detection
- **Provider integrations** (e.g., Datadog, Grafana Loki)
- **Documentation** improvements and examples
- **Bug fixes** and test coverage

### Adding a Playbook

1. Create a file in `agent/playbooks/`
2. Implement evidence collection logic
3. Register in `agent/playbooks/__init__.py`
4. Add tests in `tests/`
5. Document in `docs/`

### Adding a Diagnostic Module

1. Create a module in `agent/diagnostics/`
2. Inherit from `DiagnosticModule` base class
3. Implement `applicable()` and `diagnose()` methods
4. Register in `agent/diagnostics/registry.py`
5. Add unit tests verifying confidence scoring

## Pull Request Guidelines

- Keep PRs focused on a single change
- Include tests for new functionality
- Update documentation if your change affects user-facing behavior
- Ensure `make test-ci` passes before requesting review
- Link related issues in the PR description

## Developer Certificate of Origin

By contributing to this project, you agree that your contributions are your own original work and that you have the right to submit them under the project's Apache 2.0 license. We recommend (but do not require) adding a `Signed-off-by` line to your commits:

```bash
git commit -s -m "feat: add new playbook"
```

## Getting Help

- Open an issue for bugs or feature requests
- Start a discussion for questions about architecture or approach
- Read the [documentation](docs/README.md) for detailed guides

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
