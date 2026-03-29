---
trigger: always_on
---

# Library Architecture & Context

This file defines the architectural foundations and critical repository context
for `vi_api_client`. All agents must adhere to these principles.

## 1. Project Identity

- **Name**: `vi_api_client`
- **Purpose**: Asynchronous Python library for the Viessmann Climate Solutions
  API.
- **Primary Consumers**: `vi_climate_devices`, the bundled CLI, and custom async
  Python applications.
- **Key Characteristic**: Flat feature architecture. Deep Viessmann payloads are
  normalized into a simple list of `Feature` objects with dot-notation names.

## 2. Core Architecture: The Flat Feature Model

The most important architectural decision is the flattening of device data.

- Access state through flat feature names, not by traversing raw nested JSON.
- Use `device.get_feature("heating.circuits.0.heating.curve.slope")` for lookup.
- Read the current datapoint from `feature.value`.

### Feature Components

A `Feature` object encapsulates everything needed for a specific datapoint:

- **Name**: Unique string identifier, such as
  `heating.circuits.0.operating.programs.active`.
- **Value**: Current scalar, boolean, string, or structured boundary value.
- **Unit**: Optional API unit string, such as `celsius`.
- **Flags**: `is_enabled` and `is_ready` describe current feature usability.
- **Control**: Optional `FeatureControl` metadata for writeable features.

`FeatureControl` carries the write contract:

- `command_name` and `param_name` identify the command mapping.
- `required_params` and `parent_feature_name` support dependency resolution.
- `min`, `max`, `step`, `options`, and string constraints define validation.
- Use `feature.is_writable` instead of checking implementation details directly.

## 3. Client and Data Flow

`ViClient` is the main entry point.

### Discovery and Reading

- `get_installations()` returns typed `Installation` objects.
- `get_gateways()` returns typed `Gateway` objects.
- `get_devices(installation_id, gateway_serial, include_features=False, only_active_features=False)`
  returns typed `Device` objects and can optionally hydrate them with features.
- `get_features(device, only_enabled=False, feature_names=None)` returns the
  flattened feature list for a device.
- `get_full_installation_status(installation_id, only_enabled=True)` is the
  full discovery helper for installation-wide reads.

### Updates and Writes

- `update_device(device, only_enabled=True)` returns a **new** `Device`
  instance. Do not mutate `Device` objects in place.
- `set_feature(device, feature, target_value)` resolves command dependencies,
  validates the target value, sends the command, and returns
  `(CommandResponse, updated_device)`.
- `get_consumption(device, start_dt, end_dt, metric="summary", resolution="1d")`
  is the analytics entry point.

## 4. Authentication and CLI Context

- Authentication is OAuth2 with PKCE via `OAuth`.
- Token/config persistence is JSON-based and defaults to `tokens.json` in CLI
  workflows.
- The CLI in `src/vi_api_client/cli.py` is not a thin wrapper around raw HTTP.
  It exercises real library behavior such as auth setup, auto-discovery, and
  `MockViClient` support.
- Changes that affect CLI behavior, defaults, or auto-discovery must be checked
  against `README.md` and `docs/06_cli_reference.md`.

## 5. Testing and Fixture Reality

- HTTP-layer client and auth tests should validate the real request flow with
  `aioresponses` and fixtures under `tests/fixtures/`.
- Offline smoke, CLI, or end-to-end style flows may use `MockViClient`.
- Bundled fixtures in `src/vi_api_client/fixtures/` are the golden offline data
  source for `MockViClient`.
- Do not casually rewrite bundled fixtures just to satisfy a narrow test. If the
  fixture contract changes intentionally, update the relevant tests and docs with
  it.
- When changing parsing or fixture assumptions, review
  `tests/test_mock_data_integrity.py` and integration-style tests under
  `tests/integration/`.

## 6. Repository Boundaries

- This repository is the source of truth for the library contract.
- Consumer-repository fixes belong in separate follow-up work unless the user
  explicitly asked for multi-repo coordination.
- Do not add consumer-specific hacks that silently distort the public library
  contract. If a consumer needs an adaptation, surface the contract impact and
  update the library documentation accordingly.

## 7. Directory Structure

- `src/vi_api_client/`: Library source code.
- `src/vi_api_client/fixtures/`: Bundled mock device fixtures.
- `tests/`: Library and integration-style tests.
- `docs/`: User-facing markdown documentation.
- `.agent/`: Agent rules and workflows.
