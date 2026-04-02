# CLI Reference

The package includes a command-line interface `vi-client` for testing authentication and exploring the API.

> **Note**: Tokens are saved to `tokens.json` in your current directory. Do not commit this file!

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

# List only enabled features (names only)
vi-client list-features --enabled

# List enabled features WITH values - THIS IS THE MOST USEFUL COMMAND!
vi-client list-features --enabled --values

# Output as JSON
vi-client list-features --json
```
*Note: This auto-detects the first device. You can specify `--gateway-serial` and `--device-id` if needed.*
*Note: For `heating.power.consumption.{cooling,dhw,heating,total}`, the flattened feature list may include one synthetic alias feature per base feature: `...currentYear`. Additional synthetic `currentDay` and `currentMonth` aliases are no longer generated.*

## 4. Fetch Feature Details
Get the current value of a specific feature.

```bash
vi-client get-feature "heating.sensors.temperature.outside"
```

## 5. Discover Writable Features (Control)
List all features that can be changed, including their parameters and constraints.

```bash
vi-client list-writable
# Output example:
# Feature: heating.circuits.0.heating.curve.slope
#   Command: setCurve, Param: slope
#   Constraints: min=0.2, max=3.5, step=0.1
```

## 6. Set Feature Value (Write)
Set a new value for a specific feature.

```bash
# Set heating curve slope to 1.4
vi-client set heating.circuits.0.heating.curve.slope 1.4

# Set operating mode
vi-client set heating.circuits.0.operating.modes.active heating
```
The CLI automatically handles type conversion (string "1.4" -> float 1.4) and validation against constraints.

## 7. Advanced: Execute Raw Command
If you need to execute a command with multiple parameters at once (rare), you can use `exec`.

```bash
vi-client exec heating.circuits.0.heating.curve setCurve slope=1.4 shift=0
```

## 8. Get Consumption (Analytics)
Fetch gas/electricity consumption (summary per day/week/month/year).

- **Arguments**:
  - `--metric`: `summary` (default), `total`, `heating`, `dhw`

```bash
vi-client get-consumption --metric summary
```
*   Returns features prefixed with `analytics.` (e.g. `analytics.heating.power.consumption.total`).
*   Data is fetched from the Viessmann Analytics API (not live data).

## 9. Mock Devices (Offline Mode)
The client includes sample data for various devices, allowing you to test integration logic without a real account.

```bash
# List available mock devices
vi-client list-mock-devices

# Use a mock device to list its features
vi-client list-features --mock-device Vitocal250A --values
```

## SSL Certificate Issues

If you encounter SSL errors (e.g., `CERTIFICATE_VERIFY_FAILED`), this is often due to:
1.  **macOS Python**: Your Python environment is missing root certificates.
2.  **Corporate Networks**: Proxies like Zscaler intercepting traffic.

You can use the `--insecure` flag to bypass verification:

```bash
vi-client list-devices --insecure
vi-client get-feature "heating.circuits.0" --insecure
```

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `Device`, and `Command`.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
