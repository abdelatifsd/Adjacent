# Contributing to Adjacent

Thanks for your interest in contributing! This guide covers the basics.

## Setup

1. **Install [uv](https://docs.astral.sh/uv/getting-started/installation/)** (Python package manager).
2. Clone the repo and install dependencies:

   ```bash
   git clone https://github.com/<org>/adjacent.git
   cd adjacent
   uv sync
   ```

3. Start the development stack (requires Docker):

   ```bash
   make dev
   ```

   See [SETUP_GUIDE.md](SETUP_GUIDE.md) for full environment details.

## Lint & Format

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
make format        # auto-fix lint issues and format code
ruff check .       # lint only (no fixes)
ruff format --check .  # check formatting without changes
```

## Tests

```bash
pytest
```

> **Note:** The test suite is still being built out. If you add new functionality, please include tests where practical.

## Pull Request Conventions

- Keep PRs focused — one logical change per PR.
- Write a clear title and description explaining *why* the change is needed.
- Ensure `ruff check` and `ruff format --check` pass before opening a PR.
- Link any related issues.
