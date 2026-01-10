"""Authentication module for Viessmann API."""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import aiohttp
import pkce

from .const import ENDPOINT_AUTHORIZE, ENDPOINT_TOKEN, DEFAULT_SCOPES
from .exceptions import VitoAuthError

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

    async def request(self, method: str, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """Make an authenticated request."""
        try:
            access_token = await self.async_get_access_token()
        except VitoAuthError:
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
        token_file: str,
        websession: Optional[aiohttp.ClientSession] = None,
        scope: str = DEFAULT_SCOPES,
    ) -> None:
        """Initialize OAuth.
        
        If websession is None, one must be created externally or managed.
        For standalone CLI, we might pass one in.
        """
        super().__init__(websession)
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.token_file = token_file
        self.scope = scope
        self._token_info: Dict[str, Any] = {}
        self._pkce_verifier: Optional[str] = None

        # Load existing tokens if available
        self._load_tokens()

    def _load_tokens(self) -> None:
        """Load tokens from file."""
        try:
            with open(self.token_file, "r") as f:
                self._token_info = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._token_info = {}

    def _save_tokens(self) -> None:
        """Save tokens to file, preserving existing content."""
        current_data = {}
        try:
            with open(self.token_file, "r") as f:
                current_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
            
        current_data.update(self._token_info)
        
        with open(self.token_file, "w") as f:
            json.dump(current_data, f, indent=2)

    def get_authorization_url(self) -> str:
        """Generate authorization URL and PKCE challenge."""
        from urllib.parse import urlencode
        
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

    async def async_fetch_details_from_code(self, code: str) -> None:
        """Exchange code for tokens."""
        if not self._pkce_verifier:
            raise VitoAuthError("PKCE Verifier missing. Did you call get_authorization_url()?")

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
                raise VitoAuthError(f"Failed to fetch token: {text}")
            
            self._token_info = await resp.json()
            self._save_tokens()
    
    async def async_refresh_access_token(self) -> None:
        """Refresh the access token."""
        refresh_token = self._token_info.get("refresh_token")
        if not refresh_token:
            raise VitoAuthError("No refresh token available.")

        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with self.websession.post(ENDPOINT_TOKEN, data=data) as resp:
            if resp.status != 200:
                text = await resp.text()
                # If refresh fails, we might need to re-auth, but here we just raise
                raise VitoAuthError(f"Failed to refresh token: {text}")
            
            new_tokens = await resp.json()
            # Update tokens (preserve refresh_token if not sent back, though standard usually sends a new one)
            self._token_info.update(new_tokens)
            self._save_tokens()

    async def async_get_access_token(self) -> str:
        """Return valid access token, refreshing if necessary."""
        if not self._token_info:
            raise VitoAuthError("No tokens loaded. Please authenticate first.")

        # Check expiration (buffer of 60 seconds)
        # Usually 'expires_in' is returned, we should have calculated 'expires_at' or check if we can simply try refresh
        # Ideally, we store 'expires_at' when we save tokens. 
        # For simplicity here, if we don't have an expiry timestamp, we just rely on the token.
        # But robust implementations track time.
        
        # Let's add simple expiry handling if not present
        now = time.time()
        # Note: Viessmann API returns 'expires_in' (seconds)
        # If we just loaded from file, we need to know when it was saved. 
        # For this MVP, we might rely on try/except or just assume we need refresh if it's old?
        # A better way is to save 'expires_at' in the json.

        # FIX: We didn't save expires_at. Let's patch save logic locally or just always try refresh if we aren't sure?
        # Over-refreshing is bad. 
        # Let's check if we have an expires_at in the dict (we should inject it)
        
        expires_at = self._token_info.get("expires_at")
        if expires_at and now < expires_at - 60:
             return self._token_info["access_token"]
        
        # If no expires_at (fresh load) or expired:
        # If we have a refresh token, try to refresh.
        if "refresh_token" in self._token_info:
            try:
                await self.async_refresh_access_token()
                # Recalculate expires_at
                if "expires_in" in self._token_info:
                     self._token_info["expires_at"] = time.time() + self._token_info["expires_in"]
                     self._save_tokens()
                return self._token_info["access_token"]
            except VitoAuthError:
                # If refresh failed, maybe the access token is still valid? Unlikely if we thought it expired.
                # Just raise.
                raise

        # If we are here, we might have an access token but no refresh token (unlikely with offline_access)
        return self._token_info.get("access_token", "")
