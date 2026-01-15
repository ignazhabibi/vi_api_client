# CLI Reference

The package includes a command-line interface `vi-client` for testing authentication and exploring the API.

> **Note**: Tokens are saved to `tokens.json` in your current directory. Do not commit this file!

## 1. Login
Initiate the OAuth2 flow. You need your Client ID from the Viessmann Developer Portal.

```bash
vi-client login --client-id <YOUR_CLIENT_ID>
# Or use environment variable:
# export VIESSMANN_CLIENT_ID=<YOUR_CLIENT_ID>
# vi-client login
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

# List enabled features WITH values
vi-client list-features --enabled --values

# Output as JSON
vi-client list-features --json
```
*Note: This auto-detects the first device. You can specify `--gateway-serial` and `--device-id` if needed.*

## 4. Fetch Feature Details
Get the current value of a specific feature.

```bash
vi-client get-feature "heating.sensors.temperature.outside"
```

## 5. Discover Commands (Control)
List all features that accept commands, including their parameters and constraints.

```bash
vi-client list-commands
# Output example:
# Feature: heating.circuits.0.heating.curve
#   Command: setCurve
#     - slope* (number) [min=0.2, max=3.5, step=0.1]
#     - shift* (number) [min=-13, max=40, step=1]
```

## 6. Execute Commands (Write)
Execute a command on a feature (e.g. setting heating curve).

```bash
# Easy key=value inputs (auto-converted to numbers/bools)
vi-client exec heating.circuits.0.heating.curve setCurve slope=1.4 shift=0

# The tool automatically validates your input before sending:
# > ValueError: Parameter 'slope' (5.0) is greater than maximum (3.5)
```

## 7. Get Consumption (Analytics)
Fetch gas/electricity consumption (summary per day/week/month/year).

- **Arguments**:
  - `--metric`: `summary` (default), `total`, `heating`, `dhw`

```bash
vi-client get-consumption --metric summary
```
*   Returns flattened features prefixed with `analytics.` (e.g. `analytics.heating.power.consumption.total`).
*   Data is fetched from the Viessmann Analytics API (not live data).

## 8. Mock Devices (Offline Mode)
The client includes sample data for various devices, allowing you to test integration logic without a real account.

```bash
# List available mock devices
vi-client list-mock-devices

# Use a mock device to list its features
vi-client list-features --mock-device Vitodens200W --values
```

## Corporate Proxy / SSL Issues
If you are testing from a corporate network that intercepts SSL (e.g., Zscaler), you may encounter certificate errors. Use the `--insecure` flag to bypass verification:

```bash
vi-client list-devices --insecure
vi-client get-feature "heating.circuits.0" --insecure
```
