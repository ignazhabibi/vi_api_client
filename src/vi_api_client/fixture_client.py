"""Fixture-backed client adapters and deterministic command simulation."""

import json
import logging
from pathlib import Path
from typing import Any, TypedDict, cast

from ._adapter import CommandAdapter, DiscoveryAdapter
from ._types import JsonValue
from .client import ViClient
from .models import Device, FeatureControl

_LOGGER = logging.getLogger(__name__)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


class _FixtureMetadata(TypedDict):
    """The discovery identity of one bundled feature fixture."""

    fixtureName: str
    modelId: str
    deviceType: str


class _FixtureDiscoveryData(TypedDict):
    """The bundled fixture discovery responses and metadata catalog."""

    installations: dict[str, list[dict[str, Any]]]
    gateways: dict[str, list[dict[str, Any]]]
    devices: list[_FixtureMetadata]


def _read_fixture_file(file_name: str) -> dict[str, Any]:
    """Read one bundled JSON fixture file."""
    with (_FIXTURES_DIR / file_name).open(encoding="utf-8") as file:
        return cast(dict[str, Any], json.load(file))


def _load_fixture_discovery_data() -> _FixtureDiscoveryData:
    """Load the bundled fixture discovery responses and metadata catalog."""
    return cast(_FixtureDiscoveryData, _read_fixture_file("discovery.json"))


class _FixtureDiscoveryAdapter:
    """Return deterministic API responses without authentication or HTTP."""

    def __init__(self, device_name: str) -> None:
        """Initialize the adapter for a selected fixture device.

        Raises:
            ValueError: If the device name is not in the fixture catalog.
        """
        self._device_name = device_name
        self._discovery_data = _load_fixture_discovery_data()
        catalog = {
            metadata["fixtureName"]: metadata
            for metadata in self._discovery_data["devices"]
        }
        if device_name not in catalog:
            available = ", ".join(sorted(catalog))
            raise ValueError(
                f"Unknown fixture device {device_name!r}. Available: {available}"
            )
        self._device_metadata = catalog[device_name]

    async def get_installations(self) -> dict[str, Any]:
        """Return the fixture installation response named after the device."""
        installation = self._discovery_data["installations"]["data"][0]
        description = installation["description"].format(device_name=self._device_name)
        return {"data": [{**installation, "description": description}]}

    async def get_gateways(self) -> dict[str, Any]:
        """Return the fixture gateway response."""
        return self._discovery_data["gateways"]

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> dict[str, Any]:
        """Return the selected fixture as one deterministic device."""
        return {
            "data": [
                {
                    "id": "0",
                    "modelId": self._device_metadata["modelId"],
                    "deviceType": self._device_metadata["deviceType"],
                    "status": "connected",
                }
            ]
        }

    async def get_features(
        self, device: Device, payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the selected fixture's raw feature response."""
        return _read_fixture_file(f"{self._device_name}.json")

    async def get_gateway_features(
        self, installation_id: str, gateway_serial: str, payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the selected fixture's raw gateway-scoped feature response."""
        return _read_fixture_file(f"{self._device_name}.json")

    async def get_event_history(
        self, installation_id: str, params: dict[str, int | str]
    ) -> dict[str, Any]:
        """Return the bundled first page or the observed empty final page.

        The bundled fixtures model a two-page window: a request without a
        cursor serves the bundled first page with its continuation cursor,
        and any cursor request serves the final empty page observed from the
        live API.
        """
        if "cursor" in params:
            return _read_fixture_file("event_history_final_page.json")
        return _read_fixture_file("event_history.json")


class _FixtureCommandAdapter:
    """Return deterministic command responses without modifying fixtures."""

    async def execute_command(
        self, control: FeatureControl, parameters: dict[str, JsonValue]
    ) -> dict[str, Any]:
        """Return a successful fixture command response."""
        _LOGGER.debug(
            "Executing fixture command %r for feature %r (parameter %r) with values %s",
            control.command_name,
            control.parent_feature_name,
            control.parameter_name,
            parameters,
        )
        return {"data": {"success": True, "reason": "Fixture Execution Success"}}


class FixtureViClient(ViClient):
    """Fixture-backed client that runs public workflows without network access.

    It uses bundled JSON fixture responses without authentication, which makes
    it useful for testing, CLI usage, and development.
    """

    def __init__(self, device_name: str) -> None:
        """Initialize the fixture client.

        Args:
            device_name: The name of the fixture device (e.g. "Vitodens200W").
                Must be listed by `get_available_fixture_devices`.

        Raises:
            ValueError: If the device name is not a bundled fixture device.
        """
        # ViClient.__init__ is skipped on purpose: it needs authentication
        # and creates the live adapter that the fixture adapters replace.
        self.device_name = device_name
        self._discovery_adapter: DiscoveryAdapter = _FixtureDiscoveryAdapter(
            device_name
        )
        self._command_adapter: CommandAdapter = _FixtureCommandAdapter()

    @staticmethod
    def get_available_fixture_devices() -> list[str]:
        """Return fixture names accepted by the fixture-backed client.

        Returns:
            Sorted fixture names from the bundled metadata catalog.
        """
        discovery_data = _load_fixture_discovery_data()
        return sorted(metadata["fixtureName"] for metadata in discovery_data["devices"])
