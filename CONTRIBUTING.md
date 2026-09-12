# Contributing

## Python

- Python 3.14+ is the project baseline. Follow the Ruff configuration in `pyproject.toml`; do not duplicate mechanically enforceable rules here.
- Prefer precise types and built-in generics. Avoid expanding `Any` usage; restrict it to genuinely dynamic boundaries such as raw JSON or untyped third-party interfaces.
- Use specific exceptions and EAFP where appropriate. Do not catch bare `Exception` or expose transport-library exceptions from the public client layer when a repository exception is appropriate.
- Use descriptive identifiers. Avoid single-letter locals except for conventional, short-lived uses; name booleans with `is_`, `has_`, or `should_`. Sort collections when their order has no semantic meaning.
- Use `pathlib.Path` for filesystem paths. Keep log messages free of trailing periods and use deferred formatting in logger calls.
- Start every Python file, including test files, with a concise module docstring describing its purpose.

## Documentation and Comments

- Use Google-style docstrings for public classes, functions, and methods. Describe purpose and externally observable behavior. Explain non-obvious design rationale or constraints; do not repeat types from annotations.
- Include a `Raises:` section for exceptions intentionally raised, translated, or relevant to the callable's public contract. Do not list incidental implementation exceptions. Tests do not need docstrings when their names and Arrange-Act-Assert structure make their scenario clear.
- Keep comments meaningful and scenario-specific. Explain reasons and constraints, not code paraphrases or abandoned implementation plans.

## Tests

- Use pytest functions, not `unittest.TestCase` classes. Mark async tests with `@pytest.mark.asyncio` and mirror the source layout where practical.
- Structure each non-trivial test with Arrange-Act-Assert. Add explicit, test-specific `# Arrange:`, `# Act:`, and `# Assert:` comments when phases are not immediately clear, the test has multiple phases or state changes, or fixture and mock setup is substantial. Exception-focused tests may use `# Act and assert:`.
- Prefer native `assert` for values and state, mock assertion helpers for interactions, and `pytest.raises` for expected exceptions.
- Store substantial API payloads in `tests/fixtures/` and load them through shared helpers. Keep fixtures aligned with the real API contract and the bundled bundled product fixtures; inspect `tests/test_fixture_data_integrity.py` when changing fixture assumptions.
- Use `aioresponses` for HTTP and authentication tests against real client request flows. `patch`, `AsyncMock`, and `MagicMock` are appropriate for CLI orchestration boundaries. Prefer `FixtureViClient` for realistic offline smoke and integration-style workflows.
- Inspect fixture or snapshot diffs rather than accepting them blindly. Run the focused test first, then the full quality gate before proposing a commit.
