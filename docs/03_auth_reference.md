# Authentication & Connection

This reference covers authentication strategies and connection management.

## Connection Management (`websession`)

All Auth classes accept an optional `websession` argument (an `aiohttp.ClientSession`).
*   **Provided**: The client uses your session. Efficient for reusing connections in a larger app (e.g. Home Assistant).
*   **Not Provided**: The client creates its own `ClientSession` internally.

```python
import aiohttp
from vi_api_client import PKCEAuth, ViCareClient

async def main():
    async with aiohttp.ClientSession() as session:
        auth = PKCEAuth(..., websession=session)
        client = ViCareClient(auth)
        # requests reuse the `session` pool
```

## Base Class: `AbstractAuth`

The abstract base class for all authentication providers. It handles the core logic of attaching the Bearer token to requests.

### Methods

#### `get_access_token() -> str`
Returns a valid access token. If the current token is expired, it automatically triggers a refresh.

## `PKCEAuth`

Implements the OAuth2 PKCE flow (Proof Key for Code Exchange). This is the standard flow for Viessmann API.

```python
from vi_api_client import PKCEAuth
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

## `OAuth` (Legacy)
A standard OAuth2 implementation (without PKCE specifics). Use `PKCEAuth` for modern Viessmann apps.

## Token Format
Tokens are stored in `token_file` as:
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1700000000.0
}
```
