# AGENTS.md

## Scope and Priorities

Follow runtime instructions first, then this file, then the repository's configured tooling. Treat implementation and configuration as newer than examples or assumptions in documentation.

Keep changes small and directly related to the request. Do not refactor or clean up unrelated code. State assumptions and tradeoffs when they materially affect the solution.

## Project Context

- This is `vi_api_client`, an asynchronous Python library for the Viessmann Climate Solutions API. Library code is in `src/vi_api_client/`; tests are in `tests/`; user-facing documentation is in `docs/`.
- The library has a flat feature model: use dot-named `Feature` objects from `device.get_feature(...)` rather than navigating raw nested API payloads. Read values from `feature.value` and use `feature.is_writable` to determine whether a feature can be changed.
- `update_device` returns a new `Device`; do not mutate device instances in place. Use `set_feature` for writes rather than constructing raw API payloads.
- `MockViClient` uses the bundled fixtures in `src/vi_api_client/fixtures/` and is the preferred client for offline smoke, CLI, and integration-style tests. Use `aioresponses` for HTTP and OAuth request-flow tests against `ViClient`.
- `vi_climate_devices` and other consumers are separate codebases. Do not edit them or add consumer-specific library behavior without an explicit request. Explain compatibility impact, expected consumer follow-up, and required version bump instead.

## Python and Tests

For Python implementation, test, or review work, read [CONTRIBUTING.md](CONTRIBUTING.md) before starting.

## Development and Local Quality Gate

Prefer matching CI's Python 3.14 baseline locally. The normal development setup and validation commands are:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -c constraints-ci.txt '.[dev]'
ruff check .
ruff format --check .
pyright --pythonpath python
python -m pytest -q
python -m build
```

The quality gate builds both distribution artifacts after linting, formatting,
type checking, and tests. `constraints-ci.txt` defines the CI-tested HTTP-client
and mock combination.

## Git, Pull Requests, and Releases

- `main` is protected. Use short-lived branches and pull requests; never commit or merge directly to `main` without an explicitly confirmed emergency bypass.
- Stage only requested files. Before committing, show the files, summary, and proposed Conventional Commit message; no additional confirmation is needed.
- Run the full local quality gate before proposing a commit or push. Wait for GitHub's `quality-check` job before treating a PR as merge-ready. Squash merge only with explicit authorization.
- After a merge, fast-forward local `main` and delete the confirmed merged local branch.
- For a release, analyze commits since the previous tag, propose the semantic version bump and changelog, and wait for confirmation. Land the version bump through a PR, then create an annotated `vX.Y.Z` tag on the merged `main` commit. Its message becomes the GitHub Release body. A release is complete only after the tag workflow is green.

## Documentation Drift

After changes to architecture, public API, CLI behavior, dependencies, setup, tests or fixtures, CI, GitHub policy, or releases, check `README.md`, `docs/`, `CONTRIBUTING.md`, `AGENTS.md`, `pyproject.toml`, and relevant `.github/workflows/` files. Update affected documentation in the same change, or explicitly state that no update was needed.

## Agent skills

### Issue tracker

Issues and specifications live in this repository's GitHub Issues and are
written in English. See `docs/agents/issue-tracker.md`.

### Triage labels

The canonical triage labels use their default names. See
`docs/agents/triage-labels.md`.

### Domain docs

This repository uses a single-context domain-document layout. See
`docs/agents/domain.md`.
