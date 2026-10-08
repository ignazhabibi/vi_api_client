"""Authentication module for Viessmann API."""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from math import isfinite
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlencode

import aiohttp

from ._types import JsonValue
from .const import DEFAULT_SCOPES, ENDPOINT_AUTHORIZE, ENDPOINT_TOKEN
from .credentials import CredentialDocument
from .exceptions import ViAuthError, ViConnectionError, ViResponseError
from .validation import is_json_number, validate_json_value

_LOGGER = logging.getLogger(__name__)

# Renew tokens this long before they expire so they cannot expire mid-request.
_TOKEN_EXPIRY_MARGIN_SECONDS = 60


class AbstractAuth(ABC):
    """Abstract class to make authenticated requests."""

    def __init__(self, websession: aiohttp.ClientSession | None = None) -> None:
        """Initialize the auth with an optional externally managed session."""
        self._websession = websession
        self._owns_websession = False

    @property
    def websession(self) -> aiohttp.ClientSession | None:
        """Return the session configured at construction or created lazily."""
        return self._websession

    async def __aenter__(self) -> Self:
        """Enter the authentication context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close resources owned by the authentication provider."""
        await self.async_close()

    @abstractmethod
    async def async_get_access_token(self) -> str:
        """Return a valid access token."""
        # Abstract method bodies never execute in concrete subclasses.
        pass  # pragma: no cover

    async def _async_get_websession(self) -> aiohttp.ClientSession:
        """Return an available session, creating an owned one when needed."""
        if self._websession is None:
            self._websession = aiohttp.ClientSession()
            self._owns_websession = True
        return self._websession

    async def async_close(self) -> None:
        """Close the web session only when it was created internally."""
        if not self._owns_websession or self._websession is None:
            return

        if not self._websession.closed:
            await self._websession.close()
        self._websession = None
        self._owns_websession = False

    async def request(
        self, method: str, url: str, **kwargs: Any
    ) -> aiohttp.ClientResponse:
        """Make an authenticated request.

        Exceptions raised by the authentication provider propagate unchanged.

        Raises:
            ViConnectionError: If the HTTP request cannot be made.
        """
        access_token = await self.async_get_access_token()

        headers = kwargs.get("headers", {}).copy()
        headers["Authorization"] = f"Bearer {access_token}"
        kwargs["headers"] = headers

        websession = await self._async_get_websession()
        try:
            return await websession.request(method, url, **kwargs)
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ViConnectionError(f"Network error: {error}") from error


class OAuth(AbstractAuth):
    """OAuth2 implementation for standalone usage."""

    def __init__(
        self,
        client_id: str,
        redirect_uri: str,
        token_file: Path | str,
        websession: aiohttp.ClientSession | None = None,
        scope: str = DEFAULT_SCOPES,
    ) -> None:
        """Initialize OAuth.

        If websession is None, a session is created lazily. Use the auth provider
        as an async context manager or call `async_close` to release it.
        The token file is not read here; it is loaded in a worker thread on
        the first token request so construction never blocks the event loop.

        Args:
            client_id: OAuth client ID.
            redirect_uri: Redirect URI for authentication flow.
            token_file: Path to file for storing tokens.
            websession: Optional aiohttp ClientSession.
            scope: OAuth scopes (default: default scopes).
        """
        super().__init__(websession)
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.token_file: Path = Path(token_file)
        self._credential_document = CredentialDocument(self.token_file)
        self.scope = scope
        self._token_info: dict[str, JsonValue] | None = None
        self._token_load_lock = asyncio.Lock()
        self._pkce_verifier: str | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    def get_authorization_url(self) -> str:
        """Return the login URL and remember its PKCE verifier.

        The verifier is required by `async_exchange_code_for_tokens`, so call
        both methods on the same instance.
        """
        self._pkce_verifier, code_challenge = _generate_pkce_pair()

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        return f"{ENDPOINT_AUTHORIZE}?{urlencode(params)}"

    async def _async_token_info(self) -> dict[str, JsonValue]:
        """Return the stored tokens, loading the token file once in a thread.

        Raises:
            ViAuthError: If the token file cannot be read or is malformed.
        """
        if self._token_info is None:
            async with self._token_load_lock:
                if self._token_info is None:
                    self._token_info = await asyncio.to_thread(
                        self._credential_document.read
                    )
        return self._token_info

    async def _async_update_tokens(self, token_data: object) -> None:
        """Update internal token state and save it in a worker thread."""
        validated_token_data = _validate_token_response(token_data)
        token_info = await self._async_token_info()
        token_info.update(validated_token_data)

        expires_in = validated_token_data.get("expires_in")
        if is_json_number(expires_in):
            token_info["expires_at"] = time.time() + expires_in

        await asyncio.to_thread(self._credential_document.update, dict(token_info))

    async def _async_request_tokens(
        self, form_data: Mapping[str, JsonValue], action: str
    ) -> None:
        """Post a token request and store the returned tokens.

        Raises:
            ViAuthError: If the token endpoint rejects the request or returns
                an invalid token response.
            ViConnectionError: If the token request cannot be made.
        """
        websession = await self._async_get_websession()
        try:
            async with websession.post(ENDPOINT_TOKEN, data=form_data) as response:
                if response.status != 200:
                    raise ViAuthError(f"Failed to {action}: {await response.text()}")
                await self._async_update_tokens(await _read_token_response(response))
        except (TimeoutError, aiohttp.ClientError) as error:
            raise ViConnectionError(f"Network error: {error}") from error

    async def async_exchange_code_for_tokens(self, code: str) -> None:
        """Exchange an authorization code for tokens and store them.

        Raises:
            ViAuthError: If `get_authorization_url` was not called first or the
                token endpoint rejects the code.
            ViConnectionError: If the token endpoint cannot be reached.
        """
        if not self._pkce_verifier:
            raise ViAuthError(
                "PKCE Verifier missing. Did you call get_authorization_url()?"
            )

        form_data = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
            "code": code,
            "code_verifier": self._pkce_verifier,
        }
        await self._async_request_tokens(form_data, "fetch token")
        _LOGGER.debug("Exchanged authorization code for tokens")

    async def _async_refresh_access_token(self) -> None:
        """Refresh the access token through the token endpoint."""
        token_info = await self._async_token_info()
        refresh_token = token_info.get("refresh_token")
        if not refresh_token:
            raise ViAuthError("No refresh token available.")

        form_data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        _LOGGER.debug("Refreshing access token")
        await self._async_request_tokens(form_data, "refresh token")
        _LOGGER.debug(
            "Access token refreshed (expires_in=%s)", token_info.get("expires_in")
        )

    async def async_refresh_access_token(self) -> None:
        """Refresh the access token, sharing an in-flight refresh per instance.

        Raises:
            ViAuthError: If the refresh token is unavailable or rejected.
            ViConnectionError: If the token endpoint cannot be reached.
        """
        refresh_task = self._refresh_task
        if refresh_task is None or refresh_task.done():
            refresh_task = asyncio.create_task(self._async_refresh_access_token())
            self._refresh_task = refresh_task

        try:
            await asyncio.shield(refresh_task)
        finally:
            if refresh_task.done() and self._refresh_task is refresh_task:
                self._refresh_task = None

    async def async_close(self) -> None:
        """Let an in-flight refresh settle before closing an owned session."""
        refresh_task = self._refresh_task
        if refresh_task is not None:
            try:
                await asyncio.shield(refresh_task)
            except (
                ViAuthError,
                ViConnectionError,
                aiohttp.ClientError,
                OSError,
                TimeoutError,
                ValueError,
            ):
                _LOGGER.exception("Token refresh failed while closing authentication")
            finally:
                if refresh_task.done() and self._refresh_task is refresh_task:
                    self._refresh_task = None

        await super().async_close()

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it shortly before expiry.

        Raises:
            ViAuthError: If no usable token is stored or the refresh fails.
            ViConnectionError: If a needed refresh cannot reach the token
                endpoint.
        """
        token_info = await self._async_token_info()
        if not token_info:
            raise ViAuthError("No tokens loaded. Please authenticate first.")

        expires_at = token_info.get("expires_at")
        if (
            is_json_number(expires_at)
            and time.time() < expires_at - _TOKEN_EXPIRY_MARGIN_SECONDS
        ):
            return _access_token_value(token_info)

        if "refresh_token" in token_info:
            await self.async_refresh_access_token()
            return _access_token_value(token_info)

        # Without the offline_access scope there is no refresh token to renew with.
        _LOGGER.warning(
            "No refresh token available; using possibly expired access token"
        )
        return _access_token_value(token_info)


def _generate_pkce_pair() -> tuple[str, str]:
    """Return a PKCE code verifier and its S256 code challenge (RFC 7636)."""
    # 96 random bytes encode to 128 URL-safe characters, the RFC's maximum.
    verifier = secrets.token_urlsafe(96)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _access_token_value(token_info: Mapping[str, JsonValue]) -> str:
    """Return the validated current access token."""
    access_token = token_info.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ViAuthError("No valid access token available.")
    return access_token


def _validate_token_response(token_data: object) -> dict[str, JsonValue]:
    """Validate a successful OAuth token response before updating state."""
    try:
        validated_token_data = validate_json_value(token_data, path="Token response")
    except ViResponseError as error:
        raise ViAuthError("Token response contains invalid JSON data") from error
    if not isinstance(validated_token_data, dict):
        raise ViAuthError("Token response must be a JSON object")

    access_token = validated_token_data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ViAuthError("Token response access_token must be a non-empty string")
    for field_name in ("refresh_token", "token_type"):
        value = validated_token_data.get(field_name)
        if value is not None and not isinstance(value, str):
            raise ViAuthError(f"Token response {field_name} must be a string")
    expires_in = validated_token_data.get("expires_in")
    if expires_in is not None and (
        not is_json_number(expires_in) or not isfinite(expires_in) or expires_in < 0
    ):
        raise ViAuthError(
            "Token response expires_in must be a non-negative finite number"
        )
    return validated_token_data


async def _read_token_response(response: aiohttp.ClientResponse) -> object:
    """Read a successful token response as JSON with library-owned errors."""
    try:
        return await response.json()
    except aiohttp.ClientConnectionError, aiohttp.ClientPayloadError:
        # An incomplete body is a network failure, which the caller reports.
        raise
    except (aiohttp.ClientError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ViAuthError("Token response contains invalid JSON data") from error
