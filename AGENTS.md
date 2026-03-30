# AGENTS.md

This repository keeps its detailed agent guidance in `.agent/`.

Use this file as the bootstrap entry point. The detailed files in `.agent/` are
the canonical source for project-specific rules and workflows.

## Priority and Structure

When working in this repository, use this order:

1. System / developer / tool instructions from the runtime
2. This `AGENTS.md`
3. `.agent/rules/`
4. `.agent/workflows/`

If repository guidance conflicts internally:

- Rules in `.agent/rules/` override workflow convenience steps.
- Newer repository reality overrides stale examples.
- Do not hardcode dependency versions into guidance files unless the exact
  version is genuinely required.

## Always-Relevant Files

Read these first for most non-trivial tasks:

- `.agent/rules/architecture-context.md`
- `.agent/rules/tech-stack.md`
- `.agent/rules/python-style.md`
- `.agent/rules/python-docs.md`
- `.agent/rules/testing.md`
- `.agent/rules/git-workflow.md`
- `.agent/rules/consumer-repo-protection.md`

## Workflow Selection

Pick the matching workflow before making substantial changes:

- Feature work: `.agent/workflows/feature-develop.md`
- Releases and tags: `.agent/workflows/release.md`
- PR submission flow: `.agent/workflows/pr-submit.md`
- Test review / test updates: `.agent/workflows/test-compliance-check.md`
- Rules review / agent guidance cleanup: `.agent/workflows/rules-compliance-check.md`
- Documentation-only work: `.agent/workflows/doc-update.md`

## Self-Contained Repository Documentation

Treat the repository-operational documentation as a self-contained system that
must stay current together:

- `README.md`
- `docs/`
- `AGENTS.md`
- `.agent/rules/`, especially `architecture-context.md` and `tech-stack.md`
- `.agent/workflows/`

After any task that changes repository behavior or expectations, the agent must
perform an explicit documentation drift check before considering the work
complete.

This is mandatory for changes affecting:

- public API contracts, feature model behavior, or command semantics
- CLI commands, arguments, auto-discovery behavior, or example usage
- tech stack or Python/dependency policy
- local setup commands
- testing strategy, mock fixtures, or bundled offline workflows
- CI behavior or required checks
- release flow, versioning rules, or tag/changelog expectations
- GitHub governance such as branch policy or PR requirements

The agent must then do one of the following:

- update the affected documentation in the same task, or
- explicitly state that the documentation was checked and no update is needed

Do not keep durable repository knowledge implicit when the repository guidance
should be updated to reflect it.

## Project Context

- This is the `vi_api_client` repository, an asynchronous Python library for the
  Viessmann Climate Solutions API.
- Main library code lives in `src/vi_api_client/`.
- User-facing reference docs live in `docs/`.
- Tests live in `tests/`.
- Bundled offline mock device fixtures live in `src/vi_api_client/fixtures/`.
- The CLI entrypoint is `vi-client` via `src/vi_api_client/cli.py`.
- The library is consumed by `vi_climate_devices` and other async Python apps.
- Treat consumer repositories as separate codebases. Do not silently edit them
  as part of library work.
- `MockViClient` is the preferred offline client for smoke tests, CLI demos, and
  integration-style workflows.
- `aioresponses` is the default HTTP mocking layer for API/auth tests against
  the real `ViClient` request flow.

## Development Baseline

Prefer matching the current CI interpreter locally when possible.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[dev]'
```

Primary local quality gates:

```bash
ruff check .
ruff format --check .
python -m pytest -q
```

If a change touches packaging metadata, build config, dependency management, or
CLI entry points, validate the package build too:

```bash
python -m build
```

## CI and Release Notes

- CI currently runs via `.github/workflows/ci_cd.yml`.
- The `quality-check` job currently runs on Python 3.14.
- `main` is protected on GitHub. Treat the pull-request path as mandatory
  unless the user explicitly requests an emergency bypass.
- Renovate is enabled via `renovate.json`. Dependency update PRs are expected
  repository traffic and should be reviewed normally.
- The current Renovate policy is PR creation without automerge. Do not assume
  dependency PRs will merge themselves unless the repository config changes.
- Releases are published from `v*` tag runs in GitHub Actions.
- The GitHub Release body is populated from the annotated tag message, so
  release tags must be annotated and should include the changelog body.
- Prefer creating release tags from a clean, up-to-date `main` after the release
  version bump has landed through the normal PR flow.
- A release is not considered complete until the tag workflow is green.

## Practical Agent Notes

- Prefer `rg` / `rg --files` for repo search.
- Use `apply_patch` for manual file edits.
- Keep changes minimal and consistent with existing patterns.
- If rules or workflows look stale, update them as part of the task instead of
  working around them silently.
- End substantial tasks with an explicit repository documentation drift check
  against `README.md`, `docs/`, `AGENTS.md`, `.agent/rules/`, and
  `.agent/workflows/`.
