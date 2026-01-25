"""Authentication module for Viessmann API."""

import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp
import pkce

from .const import DEFAULT_SCOPES, ENDPOINT_AUTHORIZE, ENDPOINT_TOKEN
from .exceptions import ViAuthError

_LOGGER = logging.getLogger(__name__)


class AbstractAuth(ABC):
    """Abstract class to make authenticated requests."""

    def __init__(self, websession: aiohttp.ClientSession) -> None:
        """Initialize the auth."""
        self.websession = websession

    @abstractmethod
    async def async_get_access_token(self) -> str:
        """Return a valid access token."""
        pass

    async def request(
        self, method: str, url: str, **kwargs: Any
    ) -> aiohttp.ClientResponse:
        """Make an authenticated request."""
        try:
            access_token = await self.async_get_access_token()
        except ViAuthError:
            raise

        headers = kwargs.get("headers", {}).copy()
        headers["Authorization"] = f"Bearer {access_token}"
        kwargs["headers"] = headers

        return await self.websession.request(method, url, **kwargs)


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

        If websession is None, one must be created externally or managed.
        For standalone CLI, we might pass one in.

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
        self.scope = scope
        self._token_info: dict[str, Any] = {}
        self._pkce_verifier: str | None = None

        # Load existing tokens if available
        self._load_tokens()

    def _load_tokens(self) -> None:
        """Load tokens from file."""
        try:
            with self.token_file.open() as file:
                self._token_info = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            self._token_info = {}  # Allow init as empty if invalid/missing

    def _save_tokens(self) -> None:
        """Save tokens to file, preserving existing content."""
        current_data = {}
        try:
            with self.token_file.open() as file:
                current_data = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        current_data.update(self._token_info)

        with self.token_file.open("w") as file:
            json.dump(current_data, file, indent=2)

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

        async with self.websession.post(ENDPOINT_TOKEN, data=data) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ViAuthError(f"Failed to fetch token: {text}")

            self._update_tokens(await resp.json())

    async def async_refresh_access_token(self) -> None:
        """Refresh the access token."""
        refresh_token = self._token_info.get("refresh_token")
        if not refresh_token:
            raise ViAuthError("No refresh token available.")

        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with self.websession.post(ENDPOINT_TOKEN, data=data) as resp:
            if resp.status != 200:
                text = await resp.text()
                # If refresh fails, we might need to re-auth, but here we just raise
                raise ViAuthError(f"Failed to refresh token: {text}")

            self._update_tokens(await resp.json())

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
