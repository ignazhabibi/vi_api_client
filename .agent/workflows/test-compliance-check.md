---
description: Check and enforce compliance of a test file with testing.md rules and actual library test patterns
---

# Test Compliance Check

**Goal:** Bring a target test file into alignment with `.agent/rules/testing.md`
and the real testing patterns used in this repository.

## Instructions

Act as a strict QA reviewer. Do not change the intended behavior being tested,
but do refactor structure, fixtures, mocking style, and comments when they drift
from repo standards.

## 1. Preparation Phase

1. **Read Rules**: Open `.agent/rules/testing.md`.
2. **Read Target**: Open the target test file and identify which surface it
   tests:
   - HTTP client/auth behavior
   - CLI/context orchestration
   - offline `MockViClient` workflow
   - parsing or model logic

## 2. Analysis Checklist

Check the target file against these criteria:

- **Structure:** Does it use standalone `test_...` functions?
- **Async:** Are async tests marked with `@pytest.mark.asyncio`?
- **AAA Pattern:** Does every non-trivial test use specific `# Arrange`,
  `# Act`, `# Assert` comments?
- **Fixture Placement:** Are large JSON payloads stored in `tests/fixtures/`
  instead of inline literals?
- **Mocking Strategy:** Does the mocking match the surface under test?
  - HTTP/auth tests -> `aioresponses`
  - CLI/context tests -> `patch`, `AsyncMock`, `MagicMock`
  - offline smoke/integration flows -> `MockViClient`
- **Fixture Realism:** If the test models real device payloads, do the structures
  align with `src/vi_api_client/fixtures/`?
- **Style:** Does the file follow `.agent/rules/python-style.md`?

## 3. Remediation Phase

If the file violates any rules, rewrite it to comply.

### Step 3.1: Normalize Structure

- Convert class-based tests into standalone functions.
- Rename unclear tests to `test_<scenario>`.
- Replace generic comments with specific AAA comments.

### Step 3.2: Fix Fixture Strategy

- Move large inline API payloads into `tests/fixtures/`.
- Load them through shared helpers such as `load_fixture_json`.

### Step 3.3: Use the Correct Mocking Layer

- Prefer `aioresponses` for real HTTP client flow tests.
- Prefer `patch` / `AsyncMock` / `MagicMock` for CLI orchestration tests.
- Prefer `MockViClient` for offline end-to-end behavior.

### Step 3.4: Keep Assertions Explicit

- Use direct `assert` statements for the important behavior.
- For exception tests, `# Act and assert: ...` is acceptable.

**Example Pattern:**
```python
# Arrange: Load the installations fixture and mock the endpoint.
data = load_fixture_json("installations.json")

# Act: Fetch installations through the API client.
installations = await client.get_installations()

# Assert: Two installation objects should be parsed.
assert len(installations) == 2
```

## 4. Verification

1. Run the specific file: `python -m pytest path/to/test_file.py -q`
2. If the change touches bundled mock fixtures or fixture assumptions, also run:
   `python -m pytest tests/test_mock_data_integrity.py -q`
3. If the change affects CLI behavior broadly, consider running the relevant CLI
   test file too.
