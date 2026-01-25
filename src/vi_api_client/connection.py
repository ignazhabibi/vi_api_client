"""Connection handling for Vi API."""

import logging
from typing import Any

from .auth import AbstractAuth
from .const import API_BASE_URL
from .exceptions import (
    ViAuthError,
    ViConnectionError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViServerInternalError,
    ViValidationError,
)
from .utils import mask_pii

_LOGGER = logging.getLogger(__name__)


async def _raise_for_status(response) -> None:
    """Parses the response and raises specific ViErrors if status >= 400.

    This handles API-specific error formats including viErrorId and validation details.
    """
    status = response.status
    if status < 400:
        return

    # Attempt to extract detailed error information from the response body.
    vi_error_id = None
    error_message = f"HTTP {status}"
    validation_details = []
    error_type = "UNKNOWN"

    try:
        data = await response.json()
        vi_error_id = data.get("viErrorId")
        error_message = data.get("message", error_message)
        error_type = data.get("errorType", "")
        if "validationErrors" in data:
            validation_details = data["validationErrors"]
    except Exception:
        # Fallback if body is not JSON or empty.
        pass

    _LOGGER.error(
        "API Error %s (%s): %s (ID: %s)",
        status,
        error_type,
        error_message,
        vi_error_id,
    )

    # Map status codes to specific exceptions.
    if status == 401:
        raise ViAuthError(f"Unauthorized: {error_message}", vi_error_id)

    if status == 403:
        raise ViAuthError(f"Forbidden: {error_message}", vi_error_id)

    if status == 404:
        raise ViNotFoundError(f"Not Found: {error_message}", vi_error_id)

    if status == 429:
        raise ViRateLimitError("Rate Limit Exceeded", vi_error_id)

    if status in (400, 422):
        raise ViValidationError(error_message, vi_error_id, validation_details)

    if status >= 500:
        raise ViServerInternalError(
            f"Server Error {status}: {error_message}", vi_error_id
        )

    # Generic catch-all for other error codes.
    raise ViError(f"Unknown Error {status}: {error_message}", vi_error_id)


class ViConnector:
    """Handles raw HTTP connections to the Vi API."""

    def __init__(self, auth: AbstractAuth):
        """Initialize the connector."""
        self.auth = auth

    async def get(self, url: str) -> dict[str, Any]:
        """Execute a GET request."""
        full_url = self._prepare_url(url)
        return await self._request("GET", full_url)

    async def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a POST request."""
        full_url = self._prepare_url(url)
        return await self._request("POST", full_url, json=payload)

    def _prepare_url(self, url: str) -> str:
        """Ensure URL is absolute."""
        if url.startswith("http"):
            return url
        if not url.startswith("/"):
            url = f"/{url}"
        return f"{API_BASE_URL}{url}"

    async def _request(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        """Central request logic.

        Handles execution and delegates error checking.
        """
        try:
            _LOGGER.debug(mask_pii(f"Request: {method} {url}"))
            async with await self.auth.request(method, url, **kwargs) as response:
                # Verify response status and raise exceptions if needed.
                await _raise_for_status(response)

                # Return parsed JSON for success.
                try:
                    return await response.json()
                except Exception:
                    return {}

        except ViError:
            # Re-raise business logic errors.
            raise
        except Exception as e:
            # Wrap low-level network errors.
            raise ViConnectionError(f"Network error: {e}") from e
