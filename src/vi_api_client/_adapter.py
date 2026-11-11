"""Private API adapters for live and fixture-backed client workflows."""

import logging
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, cast
from urllib.parse import urljoin

import aiohttp

from ._types import JsonValue, ValidationDetail
from .auth import AbstractAuth
from .const import (
    API_BASE_URL,
    ENDPOINT_EVENT_HISTORY,
    ENDPOINT_FEATURES,
    ENDPOINT_GATEWAYS,
    ENDPOINT_INSTALLATIONS,
)
from .exceptions import (
    ViAuthError,
    ViCommandRejectedError,
    ViConnectionError,
    ViDeviceCommunicationError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViResponseError,
    ViServerInternalError,
    ViValidationError,
)
from .models import Device
from .privacy import redact_sensitive
from .validation import validate_json_value

_LOGGER = logging.getLogger(__name__)

# Failures while reading a response body after its headers arrived: the
# request was not completed, unlike a complete body that is not valid JSON.
_BODY_READ_ERRORS = (
    TimeoutError,
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
)


class DiscoveryAdapter(Protocol):
    """Return raw API responses; `ViClient` turns them into models.

    Kept separate from `CommandAdapter` so fixture adapters and test doubles
    implement only the role they exercise; `LiveAdapter` fulfills both.
    """

    async def get_installations(self) -> object: ...

    async def get_gateways(self) -> object: ...

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> object: ...

    async def get_features(
        self, device: Device, payload: dict[str, bool]
    ) -> object: ...

    async def get_gateway_features(
        self, installation_id: str, gateway_serial: str, payload: dict[str, bool]
    ) -> object: ...

    async def get_event_history(
        self, installation_id: str, params: dict[str, int | str]
    ) -> object: ...


class CommandAdapter(Protocol):
    """Execute feature commands without constructing domain objects.

    Kept separate from `DiscoveryAdapter` so command-focused test doubles do
    not need to stub discovery methods.
    """

    async def execute_command(
        self, uri: str, parameters: dict[str, JsonValue]
    ) -> object: ...


class LiveAdapter:
    """Return raw live API responses through an authenticated request provider."""

    def __init__(self, auth: AbstractAuth) -> None:
        """Initialize the adapter with the request provider."""
        self._auth = auth

    async def get_installations(self) -> object:
        """Return the raw installations API response."""
        return await self._request("GET", ENDPOINT_INSTALLATIONS)

    async def get_gateways(self) -> object:
        """Return the raw gateways API response."""
        return await self._request("GET", ENDPOINT_GATEWAYS)

    async def get_devices(self, installation_id: str, gateway_serial: str) -> object:
        """Return the raw devices API response."""
        return await self._request(
            "GET",
            f"{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/"
            f"{gateway_serial}/devices",
        )

    async def get_features(self, device: Device, payload: dict[str, bool]) -> object:
        """Return the raw feature API response for one device."""
        return await self._request(
            "POST",
            f"{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
            f"{device.gateway_serial}/devices/{device.id}/features/filter",
            json=payload,
        )

    async def get_gateway_features(
        self, installation_id: str, gateway_serial: str, payload: dict[str, bool]
    ) -> object:
        """Return the raw gateway-scoped feature API response."""
        return await self._request(
            "POST",
            f"{ENDPOINT_FEATURES}/{installation_id}/gateways/"
            f"{gateway_serial}/features/filter",
            json=payload,
        )

    async def get_event_history(
        self, installation_id: str, params: dict[str, int | str]
    ) -> object:
        """Return one raw event history API response."""
        return await self._request(
            "GET",
            f"{ENDPOINT_EVENT_HISTORY}/{installation_id}/events",
            params=params,
        )

    async def execute_command(
        self, uri: str, parameters: dict[str, JsonValue]
    ) -> object:
        """Return the raw command API response."""
        return await self._request("POST", uri, json=parameters, is_command=True)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, int | str] | None = None,
        json: Mapping[str, object] | None = None,
        is_command: bool = False,
    ) -> object:
        """Return the decoded JSON body of a successful response.

        The body is untrusted JSON of any shape; callers validate it.

        Raises:
            ViConnectionError: If the response body cannot be read completely.
            ViResponseError: If the URL is outside the Vi API or a successful
                response is not valid JSON.
        """
        full_url = _api_url(url)
        _LOGGER.debug("Request: %s %s", method, redact_sensitive(full_url))
        async with await self._auth.request(
            method, full_url, params=params, json=json
        ) as response:
            await _raise_for_status(response, is_command=is_command)
            try:
                return await response.json()
            except _BODY_READ_ERRORS as error:
                raise ViConnectionError(f"Network error: {error}") from error
            except (aiohttp.ClientError, ValueError) as error:
                raise ViResponseError(
                    "Successful API response was not valid JSON"
                ) from error


def _api_url(url: str) -> str:
    """Return the absolute Vi API URL for an endpoint path or command URI.

    Command URIs come from API responses, and every request carries the
    bearer token, so URLs outside the Vi API are refused rather than sent.

    Raises:
        ViResponseError: If the URL does not resolve inside the Vi API.
    """
    full_url = urljoin(API_BASE_URL, url)
    if not full_url.startswith(f"{API_BASE_URL}/"):
        raise ViResponseError(
            f"Refusing request outside the Vi API: {redact_sensitive(full_url)}"
        )
    return full_url


async def _raise_for_status(
    response: aiohttp.ClientResponse, *, is_command: bool = False
) -> None:
    """Raise the matching library error for an unsuccessful API response.

    The API's ``errorType`` decides the class before the HTTP status does:
    an unreachable device is not a caller mistake, so every response
    classified as ``DEVICE_COMMUNICATION_ERROR`` raises
    ``ViDeviceCommunicationError`` whatever its status. For other error
    types the HTTP status selects the class; the JSON error body only
    enriches it, so a missing or non-JSON body (such as a proxy error page)
    falls back to HTTP-level defaults instead of masking the status.

    Raises:
        ViAuthError: For 401 and 403.
        ViNotFoundError: For 404.
        ViRateLimitError: For 429, with parsed Retry-After guidance.
        ViValidationError: For 400 and 422, with validated details.
        ViServerInternalError: For 5xx.
        ViDeviceCommunicationError: For every ``DEVICE_COMMUNICATION_ERROR``
            error type, whatever the status.
        ViCommandRejectedError: For a command request answered with
            ``DEVICE_COMMUNICATION_ERROR`` and the rejection reason
            ``COMMAND_NOT_EXECUTABLE``.
        ViError: For any other unsuccessful status.
    """
    status = response.status
    if status < 400:
        return

    error_message = f"HTTP {status}"
    vi_error_id: str | None = None
    error_type: str | None = None
    validation_details: list[ValidationDetail] = []
    # The API documents extendedPayload as debugging information; the
    # rejection reason is the only field read for logic, and only on
    # command requests. Its text is appended to other error messages.
    rejection_reason: str | None = None
    try:
        data = await response.json()
    except TimeoutError, aiohttp.ClientError, ValueError:
        data = None
    if isinstance(data, dict):
        # The untyped aiohttp JSON boundary yields an unknown container shape;
        # each known error field is re-narrowed and validated below.
        error_body = cast("dict[str, Any]", data)
        vi_error_id = _text_or_none(error_body.get("viErrorId"))
        error_type = _text_or_none(error_body.get("errorType"))
        message = _text_or_none(error_body.get("message"))
        # An empty message, like the gateway-offline body's, keeps the
        # HTTP-level default so the text still names what happened.
        if message:
            error_message = message
        validation_details = _parse_validation_details(
            error_body.get("validationErrors")
        )
        rejection_reason = _extended_payload_reason(error_body.get("extendedPayload"))

    # The raised library exception carries all error details, so the log
    # stays at debug level to avoid double-reporting normal control flow.
    _LOGGER.debug(
        "API Error %s (%s): %s (ID: %s)",
        status,
        error_type,
        error_message,
        vi_error_id,
    )
    if (
        is_command
        and error_type == "DEVICE_COMMUNICATION_ERROR"
        and rejection_reason == "COMMAND_NOT_EXECUTABLE"
    ):
        raise ViCommandRejectedError(
            error_message, rejection_reason, vi_error_id, error_type
        )
    if error_type == "DEVICE_COMMUNICATION_ERROR":
        raise ViDeviceCommunicationError(
            _with_reason(error_message, rejection_reason), vi_error_id, error_type
        )
    if status in (401, 403):
        description = "Unauthorized" if status == 401 else "Forbidden"
        raise ViAuthError(
            _with_reason(f"{description}: {error_message}", rejection_reason),
            vi_error_id,
            error_type,
        )
    if status == 404:
        raise ViNotFoundError(
            _with_reason(f"Not Found: {error_message}", rejection_reason),
            vi_error_id,
            error_type,
        )
    if status == 429:
        raise ViRateLimitError(
            _with_reason("Rate Limit Exceeded", rejection_reason),
            vi_error_id,
            error_type,
            retry_after=_parse_retry_after(response.headers.get("Retry-After")),
        )
    if status in (400, 422):
        raise ViValidationError(
            _with_reason(error_message, rejection_reason),
            vi_error_id,
            validation_details,
            error_type,
        )
    if status >= 500:
        raise ViServerInternalError(
            _with_reason(f"Server Error {status}: {error_message}", rejection_reason),
            vi_error_id,
            error_type,
        )
    raise ViError(
        _with_reason(f"Unknown Error {status}: {error_message}", rejection_reason),
        vi_error_id,
        error_type,
    )


def _with_reason(message: str, reason: str | None) -> str:
    """Return the error message with a reported `extendedPayload.reason`."""
    return f"{message} (reason: {reason})" if reason else message


def _extended_payload_reason(value: object) -> str | None:
    """Return the only `extendedPayload` field the library reads.

    A missing or non-object ``extendedPayload`` degrades to no reason, so
    the response keeps the error class its error type and status suggest.
    """
    if isinstance(value, dict):
        # Runtime-checked container; an unknown extendedPayload shape is
        # ignored rather than validated, because it is debugging data.
        payload = cast("dict[str, Any]", value)
        return _text_or_none(payload.get("reason"))
    return None


def _text_or_none(value: object) -> str | None:
    """Return the value if it is text, otherwise None."""
    return value if isinstance(value, str) else None


def _parse_validation_details(value: object) -> list[ValidationDetail]:
    """Return validated validation details, dropping unusable collections.

    Every exposed detail is a string-keyed JSON object and unknown detail
    fields remain allowed. A collection that violates the shape — or that
    JSON cannot represent — is dropped entirely rather than masking the
    HTTP error being reported.
    """
    try:
        validated = validate_json_value(value, path="API validationErrors")
    except ViResponseError:
        return []
    if not isinstance(validated, list) or not all(
        isinstance(entry, dict) for entry in validated
    ):
        return []
    # Every entry was runtime-checked as a validated JSON object above.
    return cast("list[ValidationDetail]", validated)


def _parse_retry_after(value: str | None) -> float | None:
    """Return a non-negative retry duration from an HTTP Retry-After value.

    The header carries either delay seconds or an HTTP-date (RFC 9110). A
    date without a time zone is not a valid HTTP-date and cannot be compared
    with the current UTC time, so it yields None like any unusable value. A
    date in the past yields 0.
    """
    if value is None:
        return None

    try:
        numeric_delay = float(value)
    except ValueError:
        pass
    else:
        is_valid_delay = math.isfinite(numeric_delay) and numeric_delay >= 0
        return numeric_delay if is_valid_delay else None

    try:
        retry_at = parsedate_to_datetime(value)
    except IndexError, TypeError, ValueError:
        return None
    if retry_at.tzinfo is None:
        return None
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
