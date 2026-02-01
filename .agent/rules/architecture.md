# Project Architecture & Context

This file defines the architectural foundations and critical context for the `vi_api_client` project. All agents must adhere to these principles.

## 1. Project Identity
- **Name**: `vi_api_client`
- **Purpose**: Asynchronous Python library for the Viessmann Climate Solutions API.
- **Target Audience**: Home Assistant integrations and custom async Python applications.
- **Key Characteristic**: **Flat Architecture** - The complex, deeply nested JSON from Viessmann is flattened into a simple list of `Feature` objects.

## 2. Core Architecture: The "Flat Feature" Model (Use this!)
The most important architectural decision is the flattening of API data.

- **Concept**: Accessing data via flattened feature names.
  - ✅ `feature = device.get_feature("heating.circuits.0.heating.curve.slope")`
  - ✅ `value = feature.value`

### Feature Components
A `Feature` object encapsulates everything needed for a specific datapoint:
- **Name**: Unique string ID (e.g. `heating.circuits.0.operating.programs.active`).
- **Value**: The current state (scalar, boolean, or string).
- **Control**: Usage of `FeatureControl` object for writability (replaces `commands` dict).
  - Contains `min`, `max`, `step`, `options` (for enums).
  - **Action**: Use `feature.is_writable` to check if it can be changed.

## 3. Client & Data Flow
- **Client**: `ViClient(auth)` is the single entry point.
- **Immutability**: `Device` and `Feature` objects are effectively immutable snapshots.
- **Update Cycle**:
  1. `client.get_installations()` -> `client.get_gateways()` -> `client.get_devices()` (Initial Discovery).
  2. `client.update_device(device)` -> Returns a **NEW** `Device` object with fresh features.
  3. **Never** mutate a `Device` object in place. Always replace it.

## 4. Authentication
- **Mechanism**: OAuth2 with PKCE.
- **Persistence**: Tokens are stored in a JSON file (default `tokens.json`).
- **Auto-Renewal**: handled automatically by `aiohttp` interceptors.

## 5. Coding Standards (Strict Compliance)
- **Python Version**: **3.12+** required. Use modern features (`match/case`, `|` for types).
- **Typing**: **Strict**. No `Any`. Use `type[str | int]`, not `Union`. Use `pathlib.Path` not `os.path`.
- **Async**: All I/O is asynchronous (`async/await`).
- **Logging**: Use lazy % formatting. `_LOGGER.info("Value: %s", value)`.

## 6. Testing Strategy
- **Framework**: `pytest` only.
- **Pattern**: **Arrange-Act-Assert (AAA)** (Explicitly commented).
- **Mocking**:
  - **Network**: Use `aioresponses` to mock HTTP calls.
  - **Data**: Load **JSON Fixtures** from `tests/fixtures/`.
  - **No Magic**: Do not use `unittest.mock` for network calls.

## 7. Home Assistant Integration Context
- **Hybrid Discovery**:
  - **Priority 1**: Defined Entities (Mapped manually for perfect naming/icons).
  - **Priority 2**: Automatic Entities (Fallback for unknown features based on type).
- **Coordinator**: Polls `update_device` and stores the `Device` object.

## 8. Directory Structure
- `src/vi_api_client/`: Library source code.
- `tests/`: Tests mirroring source structure.
- `docs/`: Comprehensive markdown documentation.
- `.agent/`: AI Agent rules and workflows.
