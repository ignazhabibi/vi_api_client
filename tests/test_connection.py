"""Tests for Vi API connection handling."""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.auth import AbstractAuth
from vi_api_client.connection import ViConnector
from vi_api_client.const import API_BASE_URL
from vi_api_client.exceptions import (
    ViAuthError,
    ViConnectionError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViServerInternalError,
    ViValidationError,
)


class _ExternalOAuthError(aiohttp.ClientResponseError):
    """Represent an OAuth error owned by an external authentication provider."""


class _RaisingAuth(AbstractAuth):
    """Raise a configured exception while obtaining an access token."""

    def __init__(self, error: Exception) -> None:
        """Initialize the authentication provider with its token error."""
        super().__init__()
        self.error = error

    async def async_get_access_token(self) -> str:
        """Raise the configured authentication provider error."""
        raise self.error


class _StaticAuth(AbstractAuth):
    """Return a static access token for transport error tests."""

    async def async_get_access_token(self) -> str:
        """Return a static access token."""
        return "access-token"


@pytest.mark.asyncio
async def test_connector_preserves_external_oauth_error() -> None:
    """External OAuth errors should reach the caller unchanged."""
    # Arrange: Configure auth to raise a response-shaped external OAuth error.
    oauth_error = _ExternalOAuthError(
        request_info=MagicMock(),
        history=(),
        status=400,
        message="Refresh token rejected",
        headers=MagicMock(),
    )
    connector = ViConnector(_RaisingAuth(oauth_error))

    # Act and assert: The connector should preserve the provider-owned exception.
    with pytest.raises(_ExternalOAuthError) as raised_error:
        await connector.get("/installations")
    assert raised_error.value is oauth_error


@pytest.mark.asyncio
async def test_connector_wraps_aiohttp_connection_error() -> None:
    """Aiohttp connection failures should remain library connection errors."""
    # Arrange: Configure the HTTP session to fail while opening the connection.
    connection_error = aiohttp.ClientConnectionError("Network unavailable")
    websession = MagicMock(spec=aiohttp.ClientSession)
    websession.request = AsyncMock(side_effect=connection_error)
    connector = ViConnector(_StaticAuth(websession))

    # Act and assert: The connector should expose the library transport exception.
    with pytest.raises(ViConnectionError) as raised_error:
        await connector.get("/installations")
    assert raised_error.value.__cause__ is connection_error


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [
        (400, ViValidationError),
        (401, ViAuthError),
        (403, ViAuthError),
        (404, ViNotFoundError),
        (418, ViError),
        (429, ViRateLimitError),
        (500, ViServerInternalError),
    ],
)
@pytest.mark.asyncio
async def test_connector_preserves_viessmann_error_type(
    status: int, expected_error: type[Exception]
) -> None:
    # Arrange: Return a structured Viessmann error from the HTTP boundary.
    url = f"{API_BASE_URL}/features"
    payload = {
        "errorType": "DEVICE_COMMUNICATION_ERROR",
        "message": "Device communication failed",
        "viErrorId": "error-123",
    }

    with aioresponses() as mock_responses:
        mock_responses.get(url, payload=payload, status=status)
        async with aiohttp.ClientSession() as session:
            connector = ViConnector(_StaticAuth(session))

            # Act and assert: The public exception retains API classification data.
            with pytest.raises(expected_error) as raised_error:
                await connector.get("/features")
            assert raised_error.value.error_id == "error-123"
            assert raised_error.value.error_type == "DEVICE_COMMUNICATION_ERROR"


def test_validation_error_keeps_existing_positional_arguments() -> None:
    # Arrange: Use the public positional signature supported before error types.
    validation_errors = [{"message": "Invalid", "path": "feature"}]

    # Act: Construct the error with its existing three positional arguments.
    error = ViValidationError("Bad request", "error-123", validation_errors)

    # Assert: Existing arguments retain their meaning and error type is optional.
    assert error.error_id == "error-123"
    assert error.error_type is None
    assert error.validation_errors == validation_errors
