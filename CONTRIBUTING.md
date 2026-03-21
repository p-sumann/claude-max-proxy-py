# Contributing to cmappy

Thanks for your interest in contributing! Here's how to get started.

## Development setup

```bash
git clone https://github.com/p-sumann/claude-max-proxy-py.git
cd claude-max-proxy
uv sync --dev
```

Run locally with `uv run cmappy`. Run tests with `uv run pytest`. Lint with `uv run ruff check .`.

## Pull requests

1. Fork the repo and create a branch from `main`.
2. Keep PRs focused — one feature or fix per PR.
3. Add or update tests if your change affects behavior.
4. Run `uv run ruff check . && uv run pytest` before pushing.
5. Write a clear PR description explaining **what** changed and **why**.
6. Link any related issues (e.g., `Closes #42`).

### PR checklist

- [ ] Code passes `ruff check .` with no errors
- [ ] Tests pass (`uv run pytest`)
- [ ] New features include tests
- [ ] README updated if needed

## Issues

- **Bug reports**: Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include steps to reproduce, expected vs actual behavior, and your environment.
- **Feature requests**: Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md). Describe the problem you're solving and your proposed solution.

## Code style

- Python 3.10+ with type hints
- Formatted and linted with [ruff](https://docs.astral.sh/ruff/)
- Line length: 100 characters
- Docstrings on all public functions and classes

## Commit messages

Use clear, concise commit messages. Prefix with the area of change when helpful:

```
fix: handle empty stderr on CLI timeout
feat: add --timeout flag to CLI
docs: update README with new model IDs
```

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
