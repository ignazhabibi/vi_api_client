"""Tests for private live adapter HTTP and authentication behavior."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client._adapter import _LiveAdapter
from vi_api_client.auth import AbstractAuth
from vi_api_client.client import ViClient
from vi_api_client.const import API_BASE_URL, ENDPOINT_INSTALLATIONS
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
async def test_live_adapter_preserves_external_oauth_error() -> None:
    """External OAuth errors should reach the caller unchanged."""
    # Arrange: Configure auth to raise a response-shaped external OAuth error.
    oauth_error = _ExternalOAuthError(
        request_info=MagicMock(),
        history=(),
        status=400,
        message="Refresh token rejected",
        headers=MagicMock(),
    )
    adapter = _LiveAdapter(_RaisingAuth(oauth_error))

    # Act and assert: The adapter should preserve the provider-owned exception.
    with pytest.raises(_ExternalOAuthError) as raised_error:
        await adapter.get_installations()
    assert raised_error.value is oauth_error


@pytest.mark.asyncio
async def test_live_adapter_wraps_aiohttp_connection_error() -> None:
    """Aiohttp connection failures should remain library connection errors."""
    # Arrange: Configure the HTTP session to fail while opening the connection.
    connection_error = aiohttp.ClientConnectionError("Network unavailable")
    websession = MagicMock(spec=aiohttp.ClientSession)
    websession.request = AsyncMock(side_effect=connection_error)
    adapter = _LiveAdapter(_StaticAuth(websession))

    # Act and assert: The adapter should expose the library transport exception.
    with pytest.raises(ViConnectionError) as raised_error:
        await adapter.get_installations()
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
async def test_live_adapter_preserves_viessmann_error_type(
    status: int, expected_error: type[ViError]
) -> None:
    # Arrange: Return a structured Viessmann error from the HTTP boundary.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
    payload = {
        "errorType": "DEVICE_COMMUNICATION_ERROR",
        "message": "Device communication failed",
        "viErrorId": "error-123",
    }

    with aioresponses() as mock_responses:
        mock_responses.get(url, payload=payload, status=status)
        async with aiohttp.ClientSession() as session:
            adapter = _LiveAdapter(_StaticAuth(session))

            # Act and assert: The public exception retains API classification data.
            with pytest.raises(expected_error) as raised_error:
                await adapter.get_installations()
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


def test_rate_limit_error_keeps_existing_positional_arguments() -> None:
    """Rate-limit errors should retain their existing positional signature."""
    # Arrange and Act: Construct with positional arguments supported before retry data.
    error = ViRateLimitError("Rate limited", "error-123", "RATE_LIMIT")

    # Assert: Existing values retain their meanings and retry data defaults to none.
    assert error.error_id == "error-123"
    assert error.error_type == "RATE_LIMIT"
    assert error.retry_after is None


@pytest.mark.parametrize(
    ("retry_after_header", "expected_retry_after"),
    [
        ("12.5", 12.5),
        ("-5", None),
        ("not-a-duration", None),
        (None, None),
    ],
)
@pytest.mark.asyncio
async def test_client_normalizes_numeric_or_invalid_retry_after_without_retrying(
    retry_after_header: str | None, expected_retry_after: float | None
) -> None:
    """A 429 should expose numeric guidance through one client request."""
    # Arrange: Configure a rate-limited public client request.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
    headers = (
        {"Retry-After": retry_after_header} if retry_after_header is not None else {}
    )
    async with aiohttp.ClientSession() as session:
        client = ViClient(_StaticAuth(session))
        with aioresponses() as mock_responses:
            mock_responses.get(url, status=429, headers=headers)

            # Act and assert: The request raises once with parsed retry guidance.
            with pytest.raises(ViRateLimitError) as raised_error:
                await client.get_installations()

    # Assert: The error guidance and request count match the response.
    assert raised_error.value.retry_after == expected_retry_after
    assert len(mock_responses.requests) == 1


@pytest.mark.asyncio
async def test_client_normalizes_http_date_retry_after_without_retrying() -> None:
    """A 429 HTTP-date header should become a non-negative delay in seconds."""
    # Arrange: Configure a public client request with a future HTTP-date header.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
    retry_at = datetime.now(UTC) + timedelta(seconds=30)
    async with aiohttp.ClientSession() as session:
        client = ViClient(_StaticAuth(session))
        with aioresponses() as mock_responses:
            mock_responses.get(
                url, status=429, headers={"Retry-After": format_datetime(retry_at)}
            )

            # Act and assert: The client exposes the server delay without retrying.
            with pytest.raises(ViRateLimitError) as raised_error:
                await client.get_installations()

    # Assert: The date becomes an approximate non-negative duration from one request.
    assert raised_error.value.retry_after is not None
    assert 0 <= raised_error.value.retry_after <= 30
    assert len(mock_responses.requests) == 1
