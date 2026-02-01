---
description: Refactor the current test file to strictly match `.agent/rules/testing.md
---

# Test Suite Standardization (AAA & Fixtures)

**Goal:** Refactor the current test file to strictly match `.agent/rules/testing.md`.

## Instructions

Act as a Senior QA Engineer. You must not change the logic of what is being tested, but you must completely rewrite **HOW** it is tested.

### Step 1: Framework & Clean-up
1.  **Remove Classes:** Convert any `unittest.TestCase` class into standalone functions.
2.  **Async:** Ensure async tests use `@pytest.mark.asyncio`.
3.  **Naming:** Rename functions to `test_<scenario>` if they don't match.

### Step 2: Data Extraction (The "Fixture Strategy")
*Crucial Step: Move inline data to external files.*
1.  Identify any hardcoded JSON/Dict representing an API response.
2.  **Action:**
    - Create a new JSON file in `tests/fixtures/` (e.g., `tests/fixtures/domain/scenario.json`).
    - Move the data there.
    - In the Python test, replace the data variable with: `data = load_fixture_json("domain/scenario.json")`.
3.  *Fallback:* If you cannot create the file directly, provide the JSON content in a code block and tell me where to save it, but update the Python code as if the file exists.

### Step 3: Mocking & Networking
1.  **Mocking Library:** Use `aioresponses` to mock external API calls.
2.  **Mock Data:** Configure the mock to return the data loaded from the fixture in Step 2.
    - Example: `m.get(url, payload=data)`

### Step 4: Apply AAA Pattern (Formatting)
Refactor the function body to strictly follow the **Arrange-Act-Assert** pattern using **one-liner comments**:

1.  `# Arrange: [Description of setup]`
    - Load fixtures.
    - Setup mocks.
    - Initialize the Class Under Test.
2.  `# Act: [Description of action]`
    - Call the method being tested.
3.  `# Assert: [Description of verification]`
    - Check results (`assert val == expected`).

**Example:**
```python
# Arrange: Load fixture and mock API.
data = ...

# Act: Call the function.
result = await ...

# Assert: Check return value.
assert result == ...
```

## Output
1.  Apply changes to the Python test file.
2.  (If applicable) Create the new JSON fixture files.
3.  Show a bullet list of improvements made.
