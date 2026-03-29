---
trigger: always_on
---

# Testing Standards (Library Context)

These rules apply to `tests/` and to changes that introduce or modify fixtures,
mock data, or library-facing test helpers.

## 1. Structure & Organization
- **Mirror Source:** Keep test files close to the module or surface they verify.
    - `src/vi_api_client/api.py` -> `tests/test_api.py`
    - `src/vi_api_client/models.py` -> `tests/test_models.py`
    - CLI-specific behavior -> `tests/test_cli_*.py`
- **Integration-Style Flows:** End-to-end offline workflows may live under
  `tests/integration/`.
- **Fixture Files:** Store raw API snippets in `tests/fixtures/`.
    - Do not keep large JSON blobs inline in Python tests.
    - Use subfolders when it improves clarity, such as `fixtures/analytics/` or
      `fixtures/parsing/`.
- **Bundled Mock Fixtures:** `src/vi_api_client/fixtures/` is the golden data
  source for `MockViClient`.

## 2. Framework & Style
- **Framework:** Use `pytest` exclusively.
- **No Classes:** Use simple functions (`def test_...():`), NEVER use `unittest.TestCase` classes.
- **Naming:** Test files start with `test_`. Test functions start with `test_`.
- **Code Style:** Tests MUST follow all rules from `.agent/rules/python-style.md`:
  - No single-letter variables (`f`, `v`, `d`, `i`, `k`) when a descriptive name
    would materially improve readability.
  - Comments must be full sentences.
  - Use f-strings outside logger calls.
  - Descriptive boolean names should begin with `is_`, `has_`, or `should_`.
- **Async:** Mark async tests explicitly with `@pytest.mark.asyncio`.

## 3. The "Arrange-Act-Assert" Pattern (MANDATORY)
Every test function must follow the **Arrange-Act-Assert** structure with
specific comments.

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
feature = ...

# Act: Parse the feature using flat architecture parser.
result = ...

# Assert: Feature should have correct name, value (5.5°C) and unit.
assert result.value == 5.5
```

1. `# Arrange: [Description]` - Prepare inputs, load fixtures, configure mocks,
   and initialize the class under test.
2. `# Act: [Description]` - Execute the specific method or function being tested.
3. `# Assert: [Description]` - Verify return values, mutations, or raised
   exceptions using native `assert`.

For exception-focused tests, `# Act and assert: ...` is acceptable when splitting
the steps would be artificial.

## 4. Data & Mocking
- **Fixture Loading:** Use shared helpers such as `load_fixture_json` for
  `tests/fixtures/` data.
- **HTTP-Layer Tests:** Use `aioresponses` to mock external API calls against
  the real `ViClient` / auth request flow.
- **CLI and Boundary Tests:** Using `unittest.mock.patch`, `AsyncMock`, and
  `MagicMock` is acceptable for CLI/context tests that patch session setup,
  output handling, or orchestration boundaries.
- **Offline Workflow Tests:** Prefer `MockViClient` for smoke and
  integration-style flows that should exercise realistic flattened feature data
  without live credentials.
- **No Phantom Dependencies:** Do not require `pytest-mock` unless the repo adds
  it intentionally.

## 5. Fixture Realism
- **Integrity Check:** Small JSON snippets in `tests/fixtures/` should correspond
  to structures found in the bundled mock fixtures when they represent the same
  API family.
- **Naming:** Feature names and property structures in fixtures must match the
  actual API contract.
- **Verification:** Use `tests/test_mock_data_integrity.py` when changing mock
  assumptions or bundled fixture behavior.
- **Bundled Fixture Protection:** Do not modify `src/vi_api_client/fixtures/`
  just to satisfy a narrow test expectation unless the mock contract is being
  intentionally updated.

## 6. Example Patterns

### HTTP-Layer Test

```python
import aiohttp
import pytest
from aioresponses import aioresponses
from vi_api_client.api import ViClient
from vi_api_client.auth import AbstractAuth


class MockAuth(AbstractAuth):
    async def async_get_access_token(self) -> str:
        return "mock-token"

@pytest.mark.asyncio
async def test_get_installations(load_fixture_json):
    # Arrange: Load installation fixture and mock the installations endpoint.
    data = load_fixture_json("installations.json")

    with aioresponses() as m:
        m.get("https://example.invalid/installations", payload=data)

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act: Fetch installations through the real client flow.
            installations = await client.get_installations()

            # Assert: Two installation objects should be parsed correctly.
            assert len(installations) == 2
```

### Offline Workflow Test

```python
import pytest

from vi_api_client import MockViClient
from vi_api_client.models import Device


@pytest.mark.asyncio
async def test_mock_workflow_vitocal():
    # Arrange: Create a MockViClient backed by the bundled Vitocal250A fixture.
    client = MockViClient("Vitocal250A")
    device = Device(
        id="0",
        gateway_serial="MOCK_GW",
        installation_id="99999",
        model_id="Vitocal250A",
        device_type="heating",
        status="connected",
    )

    # Act: Fetch all enabled features from the offline mock device.
    features = await client.get_features(device, only_enabled=True)

    # Assert: The compressor outlet temperature feature should be present.
    assert any(
        feature.name == "heating.compressors.0.sensors.temperature.outlet"
        for feature in features
    )
```
