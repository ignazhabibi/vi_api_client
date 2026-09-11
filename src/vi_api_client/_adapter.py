"""Private API adapters for live and fixture-backed client workflows."""

import logging
from typing import Any, Protocol

import aiohttp

from .auth import AbstractAuth
from .const import (
    API_BASE_URL,
    ENDPOINT_FEATURES,
    ENDPOINT_GATEWAYS,
    ENDPOINT_INSTALLATIONS,
)
from .exceptions import (
    ViAuthError,
    ViError,
    ViNotFoundError,
    ViRateLimitError,
    ViResponseError,
    ViServerInternalError,
    ViValidationError,
)
from .models import Device, FeatureControl
from .utils import mask_pii

_LOGGER = logging.getLogger(__name__)


class _DiscoveryAdapter(Protocol):
    """Retrieve API envelopes without constructing domain objects."""

    async def get_installations(self) -> dict[str, Any]: ...

    async def get_gateways(self) -> dict[str, Any]: ...

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> dict[str, Any]: ...

    async def get_features(
        self, device: Device, payload: dict[str, bool | list[str]]
    ) -> dict[str, Any]: ...

    async def get_gateway_features(
        self, devices: list[Device], payload: dict[str, bool]
    ) -> dict[str, Any]: ...


class _CommandAdapter(Protocol):
    """Execute feature commands without constructing domain objects."""

    async def execute_command(
        self, control: FeatureControl, parameters: dict[str, Any]
    ) -> dict[str, Any]: ...


class _LiveAdapter:
    """Retrieve live API envelopes through an authenticated request provider."""

    def __init__(self, auth: AbstractAuth) -> None:
        """Initialize the adapter with the request provider."""
        self._auth = auth

    async def get_installations(self) -> dict[str, Any]:
        """Return the installations API envelope."""
        return await self._get(ENDPOINT_INSTALLATIONS)

    async def get_gateways(self) -> dict[str, Any]:
        """Return the gateways API envelope."""
        return await self._get(ENDPOINT_GATEWAYS)

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> dict[str, Any]:
        """Return the devices API envelope."""
        return await self._get(
            f"{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/"
            f"{gateway_serial}/devices"
        )

    async def get_features(
        self, device: Device, payload: dict[str, bool | list[str]]
    ) -> dict[str, Any]:
        """Return the feature API envelope for one device."""
        return await self._post(
            f"{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
            f"{device.gateway_serial}/devices/{device.id}/features/filter",
            payload,
        )

    async def get_gateway_features(
        self, devices: list[Device], payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the gateway-scoped feature API envelope."""
        first_device = devices[0]
        return await self._post(
            f"{ENDPOINT_FEATURES}/{first_device.installation_id}/gateways/"
            f"{first_device.gateway_serial}/features/filter",
            payload,
        )

    async def execute_command(
        self, control: FeatureControl, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the command API envelope."""
        return await self._post(control.uri, parameters)

    async def _get(self, url: str) -> dict[str, Any]:
        """Execute a GET request through authentication."""
        return await self._request("GET", url)

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a POST request through authentication."""
        return await self._request("POST", url, json=payload)

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Return a validated JSON response envelope."""
        full_url = self._prepare_url(url)
        _LOGGER.debug("Request: %s %s", method, mask_pii(full_url))
        async with await self._auth.request(method, full_url, **kwargs) as response:
            await _raise_for_status(response)
            try:
                return await response.json()
            except (aiohttp.ClientError, ValueError) as error:
                raise ViResponseError(
                    "Successful API response was not valid JSON"
                ) from error

    @staticmethod
    def _prepare_url(url: str) -> str:
        """Return an absolute Vi API URL."""
        if url.startswith("http"):
            return url
        if not url.startswith("/"):
            url = f"/{url}"
        return f"{API_BASE_URL}{url}"


async def _raise_for_status(response: aiohttp.ClientResponse) -> None:
    """Raise the matching library error for an unsuccessful API response."""
    status = response.status
    if status < 400:
        return

    vi_error_id = None
    error_message = f"HTTP {status}"
    validation_details = []
    error_type = None
    try:
        data = await response.json()
        if isinstance(data, dict):
            vi_error_id = data.get("viErrorId")
            error_message = data.get("message", error_message)
            error_type = data.get("errorType")
            validation_details = data.get("validationErrors", [])
    except aiohttp.ClientError, ValueError:
        pass

    _LOGGER.error(
        "API Error %s (%s): %s (ID: %s)",
        status,
        error_type,
        error_message,
        vi_error_id,
    )
    if status in (401, 403):
        description = "Unauthorized" if status == 401 else "Forbidden"
        raise ViAuthError(f"{description}: {error_message}", vi_error_id, error_type)
    if status == 404:
        raise ViNotFoundError(f"Not Found: {error_message}", vi_error_id, error_type)
    if status == 429:
        raise ViRateLimitError("Rate Limit Exceeded", vi_error_id, error_type)
    if status in (400, 422):
        raise ViValidationError(
            error_message, vi_error_id, validation_details, error_type
        )
    if status >= 500:
        raise ViServerInternalError(
            f"Server Error {status}: {error_message}", vi_error_id, error_type
        )
    raise ViError(f"Unknown Error {status}: {error_message}", vi_error_id, error_type)
