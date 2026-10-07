"""Tests for private live adapter HTTP and authentication behavior."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import MagicMock

import aiohttp
import pytest

from vi_api_client._adapter import LiveAdapter
from vi_api_client._types import JsonValue
from vi_api_client.auth import AbstractAuth
from vi_api_client.const import API_BASE_URL, ENDPOINT_INSTALLATIONS
from vi_api_client.exceptions import (
    ViAuthError,
    ViConnectionError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViResponseError,
    ViServerInternalError,
    ViValidationError,
)
from vi_api_client.models import FeatureControl

INSTALLATIONS_URL = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
COMMAND_URL = f"{API_BASE_URL}/iot/v2/features/commands/setMode"


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


@pytest.fixture
def live_adapter(static_token_auth: AbstractAuth) -> LiveAdapter:
    """Return a live adapter whose HTTP requests go to ``mock_responses``."""
    return LiveAdapter(static_token_auth)


def _command_control(uri: str) -> FeatureControl:
    """Build a command control targeting the given URI."""
    return FeatureControl(
        command_name="setMode",
        param_name="mode",
        required_params=["mode"],
        parent_feature_name="heating.mode",
        uri=uri,
    )


async def test_live_adapter_preserves_external_oauth_error() -> None:
    # Arrange: Configure auth to raise a response-shaped external OAuth error.
    oauth_error = _ExternalOAuthError(
        request_info=MagicMock(),
        history=(),
        status=400,
        message="Refresh token rejected",
        headers=MagicMock(),
    )
    adapter = LiveAdapter(_RaisingAuth(oauth_error))

    # Act: Read installations while the provider rejects the refresh token.
    with pytest.raises(_ExternalOAuthError) as raised_error:
        await adapter.get_installations()

    # Assert: The adapter passes on the provider-owned exception itself.
    assert raised_error.value is oauth_error


async def test_live_adapter_wraps_aiohttp_connection_error(
    live_adapter, mock_responses
) -> None:
    # Arrange: Make the connection drop before the installations response.
    mock_responses.get(INSTALLATIONS_URL, exception=True)

    # Act: Read installations while the connection drops.
    with pytest.raises(ViConnectionError) as raised_error:
        await live_adapter.get_installations()

    # Assert: The library error keeps the aiohttp failure as its cause.
    assert isinstance(raised_error.value.__cause__, aiohttp.ClientConnectionError)


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [
        pytest.param(400, ViValidationError, id="bad-request"),
        pytest.param(401, ViAuthError, id="unauthorized"),
        pytest.param(403, ViAuthError, id="forbidden"),
        pytest.param(404, ViNotFoundError, id="not-found"),
        pytest.param(418, ViError, id="unmapped-status"),
        pytest.param(422, ViValidationError, id="unprocessable"),
        pytest.param(429, ViRateLimitError, id="rate-limited"),
        pytest.param(500, ViServerInternalError, id="server-error"),
    ],
)
async def test_live_adapter_preserves_viessmann_error_type(
    live_adapter, mock_responses, status: int, expected_error: type[ViError]
) -> None:
    # Arrange: Return a structured Viessmann error from the HTTP boundary.
    payload = {
        "errorType": "DEVICE_COMMUNICATION_ERROR",
        "message": "Device communication failed",
        "viErrorId": "error-123",
    }

    mock_responses.get(INSTALLATIONS_URL, payload=payload, status=status)

    # Act: Read installations while the API reports a structured error.
    with pytest.raises(ViError) as raised_error:
        await live_adapter.get_installations()

    # Assert: The status selects the exact error class, not merely a subclass of
    # it, and the exception retains the API classification data.
    assert type(raised_error.value) is expected_error
    assert raised_error.value.error_id == "error-123"
    assert raised_error.value.error_type == "DEVICE_COMMUNICATION_ERROR"


async def test_live_adapter_exposes_validated_validation_details(
    live_adapter, mock_responses
) -> None:
    """Validated validation details stay dictionary-shaped on the exception."""
    # Arrange: Return one structured validation detail with an unknown field.
    payload: dict[str, JsonValue] = {
        "errorType": "VALIDATION_FAILED",
        "message": "Invalid command parameters",
        "viErrorId": "error-123",
        "validationErrors": [
            {"message": "out of range", "path": "slope", "extra": 1},
        ],
    }

    mock_responses.get(INSTALLATIONS_URL, payload=payload, status=400)

    # Act: Read installations while the API reports a validation error.
    with pytest.raises(ViValidationError) as raised_error:
        await live_adapter.get_installations()

    # Assert: The detail remains a dictionary with dictionary access and
    # contributes to the formatted message.
    error = raised_error.value
    details = error.validation_errors
    assert details is not None
    assert isinstance(details[0], dict)
    assert details[0]["message"] == "out of range"
    assert details[0].get("path") == "slope"
    assert "out of range (path: slope)" in str(error)


@pytest.mark.parametrize(
    "validation_errors",
    [
        pytest.param("not-a-list", id="string"),
        pytest.param(42, id="number"),
        pytest.param([{"message": "ok"}, "entry"], id="mixed-entries"),
        pytest.param(["entry", 42], id="no-object-entries"),
    ],
)
async def test_live_adapter_drops_unusable_validation_details(
    live_adapter,
    mock_responses,
    validation_errors: JsonValue,
) -> None:
    """Validation detail collections that violate the contract are not exposed."""
    # Arrange: Return a validationErrors value outside the detail contract.
    payload: dict[str, JsonValue] = {
        "errorType": "VALIDATION_FAILED",
        "message": "Invalid command parameters",
        "viErrorId": "error-123",
        "validationErrors": validation_errors,
    }

    mock_responses.get(INSTALLATIONS_URL, payload=payload, status=400)

    # Act: Read installations while the API reports a validation error.
    with pytest.raises(ViValidationError) as raised_error:
        await live_adapter.get_installations()

    # Assert: The unusable collection is not partially exposed.
    assert raised_error.value.validation_errors == []


async def test_live_adapter_drops_non_json_validation_details(
    live_adapter, mock_responses
) -> None:
    """Validation details JSON cannot represent are dropped, not raised."""
    # Arrange: Return validation details containing a non-finite number.
    payload: dict[str, JsonValue] = {
        "errorType": "VALIDATION_FAILED",
        "message": "Invalid command parameters",
        "viErrorId": "error-123",
        "validationErrors": [float("inf")],
    }

    mock_responses.get(INSTALLATIONS_URL, payload=payload, status=400)

    # Act: Read installations while the API reports a validation error.
    with pytest.raises(ViValidationError) as raised_error:
        await live_adapter.get_installations()

    # Assert: The HTTP error surfaces with the unusable details dropped.
    assert raised_error.value.validation_errors == []


async def test_live_adapter_drops_malformed_structured_error_fields(
    live_adapter, mock_responses
) -> None:
    """Malformed structured error fields must not reach the public exception."""
    # Arrange: Return structured error fields that violate their contracts.
    payload: dict[str, JsonValue] = {
        "errorType": 404,
        "message": {"text": "broken"},
        "viErrorId": ["not-an-id"],
    }

    mock_responses.get(INSTALLATIONS_URL, payload=payload, status=400)

    # Act: Read installations while the API reports malformed error fields.
    with pytest.raises(ViValidationError) as raised_error:
        await live_adapter.get_installations()

    # Assert: HTTP-level defaults replace every malformed field.
    error = raised_error.value
    assert error.error_id is None
    assert error.error_type is None
    assert error.validation_errors == []
    assert str(error) == "HTTP 400"


@pytest.mark.parametrize(
    ("retry_after_header", "expected_retry_after"),
    [
        pytest.param("12.5", 12.5, id="fractional-seconds"),
        pytest.param("0", 0.0, id="zero-seconds"),
        pytest.param("-5", None, id="negative-seconds"),
        pytest.param("inf", None, id="infinite-seconds"),
        pytest.param("nan", None, id="nan-seconds"),
        pytest.param("not-a-duration", None, id="not-a-duration"),
        pytest.param("Wed, 21 Oct 2015 07:28:00", None, id="date-without-timezone"),
        pytest.param(None, None, id="missing-header"),
    ],
)
async def test_client_normalizes_numeric_or_invalid_retry_after_without_retrying(
    vi_client,
    mock_responses,
    retry_after_header: str | None,
    expected_retry_after: float | None,
) -> None:
    """A 429 should expose numeric guidance through one client request."""
    # Arrange: Configure a rate-limited public client request.
    headers = (
        {"Retry-After": retry_after_header} if retry_after_header is not None else {}
    )
    mock_responses.get(INSTALLATIONS_URL, status=429, headers=headers)

    # Act: Read installations while the API is rate limiting.
    with pytest.raises(ViRateLimitError) as raised_error:
        await vi_client.get_installations()

    # Assert: The error carries the parsed guidance after a single request.
    assert raised_error.value.retry_after == expected_retry_after
    assert len(mock_responses.requests) == 1


@pytest.mark.parametrize(
    ("retry_after_header", "max_retry_after"),
    [
        pytest.param(
            lambda: format_datetime(
                datetime.now(UTC) + timedelta(seconds=30), usegmt=True
            ),
            30,
            id="future-date",
        ),
        pytest.param(lambda: "Wed, 21 Oct 2015 07:28:00 GMT", 0, id="past-date"),
    ],
)
async def test_client_normalizes_http_date_retry_after_without_retrying(
    vi_client,
    mock_responses,
    retry_after_header: Callable[[], str],
    max_retry_after: float,
) -> None:
    """A 429 HTTP-date header should become a non-negative delay in seconds."""
    # Arrange: Build the header when the test runs, so a future date stays in
    # the future regardless of collection time.
    mock_responses.get(
        INSTALLATIONS_URL,
        status=429,
        headers={"Retry-After": retry_after_header()},
    )

    # Act: Read installations while the API is rate limiting.
    with pytest.raises(ViRateLimitError) as raised_error:
        await vi_client.get_installations()

    # Assert: The date becomes a delay, clamped at zero for past dates, from one
    # request.
    assert raised_error.value.retry_after is not None
    assert 0 <= raised_error.value.retry_after <= max_retry_after
    assert len(mock_responses.requests) == 1


@pytest.mark.parametrize(
    "command_uri",
    [
        pytest.param(
            f"{API_BASE_URL}/iot/v2/features/commands/setMode", id="absolute-uri"
        ),
        pytest.param("/iot/v2/features/commands/setMode", id="rooted-uri"),
    ],
)
async def test_live_adapter_sends_commands_to_vi_api_uris(
    live_adapter, mock_responses, command_uri: str
) -> None:
    """Absolute and rooted command URIs resolve to the Vi API."""
    # Arrange: Accept the command at its absolute Vi API URL.
    mock_responses.post(COMMAND_URL, payload={"data": {"success": True}})

    # Act: Execute a command whose URI uses the given spelling.
    response = await live_adapter.execute_command(
        _command_control(command_uri), {"mode": "dhw"}
    )

    # Assert: The command reached the Vi API URL.
    assert response == {"data": {"success": True}}


@pytest.mark.parametrize(
    "command_uri",
    [
        pytest.param(
            "https://example.invalid/iot/v2/features/commands/setMode",
            id="foreign-host",
        ),
        pytest.param(
            f"{API_BASE_URL}.example.invalid/commands/setMode", id="lookalike-host"
        ),
        pytest.param("//example.invalid/commands/setMode", id="scheme-relative"),
        pytest.param("", id="empty"),
    ],
)
@pytest.mark.usefixtures("no_http_requests")
async def test_live_adapter_refuses_command_uris_outside_vi_api(
    live_adapter,
    command_uri: str,
) -> None:
    """Command URIs from API responses must not receive the bearer token."""

    with pytest.raises(ViResponseError, match="outside the Vi API"):
        await live_adapter.execute_command(
            _command_control(command_uri), {"mode": "dhw"}
        )


async def test_live_adapter_maps_non_json_error_bodies(
    live_adapter, mock_responses
) -> None:
    # Arrange: Return an HTML error page with a server error status.
    mock_responses.get(
        INSTALLATIONS_URL,
        status=502,
        body="<html>Bad Gateway</html>",
        content_type="text/html",
    )

    # Act: Read installations while a proxy returns an HTML error page.
    with pytest.raises(ViServerInternalError) as raised_error:
        await live_adapter.get_installations()

    # Assert: The error surfaces with the HTTP-level message.
    assert str(raised_error.value) == "Server Error 502: HTTP 502"


@pytest.mark.parametrize(
    ("status", "expected_error", "expected_message"),
    [
        pytest.param(
            401,
            ViAuthError,
            "Unauthorized: Device communication failed",
            id="unauthorized",
        ),
        pytest.param(
            403, ViAuthError, "Forbidden: Device communication failed", id="forbidden"
        ),
        pytest.param(
            404,
            ViNotFoundError,
            "Not Found: Device communication failed",
            id="not-found",
        ),
        pytest.param(
            418,
            ViError,
            "Unknown Error 418: Device communication failed",
            id="unmapped-status",
        ),
    ],
)
async def test_client_errors_name_the_http_status_in_their_message(
    vi_client,
    mock_responses,
    status: int,
    expected_error: type[ViError],
    expected_message: str,
) -> None:
    """Error messages say which HTTP failure occurred before the API message."""
    # Arrange: Return a structured Viessmann error for the installation read.

    mock_responses.get(
        INSTALLATIONS_URL,
        status=status,
        payload={"message": "Device communication failed"},
    )

    # Act: Read installations while the API reports an HTTP error.
    with pytest.raises(ViError) as raised_error:
        await vi_client.get_installations()

    # Assert: The exact error class and full message are stable for callers
    # that display them.
    assert type(raised_error.value) is expected_error
    assert str(raised_error.value) == expected_message
