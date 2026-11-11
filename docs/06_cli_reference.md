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

# List only features whose removal Viessmann has announced
vi-client list-features --deprecated
```
JSON output writes one parseable document to standard output; setup diagnostics
and warnings are written to standard error.

`--deprecated` lists only features that carry a removal notice and appends the
notice to each line, the removal date first and the API's info text after it.
The header counts the listed features. The option combines with `--enabled`,
`--values`, and `--json`:

```text
Found 29 Features for device 0:
- heating.scop.total (removal 2024-09-15: replaced by heating.spf.total)
- heating.dhw.temperature.levels.min (removal 2027-03-31: Not used anymore)
```

With `--values` the notice follows the value; a missing date or info text is
left out. `--deprecated --json` prints the filtered feature names. With
`--json --values`, every document carries the key `deprecation`, with and
without `--deprecated`, in the shape `get-feature --json` uses: an object with
`removal_date` and `info`, or `null` for a feature without a notice. A device
without any notice prints a count of zero, or an empty JSON list, and exits
successfully. Without `--deprecated` the text output is unchanged.

A deprecated API feature that reports no properties has no flat feature and is
therefore not listed; devices report retired features that way, usually as
disabled.

*Note: This auto-detects the first device. You can specify `--installation-id`, `--gateway-serial`, and `--device-id` if needed. A numeric installation ID matches the API's string identifier.*
*Note: For `heating.power.consumption.{cooling,dhw,heating,total}`, the flattened feature list may include the synthetic aliases `...currentDay` and `...currentYear` per base feature, mirroring the first entry of the `day` and `year` series. `total.currentDay` passes the API value `total.day[0]` through unchanged; the API can let it lag behind `heating.currentDay` + `dhw.currentDay` for the running day. `currentMonth` aliases are not generated.*

## 4. Fetch Feature Details
Get the current value of a specific feature. The name may be a feature name or
an API feature name; an API feature name prints every feature parsed from it.

```bash
vi-client get-feature "heating.sensors.temperature.outside"

# An API feature name prints both heating curve features (shift and slope)
vi-client get-feature "heating.circuits.0.heating.curve"

# Print the feature as JSON; several matches print one JSON array, and setup
# messages go to standard error
vi-client get-feature "heating.sensors.temperature.outside" --json
```

With `--json`, each document carries `name`, `value`, and `unit`, plus
`constraints` with the `FeatureConstraints` rules (or `null`) and `command`
with `{"name": ..., "parameter": ...}` (or `null`) for a writable feature. A
boolean feature written through an on/off command pair prints `command` as
`{"on": ..., "off": ...}` with both command names, and the text mode prints
`Writable via commands: enable (on), disable (off)`. The
command URI is not printed because it contains installation, gateway, and
device IDs. Every document also carries `deprecation`: `null`, or for a
feature whose API feature reports a removal notice an object with
`removal_date` (ISO date or `null`) and `info` (the API's text or `null`). The JSON mode does not print the command list; it selects the
same features with one feature read.

In text mode, the matched features are followed by the commands their API
features report, with their parameter names, required markers (`*`), and the
parameter constraints the device reports; the listing reads the device
snapshot once after the selection:

```text
- heating.circuits.0.heating.curve.slope: 0.6
  Writable via command: setCurve
  Target param: slope
  Commands of heating.circuits.0.heating.curve:
  - setCurve: slope*, shift*
      slope: number, min: 0.2, max: 3.5, step: 0.1
      shift: number, min: -13, max: 40, step: 1
```

A feature whose API feature reports a removal notice prints it on its own
line with the announced date and the API's info text:

```text
- heating.dhw.sensors.temperature.hotWaterStorage: 48.8 celsius
  Deprecated: removal announced for 2024-09-15 (replaced by heating.dhw.sensors.temperature.dhwCylinder)
```

The CLI only shows the notice. It does not hide, block, or rename a deprecated
feature. `list-features --deprecated` lists every feature with a notice.

## 5. List Installation Events
Read the complete event history an installation returns for a rolling window
with one invocation. The command follows the continuation cursor across
pages; no gateway or device selection is required.

```bash
# Readable summary of the complete window for the last 7 days
vi-client list-events --days 7

# Winter-length investigation over a full year, with a larger page size
vi-client list-events --installation-id 123456 --days 365 --limit 100

# One JSON document with full events and pagination metadata
vi-client list-events --days 365 --json
```

The readable summary keeps the order the provider returned the events in and
prints a UTC date heading whenever the date changes, so dates can repeat when
the history interleaves them. Every event line states the UTC time of the
event explicitly. Timestamps with a non-UTC offset are converted to UTC before
the heading and time are derived; a missing or unparsable timestamp is
reported as `Unknown date` / `time unknown` without an invented value. Long
content wraps onto indented continuation lines instead of truncating —
wrapping preserves every character, including spaces inside parameter values —
so complete command parameters and body values stay visible:

```text
Found 3 event(s) for installation 1234567 (last 7 days):

2026-09-20 (UTC)
- 10:15:30 UTC feature-changed
    heating.dhw.temperature.main
    command: setTargetTemperature
    parameters: {"temperature": 55}

2026-09-19 (UTC)
- 22:41:03 UTC gateway-online
    ONLINE

2026-09-18 (UTC)
- 08:02:10 UTC device-message-status
    code: S.134
    ACTIVE
    device: 17, model: 67
    description: Burner fault

Earliest event: 2026-09-18T08:02:10.500Z; latest event: 2026-09-20T10:15:30.000Z
Pagination completed after 2 page(s); this does not confirm how far back the provider retained events
```

Event bodies use the descriptions the API already supplies; no code-description
catalog is consulted and no operating-state timeline is derived:

- `feature-changed` events print the complete feature name, `command: <name>`
  when the command is known, and `parameters: <json>` with all supplied command
  parameters. Parameters stay visible even when the command name is absent.
- `gateway-online` events print `ONLINE` or `OFFLINE` according to the boolean
  `online` value in the body; the provider event type can still be
  `gateway-online` when `online` is `false`. A missing or non-boolean value is
  never mapped to a state.
- `device-message-status` events print the original code, `ACTIVE` for a boolean
  `active=true` or `ENDED` for a boolean `active=false`, and the available
  device and model identifiers and equipment type. These labels describe the
  transition reported at the event timestamp, not the device's current state.
  An `errorDescription` is printed only when it adds information beyond the
  code; a description that merely repeats the code is omitted.
- Any other event body — including `null`, scalars, lists, unknown objects,
  and bodies of unknown event types that happen to contain feature-shaped
  fields — is printed as its complete JSON representation.

When events come from more than one gateway, each event line carries a
`gateway <serial>` label, or `gateway unknown` when the provider did not report
one; with a single gateway the label is omitted. After the events it reports
the earliest and latest event timestamps when events were returned, and the
pagination outcome: either `Pagination completed after N page(s)` or
`Stopped at the safety limit of N page(s)` with the cursor that would
continue the traversal. A completed traversal reports that the provider
returned no further page; it does not confirm how far back the provider
retained events. `--json` writes exactly one document to standard output and
remains the way to inspect all original event fields and technical metadata:

```json
{
  "installationId": "...",
  "events": ["... full provider fields, including body ..."],
  "eventCount": 123,
  "earliestEventTimestamp": "2025-10-14T09:00:00.000Z",
  "latestEventTimestamp": "2026-09-26T08:14:02.000Z",
  "pagesFetched": 3,
  "paginationComplete": true,
  "nextCursor": null
}
```

`paginationComplete` is `false` and `nextCursor` carries the remaining cursor
when the safety limit stopped the traversal. Interpret
`earliestEventTimestamp` as the oldest event the provider returned for this
request, not as proof that the entire requested window was retained: the
provider's retention period is not publicly confirmed, and exhausting the
cursor only proves that all pages returned for this request were read.
Setup diagnostics stay on standard error. Without `--installation-id` the
first installation of the account is used.

*Note: `--days` is required and must be positive; it is never hardcoded, so
`--days 365` requests a rolling year. `--limit` accepts 1 to 1000 and applies
to every page. `--max-pages` bounds how many pages the traversal fetches
(default 50); when the limit is reached with a cursor remaining, the output
marks the result incomplete.*

The command works offline with a bundled fixture that models a two-page
window (a full first page with a cursor and an empty final page):

```bash
vi-client list-events --fixture-device Vitodens200W --days 7 --json

# Limit-hit path offline: stop after the first fixture page
vi-client list-events --fixture-device Vitodens200W --days 7 --max-pages 1 --json
```

## 6. Discover Writable Features (Control)
List all features that can be changed, including their parameters and constraints.

```bash
vi-client list-writable
```

Example output:

```text
- heating.circuits.0.heating.curve.slope
    Param:   slope (via setCurve)
    Constraints: min: 0.2, max: 3.5, step: 0.1

- heating.dhw.pumps.circulation.schedule
    Param:   newSchedule (via setSchedule)
    Schedule rules: max_entries: 4, modes: ('on',), resolution: 10, overlap_allowed: False, default_mode: off

- heating.dhw.hygiene.enabled
    Commands: enable (on), disable (off)
```

A boolean feature that the device changes through an on/off command pair lists
both commands instead of a parameter.

`Schedule rules` lists only the rules the device reports for a schedule.

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

A boolean feature written through an on/off command pair takes `true` and
`false` like any other boolean; the CLI then sends the on or the off command:

```bash
vi-client set heating.dhw.hygiene.enabled true
```

A schedule takes the whole weekly plan as one JSON object, in the same shape
that `get-feature` reads. The plan must contain all seven days; a day without
slots is an empty list:

```bash
vi-client set heating.dhw.pumps.circulation.schedule \
  '{"mon": [{"start": "06:00", "end": "22:00", "mode": "on", "position": 0}],
    "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []}'
```

The CLI checks the plan against the rules the device reports (slots per day,
modes, time grid, overlaps) before sending it.

On success, `set` prints the success line. When the API or device rejects the
command, the CLI prints the failure line with the rejection details and exits
non-zero:

```text
✅ Success!
```

```text
❌ Failed!
Message: Device communication error
Reason: COMMAND_NOT_EXECUTABLE
```

## 8. Advanced: Run a Command of an API Feature
If you need to run a command with multiple parameters at once (rare), you can use `exec`.

```bash
vi-client exec heating.circuits.0.heating.curve setCurve slope=1.4 shift=0
```

The first argument is an API feature name, and `<command-name>` is any command
that API feature reports — it does not have to be the command bound to a
feature, and it does not have to be executable at the moment of the write;
`exec` sends it and the API decides. A flat feature name such as
`heating.circuits.0.heating.curve.slope` is not accepted: the error names the
API features that report commands with a similar name.

Features that only a command without the `set` prefix takes as a parameter
are read-only and are not listed by `list-writable`: the holiday dates, the
daylight saving dates on devices without `setActive`, and the hygiene trigger
time. `set` on one of them prints an error that names the commands of its API
feature. Run the command with `exec` and supply every parameter:

```bash
vi-client exec heating.operating.programs.holiday schedule start=2026-12-24 end=2026-12-31
```

`exec` validates every supplied parameter against the command's reported
parameters and constraints and sends them exactly as supplied. It does not add
dependent values or update a device snapshot; use `set` for the normal
single-feature workflow. A rejection prints the failure line with
`Message:` and `Reason:` and exits non-zero, like `set`.

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

### Export a Device as a Fixture

`dump-device` prints the selected device's features as one anonymized JSON
document, the format a new fixture is added from. It selects the device like
the other commands and prints setup messages to standard error, so the output
can be redirected into a file:

```bash
vi-client dump-device > export.json
vi-client dump-device --installation-id 123456 --device-id 1 > export.json
```

The export always reads every feature, including disabled and not-ready ones,
and redacts secrets, address data, installation IDs, gateway and device serials,
and coordinates. Free text, such as circuit names, is kept; check the file
before sharing it. See `export_device_fixture` in the
[Client Reference](05_client_reference.md).

### Compare Device Features

`diff-features` compares the API features of two sources to show new or
changed features, for example after a firmware update. Each side is
`fixture:<name>` for a bundled fixture, `live` for the selected device
(exported like `dump-device`), or the path of a fixture file or export:

```bash
# A new export against the bundled fixture of the same device
vi-client diff-features fixture:Vitocal250A live
vi-client diff-features fixture:Vitocal250A export.json

# Two bundled fixtures
vi-client diff-features fixture:Vitocal222S fixture:Vitocal250A
```

Values are not compared. The report has up to four sections:

*   **Added** and **Removed**: features that exist on one side only.
*   **Structure changed**: properties (name, type, unit), commands, and
    command parameters (type, required flag, constraints such as `min`, `max`,
    or `enum`), and the `deprecated` notice. Properties and commands are
    compared only when the feature is enabled on both sides, because the API
    returns a disabled feature without them.
*   **State changed**: `isEnabled`, `isReady`, and each command's
    `isExecutable`.

```text
Structure changed (1):
  heating.dhw.temperature.main
    setTargetTemperature.temperature max: 60 → 65
```

Comparing different devices also works, but then many differences come from
the installation, such as the number of heating circuits. The command exits
with 0 after a comparison, with or without differences, and with 1 when a
source cannot be read.

## Debug Logging

Every command accepts `--verbose` to enable debug logging for the CLI and the
library:

```bash
vi-client list-devices --verbose
vi-client list-features --fixture-device Vitocal250A --verbose
```

Without the flag, the CLI logs informational messages only. With `--verbose`,
debug output from the library workflows (feature fetches, device refreshes,
token refreshes, fallback strategies) is printed as well. Library log records use the
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
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `FeatureConstraints`, `Device`, and command outcomes.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
