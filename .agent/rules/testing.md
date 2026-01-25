---
trigger: always_on
---

# Testing Standards (Library Context)

These rules apply strictly to the `tests/` directory.

## 1. Structure & Organization
- **Mirror Source:** The test structure must mirror the source code structure.
    - `src/lib/client.py` -> `tests/test_client.py`
    - `src/lib/models.py` -> `tests/test_models.py`
- **Fixtures Directory:**
    - Store all raw JSON/API responses in `tests/fixtures/`.
    - Do NOT put large JSON blobs inside python files.
    - Use subfolders if necessary (e.g., `fixtures/auth/`, `fixtures/devices/`).

## 2. Framework & Style
- **Framework:** Use `pytest` exclusively.
- **No Classes:** Use simple functions (`def test_...():`), NEVER use `unittest.TestCase` classes.
- **Naming:** Test files start with `test_`. Test functions start with `test_`.
- **Code Style:** Tests MUST follow all rules from `.agent/rules/python-style.md`:
  - No single-letter variables (`f`, `v`, `d`, `i`) - use descriptive names
  - Comments must be full sentences (capital letter, period)
  - Use f-strings (except in logger calls)
  - Descriptive boolean names (`is_`, `has_`, `should_`)

## 3. The "Arrange-Act-Assert" Pattern (MANDATORY)
Every test function must follow the **Arrange-Act-Assert** structure.

**CRITICAL: AAA comments must be test-specific, NOT generic.**

❌ **Wrong (Generic):**
```python
# Arrange: Prepare test data and fixtures.
# Act: Execute the function being tested.
# Assert: Verify the results match expectations.
```

✅ **Right (Specific):**
```python
# Arrange: Load fixture for simple temperature sensor value.
# Act: Parse the feature using flat architecture parser.
# Assert: Feature should have correct name, value (5.5°C) and unit.
```

Visually separate these sections with comments if the test is longer than 5 lines.

1.  **Arrange:** Prepare inputs, load fixtures, configure mocks (`respx`), and initialize the class under test.
2.  **Act:** Execute the specific method or function being tested.
3.  **Assert:** Verify the results using native `assert`. Check return values and side effects.

## 4. Data & Mocking
- **Loading Data:** Use a `load_fixture_json` fixture (from `conftest.py`) to read JSON files.
- **HTTP Mocking:** Use `respx` to mock external API calls.
    - Configure `respx` to return the data loaded from `fixtures/`.
- **General Mocking:** Use the `mocker` fixture (`pytest-mock`) instead of `unittest.mock.patch` contexts.
- **Async:** Mark async tests explicitly with `@pytest.mark.asyncio`.

## 5. Fixture Realism
- **Integrity Check:** Small JSON snippets in `tests/fixtures/` MUST correspond to real-world data found in `src/vi_api_client/fixtures/`.
- **Naming:** Feature names and property structures in test fixtures must match the actual API responses exactly.
- **Verification:** Use `tests/test_mock_data_integrity.py` to ensure all test snippets are valid subsets of the real production fixtures.

## 6. One-Shot Example
Follow this exact style for writing tests:

```python
import pytest
from httpx import Response
from my_lib.client import DeviceClient
from my_lib.exceptions import AuthError

@pytest.mark.asyncio
async def test_get_device_details_success(respx_mock, load_fixture_json):
    # --- ARRANGE ---
    # 1. Load data from fixtures (Single Source of Truth)
    api_response = load_fixture_json("devices/plug_details.json")

    # 2. Mock the API endpoint using respx
    respx_mock.get("[https://api.example.com/v1/devices/123](https://api.example.com/v1/devices/123)").mock(
        return_value=Response(200, json=api_response)
    )

    # 3. Initialize the client
    client = DeviceClient(token="abc")

    # --- ACT ---
    device = await client.get_device("123")

    # --- ASSERT ---
    assert device.id == "123"
    assert device.name == "Smart Plug"
    assert device.is_on is True
    # Ensure raw data was parsed correctly into the model
    assert device.voltage == 230.5
