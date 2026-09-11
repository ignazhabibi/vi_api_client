"""Private adapters for installation and gateway discovery."""

from typing import Any, Protocol

from .connection import ViConnector
from .const import ENDPOINT_GATEWAYS, ENDPOINT_INSTALLATIONS


class _DiscoveryAdapter(Protocol):
    """Retrieve raw discovery envelopes without constructing domain objects."""

    async def get_installations(self) -> dict[str, Any]:
        """Return the raw installations API envelope."""
        raise NotImplementedError

    async def get_gateways(self) -> dict[str, Any]:
        """Return the raw gateways API envelope."""
        raise NotImplementedError


class _LiveDiscoveryAdapter:
    """Retrieve discovery envelopes through the authenticated HTTP connector."""

    def __init__(self, connector: ViConnector) -> None:
        """Initialize the adapter with its authenticated connector."""
        self._connector = connector

    async def get_installations(self) -> dict[str, Any]:
        """Return the raw installations API envelope."""
        return await self._connector.get(ENDPOINT_INSTALLATIONS)

    async def get_gateways(self) -> dict[str, Any]:
        """Return the raw gateways API envelope."""
        return await self._connector.get(ENDPOINT_GATEWAYS)
