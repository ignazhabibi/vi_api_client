# Exceptions Reference

The library simplifies Viessmann API error handling by mapping HTTP status codes and API error payloads to specific Python exceptions.

All exceptions inherit from `ViError` (and `Exception`).

## Exception Hierarchy

*   `ViError` (Base class)
    *   `ViConnectionError`: Network issues (DNS, Timeout, Connection Refused).
    *   `ViAuthError`: Authentication failed (401/403). check your tokens.
    *   `ViNotFoundError`: Resource not found (404). Typically happens if you request a feature that does not exist on the device.
    *   `ViRateLimitError`: API Rate Limit exceeded (429). You should back off.
    *   `ViValidationError`: Bad Request (400) or Validation Error (422). Includes detailed validation messages from the API.
    *   `ViServerInternalError`: Server issues (500+).

## Exception Details

Exceptions often carry detailed information from the API:

```python
try:
    await client.execute_command(...)
except ViValidationError as e:
    print(f"Validation Failed: {e.message}")
    print(f"Error ID: {e.error_id}")
    # Specific validation details (list of dicts)
    for err in e.validation_errors:
        print(f" - {err['message']} @ {err['path']}")
```

## Handling Specific Cases

### 404 Not Found
When requesting a feature that isn't supported by a device, the API returns 404. The client raises `ViNotFoundError`.

```python
try:
    flow = await client.get_feature(..., "heating.sensors.volumetricFlow.share")
except ViNotFoundError:
    print("This device does not have a volumetric flow sensor.")
```

### Rate Limits
If you hit a rate limit (429), `ViRateLimitError` is raised. The API might imply a cooldown period.

```python
from asyncio import sleep

try:
    data = await client.get_features(...)
except ViRateLimitError:
    print("Rate limit hit! Waiting 60s...")
    await sleep(60)
```
