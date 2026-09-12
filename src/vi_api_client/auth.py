"""Authentication module for Viessmann API."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlencode

import aiohttp
import pkce

from .const import DEFAULT_SCOPES, ENDPOINT_AUTHORIZE, ENDPOINT_TOKEN
from .credentials import CredentialDocument
from .exceptions import ViAuthError, ViConnectionError

_LOGGER = logging.getLogger(__name__)


class AbstractAuth(ABC):
    """Abstract class to make authenticated requests."""

    def __init__(self, websession: aiohttp.ClientSession | None = None) -> None:
        """Initialize the auth with an optional externally managed session."""
        self.websession = websession
        self._owns_websession = False

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
        pass

    async def _async_get_websession(self) -> aiohttp.ClientSession:
        """Return an available session, creating an owned one when needed."""
        if self.websession is None:
            self.websession = aiohttp.ClientSession()
            self._owns_websession = True
        return self.websession

    async def async_close(self) -> None:
        """Close the web session only when it was created internally."""
        if not self._owns_websession or self.websession is None:
            return

        if not self.websession.closed:
            await self.websession.close()
        self.websession = None
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
        self.token_file = Path(token_file)
        self._credential_document = CredentialDocument(self.token_file)
        self.scope = scope
        self._token_info: dict[str, Any] = {}
        self._pkce_verifier: str | None = None
        self._refresh_task: asyncio.Task[None] | None = None

        # Load existing tokens if available.
        self._load_tokens()

    def _load_tokens(self) -> None:
        """Load tokens from file."""
        self._token_info = self._credential_document.read()

    def _save_tokens(self) -> None:
        """Save tokens to file, preserving existing content."""
        self._credential_document.update(self._token_info)

    def get_authorization_url(self) -> str:
        """Generate authorization URL and PKCE challenge."""
        self._pkce_verifier, code_challenge = pkce.generate_pkce_pair()

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        return f"{ENDPOINT_AUTHORIZE}?{urlencode(params)}"

    def _update_tokens(self, token_data: dict[str, Any]) -> None:
        """Update internal token state and save."""
        self._token_info.update(token_data)

        # Calculate absolute expiration time if 'expires_in' is present
        if "expires_in" in token_data:
            self._token_info["expires_at"] = time.time() + token_data["expires_in"]

        self._save_tokens()

    async def async_fetch_details_from_code(self, code: str) -> None:
        """Exchange code for tokens."""
        if not self._pkce_verifier:
            raise ViAuthError(
                "PKCE Verifier missing. Did you call get_authorization_url()?"
            )

        data = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
            "code": code,
            "code_verifier": self._pkce_verifier,
        }

        websession = await self._async_get_websession()
        async with websession.post(ENDPOINT_TOKEN, data=data) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ViAuthError(f"Failed to fetch token: {text}")

            self._update_tokens(await resp.json())

    async def _async_refresh_access_token(self) -> None:
        """Refresh the access token through the token endpoint."""
        refresh_token = self._token_info.get("refresh_token")
        if not refresh_token:
            raise ViAuthError("No refresh token available.")

        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        websession = await self._async_get_websession()
        async with websession.post(ENDPOINT_TOKEN, data=data) as resp:
            if resp.status != 200:
                text = await resp.text()
                # If refresh fails, we might need to re-auth, but here we just raise
                raise ViAuthError(f"Failed to refresh token: {text}")

            self._update_tokens(await resp.json())

    async def async_refresh_access_token(self) -> None:
        """Refresh the access token, sharing an in-flight refresh per instance.

        Raises:
            ViAuthError: If the refresh token is unavailable or rejected.
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
        """Return valid access token, refreshing if necessary."""
        if not self._token_info:
            raise ViAuthError("No tokens loaded. Please authenticate first.")

        # Check existing expiration (buffer of 60 seconds)
        now = time.time()
        expires_at = self._token_info.get("expires_at")

        if expires_at and now < expires_at - 60:
            return self._token_info["access_token"]

        # If expired or unknown: try refresh
        if "refresh_token" in self._token_info:
            await self.async_refresh_access_token()
            return self._token_info["access_token"]

        # Fallback: return what we have (e.g. if offline_access scope was missing)
        return self._token_info.get("access_token", "")
