# Authentication & Connection

This reference covers authentication strategies and connection management.

## Connection Management (`websession`)

All Auth classes accept an optional `websession` argument (an `aiohttp.ClientSession`).

*   **Provided**: The client uses your session but leaves its lifecycle under
    caller control. This is efficient for reusing connections in a larger app
    (e.g. Home Assistant).
*   **Not Provided**: The client creates its own `ClientSession` lazily when the
    first request is made.

```python
import aiohttp
from vi_api_client import OAuth, ViClient


async def main():
    async with aiohttp.ClientSession() as session:
        auth = OAuth(..., websession=session)
        client = ViClient(auth)
        # requests reuse the `session` pool
```

When no session is supplied, use the auth provider as an async context manager
so its internally created session is closed reliably:

```python
async def main():
    async with OAuth(
        client_id="YOUR_CLIENT_ID",
        redirect_uri="http://localhost:4200/",
        token_file="tokens.json",
    ) as auth:
        client = ViClient(auth)
        installations = await client.get_installations()
```

Alternatively, call `await auth.async_close()` when the provider is no longer
needed. `async_close()` never closes a session passed through `websession`.

## Base Class: `AbstractAuth`

The abstract base class for all authentication providers. It handles the core logic of attaching the Bearer token to requests.

### Methods

#### `get_access_token() -> str`
Returns a valid access token. If the current token is expired, it automatically triggers a refresh.

## `OAuth`

Implements the OAuth2 PKCE flow (Proof Key for Code Exchange). This is the standard flow for Viessmann API.

```python
from vi_api_client import OAuth
```

### Constructor

| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `client_id` | `str` | Yes | Your Client ID from the [Viessmann Developer Portal](https://developer.viessmann.com/). |
| `redirect_uri` | `str` | Yes | Must match your registered Redirect URI (e.g. `http://localhost:4200/`). |
| `token_file` | `str` | No | Path to store/load tokens (JSON). Defaults to None (memory only). |
| `websession` | `ClientSession` | No | `aiohttp` session to share connections. |

### Automatic Token Refresh
If `token_file` is provided, the class automatically:
1.  **Loads** tokens from disk on startup.
2.  **Saves** new tokens to disk whenever they are refreshed.

This ensures persistent authentication across restarts.

(The `OAuth` class implements the PKCE flow internally).

## Token Format
Tokens are stored in `token_file` as:
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1700000000.0
}
```

## Next Steps

- **[Getting Started](01_getting_started.md)**: installation and basic usage.
- **[API Concepts](02_api_structure.md)**: understand the data-driven design.
- **[Models Reference](04_models_reference.md)**: detailed documentation of `Feature`, `Device`, and `Command`.
- **[Client Reference](05_client_reference.md)**: methods on `ViClient`.
- **[CLI Reference](06_cli_reference.md)**: terminal usage.
- **[Exceptions Reference](07_exceptions_reference.md)**: error handling.
