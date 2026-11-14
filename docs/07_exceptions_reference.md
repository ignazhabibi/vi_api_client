# Exceptions Reference

The library simplifies Viessmann API error handling by mapping HTTP status codes and API error payloads to specific Python exceptions.

All exceptions inherit from `ViError` (and `Exception`).

## The three you catch in everyday use

*   `ValueError`: The value violates the feature's rules and was **not sent**
    (e.g. `Value 70 > max (60)`).
*   `ViCommandRejectedError`: The device **rejected** the change.
*   `ViConnectionError` / `ViDeviceCommunicationError`: Network trouble, or the
    device/gateway was not reachable.

```python
try:
    device = await client.set_feature(device, "heating.dhw.temperature.main", 50)
except ValueError as error:
    print(f"Not sent: {error}")
except ViCommandRejectedError as error:
    print(f"The device rejected the change: {error}")
```

The full hierarchy and details follow below.

## Exception Hierarchy

*   `ViError` (Base class)
    *   `ViConnectionError`: Network issues (DNS, Timeout, Connection Refused).
    *   `ViAuthError`: Authentication failed (401/403). check your tokens.
    *   `ViNotFoundError`: Resource not found (404). Typically happens if you request a feature that does not exist on the device.
    *   `ViRateLimitError`: API Rate Limit exceeded (429), optionally with server retry guidance.
    *   `ViResponseError`: A successful HTTP response violates the expected response contract.
    *   `ViDeviceCommunicationError`: The API could not communicate with the device or gateway; raised for every error response reported as `DEVICE_COMMUNICATION_ERROR`, whatever its HTTP status.
    *   `ViCommandRejectedError`: The API or device rejected a command; raised by `set_feature` and `run_command`.
    *   `ViValidationError`: Bad Request (400) or Validation Error (422). Includes detailed validation messages from the API.
    *   `ViServerInternalError`: Server issues (500+).

## Exception Details

Exceptions expose an optional `error_id` and an optional `error_type` from
structured Viessmann error responses. `ViValidationError` additionally exposes
`validation_errors`; `ViCommandRejectedError` additionally exposes `message` and
`reason`.

```python
try:
    device = await client.set_feature(device, feature, 50.0)
    # No exception means the API accepted the command.
except ViCommandRejectedError as e:
    print(f"Command rejected: {e}")
    print(f"Message: {e.message}")
    print(f"Reason: {e.reason}")
    print(f"Error type: {e.error_type}")
except ViValidationError as e:
    print(f"Validation Failed: {e}")
    print(f"Error ID: {e.error_id}")
    print(f"Error type: {e.error_type}")
    # Specific validation details (list of dicts)
    for err in e.validation_errors:
        print(f" - {err['message']} @ {err['path']}")
```

Structured Viessmann error fields are validated before exposure. `error_id`
and `error_type` are exposed only when the API reports strings, the message
must be a string, and `validation_errors` is exposed only when the API reports
a list of string-keyed JSON objects; malformed fields keep their HTTP-level
defaults instead of being exposed. `validation_errors` is a sequence of
`ValidationDetail` dictionaries, so iteration, indexing, and `.get` keep
working. Rate-limit retry guidance is exposed only as the validated,
non-negative `retry_after` delay.

`refresh_gateway_devices` uses `error_type` to keep
`DEVICE_COMMUNICATION_ERROR`, `DEVICE_NOT_FOUND`, and
`PACKAGE_NOT_PAID_FOR` failures from concrete per-device fallbacks in its
partial result. Authentication, rate-limit, connection, server, unknown API,
and response-contract errors abort the whole refresh.

## Device Communication Errors

`ViDeviceCommunicationError` is raised for every error response the API
classifies as `DEVICE_COMMUNICATION_ERROR`, whatever its HTTP status: the
recorded device bodies come with status 400 and the same error type has been
seen with status 502. An unreachable device or gateway is not a mistake in the
request, so it never maps to `ViValidationError` or `ViServerInternalError` on
its status alone; a status 400 with another error type still raises
`ViValidationError`, and a 5xx with another error type still raises
`ViServerInternalError`. The exception keeps `error_type` filled, so
error-type based fallbacks keep working.

When the API reports a debugging `extendedPayload` with a string `reason`, the
reason is appended to the exception message as text, for example
`Device communication error (reason: DEVICE_OFFLINE)`. No other
`extendedPayload` field is used or printed, because the API documents the
payload as debugging information that may change and may contain account
identifiers.

## Command Rejections

`ViCommandRejectedError` is raised by `set_feature` and `run_command` when the
API or device rejects a command, so success always means that no exception was
raised. Its `message` and `reason` attributes (both `str | None`) carry the
rejection details, and `error_id` and `error_type` are filled when the
rejection came as an HTTP error envelope.

A rejection is recognized in two ways:

1. A successful HTTP command response whose `success` flag is false; `message`
   and `reason` come from that response.
2. A command request answered with status 400, error type
   `DEVICE_COMMUNICATION_ERROR`, and `extendedPayload.reason`
   `COMMAND_NOT_EXECUTABLE` — what a device returns for a command it currently
   does not allow. `ViCommandRejectedError` is raised for command requests
   only; the same envelope on a read request raises
   `ViDeviceCommunicationError` like every other response of that error type.

The second recognition reads the documented-but-debugging `extendedPayload`
for this one reason value only, and degrades safely: when `extendedPayload`
is missing, not an object, or carries another reason, the response raises
`ViDeviceCommunicationError`. A rejected command gives `set_feature` no
snapshot, so the caller keeps its previous one.

## Handling Specific Cases

### 404 Not Found
`get_features` matches `feature_names` locally, so a feature the device does
not report yields an empty list rather than an error. `ViNotFoundError` means
that the API reported a missing resource, such as an unknown device.

```python
try:
    features = await client.get_features(
        device,
        feature_names=["heating.sensors.volumetricFlow.share"],
    )
    if not features:
        print("Feature not reported by this device.")
except ViNotFoundError:
    print("Device or API endpoint not found.")
```

### Rate Limits
If you hit a rate limit (429), `ViRateLimitError` is raised. Its optional
`retry_after` attribute is a non-negative delay in seconds parsed from a valid
`Retry-After` response header; it is `None` when the server does not provide
usable guidance. The library never sleeps or retries automatically, so the
calling application owns the backoff decision.

```python
from asyncio import sleep

try:
    data = await client.get_features(...)
except ViRateLimitError as error:
    delay = error.retry_after if error.retry_after is not None else 60
    print(f"Rate limit hit! Waiting {delay}s...")
    await sleep(delay)
```

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Authentication](03_auth_reference.md)**: setup tokens and sessions.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `FeatureConstraints`, `Device`, and command outcomes.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.

## Import Reference

Import exception classes from the package root:

```python
from vi_api_client import (
    ViAuthError,
    ViCommandRejectedError,
    ViConnectionError,
    ViDeviceCommunicationError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViResponseError,
    ViServerInternalError,
    ViValidationError,
)
```
