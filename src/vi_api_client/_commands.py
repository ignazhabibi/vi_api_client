"""Private adapters for feature command execution."""

from typing import Any, Protocol

from .connection import ViConnector
from .models import FeatureControl


class _CommandAdapter(Protocol):
    """Execute feature commands without constructing domain objects."""

    async def execute_command(
        self, control: FeatureControl, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the raw command response envelope."""
        raise NotImplementedError


class _LiveCommandAdapter:
    """Execute feature commands through the authenticated HTTP connector."""

    def __init__(self, connector: ViConnector) -> None:
        """Initialize the adapter with its authenticated connector."""
        self._connector = connector

    async def execute_command(
        self, control: FeatureControl, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the raw command response envelope."""
        return await self._connector.post(control.uri, parameters)
