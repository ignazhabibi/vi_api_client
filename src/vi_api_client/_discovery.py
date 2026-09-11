"""Private adapters for installation and gateway discovery."""

from typing import Any, Protocol

from .connection import ViConnector
from .const import ENDPOINT_FEATURES, ENDPOINT_GATEWAYS, ENDPOINT_INSTALLATIONS
from .models import Device


class _DiscoveryAdapter(Protocol):
    """Retrieve raw client envelopes without constructing domain objects."""

    async def get_installations(self) -> dict[str, Any]:
        """Return the raw installations API envelope."""
        raise NotImplementedError

    async def get_gateways(self) -> dict[str, Any]:
        """Return the raw gateways API envelope."""
        raise NotImplementedError

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> dict[str, Any]:
        """Return the raw devices API envelope."""
        raise NotImplementedError

    async def get_features(
        self, device: Device, payload: dict[str, bool | list[str]]
    ) -> dict[str, Any]:
        """Return the raw feature API envelope."""
        raise NotImplementedError

    async def get_gateway_features(
        self, devices: list[Device], payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the raw gateway-scoped feature API envelope."""
        raise NotImplementedError


class _LiveDiscoveryAdapter:
    """Retrieve client envelopes through the authenticated HTTP connector."""

    def __init__(self, connector: ViConnector) -> None:
        """Initialize the adapter with its authenticated connector."""
        self._connector = connector

    async def get_installations(self) -> dict[str, Any]:
        """Return the raw installations API envelope."""
        return await self._connector.get(ENDPOINT_INSTALLATIONS)

    async def get_gateways(self) -> dict[str, Any]:
        """Return the raw gateways API envelope."""
        return await self._connector.get(ENDPOINT_GATEWAYS)

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> dict[str, Any]:
        """Return the raw devices API envelope."""
        url = (
            f"{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/"
            f"{gateway_serial}/devices"
        )
        return await self._connector.get(url)

    async def get_features(
        self, device: Device, payload: dict[str, bool | list[str]]
    ) -> dict[str, Any]:
        """Return the raw feature API envelope."""
        url = (
            f"{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
            f"{device.gateway_serial}/devices/{device.id}/features/filter"
        )
        return await self._connector.post(url, payload)

    async def get_gateway_features(
        self, devices: list[Device], payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the raw gateway-scoped feature API envelope."""
        first_device = devices[0]
        url = (
            f"{ENDPOINT_FEATURES}/{first_device.installation_id}/gateways/"
            f"{first_device.gateway_serial}/features/filter"
        )
        return await self._connector.post(url, payload)
