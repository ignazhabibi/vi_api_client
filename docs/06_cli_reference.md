# CLI Reference

The package includes a command-line interface `vi-client` for testing authentication and exploring the API.

Commands return exit status `0` on success and a non-zero status when the
requested operation fails. This makes the CLI safe to use in shell scripts.

> **Note**: Tokens are saved to `tokens.json` in your current directory. Do not commit this file! Every command accepts `--token-file` to read and write tokens from a different path instead.

## 1. Login
Initiate the OAuth2 flow. You need your Client ID from the Viessmann Developer Portal.

```bash
vi-client login --client-id <YOUR_CLIENT_ID> --redirect-uri <YOUR_REDIRECT_URI>

# Defaults to http://localhost:4200/ if not specified.

# Or use environment variables:
# export VIESSMANN_CLIENT_ID=<YOUR_CLIENT_ID>
# export VIESSMANN_REDIRECT_URI=<YOUR_REDIRECT_URI>
```
Follow the URL, log in, and paste the code back into the terminal.
The client ID and redirect URI used during login are saved with the tokens, so
later commands can reuse them without repeating `--client-id`. Configuration
precedence is command-line arguments, environment variables, the credential
document, and finally the default redirect URI.
If the credential document is malformed, the command exits unsuccessfully and
leaves the file unchanged; repair or remove it before trying again.

## 2. List Devices
View all installations, gateways, and devices available to your account.

```bash
vi-client list-devices
```

## 3. List Features
Feature names vary by device model (e.g., heat pumps vs gas boilers). Use this to see exactly what is available.

```bash
# List all features (names only)
vi-client list-features

# List only enabled and ready features (names only)
vi-client list-features --enabled

# List enabled and ready features with values
vi-client list-features --enabled --values

# Output as JSON
vi-client list-features --json
```
JSON output writes one parseable document to standard output; setup diagnostics
and warnings are written to standard error.

*Note: This auto-detects the first device. You can specify `--installation-id`, `--gateway-serial`, and `--device-id` if needed. A numeric installation ID matches the API's string identifier.*
*Note: For `heating.power.consumption.{cooling,dhw,heating,total}`, the flattened feature list may include one synthetic `...currentYear` alias per base feature. `currentDay` and `currentMonth` aliases are not generated.*

## 4. Fetch Feature Details
Get the current value of a specific feature.

```bash
vi-client get-feature "heating.sensors.temperature.outside"

# Show the raw JSON response instead of the parsed feature
vi-client get-feature "heating.sensors.temperature.outside" --raw
```

## 5. List Installation Events
Read one page of an installation's event history for a rolling window. No
gateway or device selection is required.

```bash
# Readable first-page summary for the last 7 days
vi-client list-events --days 7

# Explicit installation and page size
vi-client list-events --installation-id 123456 --days 30 --limit 100

# One JSON document with full events and pagination metadata
vi-client list-events --days 7 --json
```

The readable summary prints one line per event (`eventTimestamp`,
`eventType`, and the reporting gateway) plus an indented detail line
summarizing the event body when one is present: feature changes report the
`feature`, `command`, and `params`, and any other body shape falls back to
compact JSON (truncated to one line). The continuation cursor is printed when
the provider reported one. `--json` writes exactly one document to standard
output with the shape
`{"installationId": ..., "events": [...], "nextCursor": ...}`; every event
object preserves all provider fields, including the complete `body` and
unknown future fields. Setup diagnostics stay on standard error. Without
`--installation-id` the first installation of the account is used.

*Note: `--days` is required and must be positive. `--limit` accepts 1 to 1000;
without it the provider default applies. Traversing further pages with the
reported cursor is a client-API operation; the CLI intentionally reads only
the first page.*

The command works offline with a bundled fixture:

```bash
vi-client list-events --fixture-device Vitodens200W --days 7 --json
```

## 6. Discover Writable Features (Control)
List all features that can be changed, including their parameters and constraints.

```bash
vi-client list-writable
# Output example:
# Feature: heating.circuits.0.heating.curve.slope
#   Command: setCurve, Param: slope
#   Constraints: min=0.2, max=3.5, step=0.1
```

## 7. Set Feature Value (Write)
Set a new value for a specific feature.

```bash
# Set heating curve slope to 1.4
vi-client set heating.circuits.0.heating.curve.slope 1.4

# Set operating mode
vi-client set heating.circuits.0.operating.modes.active heating
```
The CLI converts values according to the feature's command type: numeric values
become numbers, `true`/`false` become booleans, and text values such as `auto`
or `01` remain strings. It then validates the value against the feature constraints.

## 8. Advanced: Execute an Explicit Command
If you need to execute a command with multiple parameters at once (rare), you can use `exec`.

```bash
vi-client exec heating.circuits.0.heating.curve setCurve slope=1.4 shift=0
```

`exec` sends every supplied parameter as one explicit command. It does not add
dependent values or create a command-updated device snapshot; use `set` for the normal
single-feature workflow.

## 9. Fixture Devices (Offline Mode)
The client includes sample data for various devices, allowing you to test integration logic without a real account.
Fixture mode does not read OAuth credentials or `tokens.json` and never makes network
requests.

```bash
# List available fixture devices
vi-client list-fixture-devices

# Use a fixture device to list its features
vi-client list-features --fixture-device Vitocal250A --values
```

## Debug Logging

Every command accepts `--verbose` to enable debug logging for the CLI and the
library:

```bash
vi-client list-devices --verbose
vi-client list-features --fixture-device Vitocal250A --verbose
```

Without the flag, the CLI logs informational messages only. With `--verbose`,
debug output from the library workflows (feature fetches, hydration, token
refreshes, fallback strategies) is printed as well. Library log records use the
`vi_api_client` logger namespace, so embedding applications can filter or
configure them independently of the CLI.

## SSL Certificate Issues

If you encounter SSL errors (e.g., `CERTIFICATE_VERIFY_FAILED`), this is often due to:
1.  **macOS Python**: Your Python environment is missing root certificates.
2.  **Corporate Networks**: Proxies like Zscaler intercepting traffic.

You can use the `--insecure` flag to bypass verification:

```bash
vi-client list-devices --insecure
vi-client get-feature "heating.circuits.0" --insecure
```

The CLI creates its HTTP session before constructing `OAuth` and injects that
session into the auth provider. Normal commands verify TLS; `--insecure`
explicitly disables verification and emits a warning.

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `FeatureControl`, `Device`, and command results.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
