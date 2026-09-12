"""Fixture-backed client adapters and deterministic command simulation."""

import json
import logging
from pathlib import Path
from typing import Any, TypedDict, cast

from ._adapter import _CommandAdapter, _DiscoveryAdapter
from .client import ViClient
from .models import (
    Device,
    FeatureControl,
)

_LOGGER = logging.getLogger(__name__)


class _FixtureMetadata(TypedDict):
    """The discovery identity of one bundled feature fixture."""

    fixtureName: str
    modelId: str
    deviceType: str


class _FixtureDiscoveryData(TypedDict):
    """The bundled fixture discovery envelopes and metadata catalog."""

    installations: dict[str, list[dict[str, Any]]]
    gateways: dict[str, list[dict[str, Any]]]
    devices: list[_FixtureMetadata]


def _load_fixture_discovery_data() -> _FixtureDiscoveryData:
    """Load the bundled fixture discovery envelopes and metadata catalog."""
    fixture_path = Path(__file__).parent / "fixtures" / "discovery.json"
    with fixture_path.open(encoding="utf-8") as file:
        return cast(_FixtureDiscoveryData, json.load(file))


class _FixtureDiscoveryAdapter:
    """Return deterministic client envelopes without authentication or HTTP."""

    def __init__(self, device_name: str) -> None:
        """Initialize the adapter for a selected fixture device."""
        self._device_name = device_name
        self._discovery_data = _load_fixture_discovery_data()
        self._device_metadata = next(
            metadata
            for metadata in self._discovery_data["devices"]
            if metadata["fixtureName"] == device_name
        )
        self._feature_data: dict[str, Any] | None = None

    async def get_installations(self) -> dict[str, Any]:
        """Return the fixture installation envelope."""
        return {
            "data": [
                {
                    **self._discovery_data["installations"]["data"][0],
                    "description": self._discovery_data["installations"]["data"][0][
                        "description"
                    ].format(device_name=self._device_name),
                }
            ]
        }

    async def get_gateways(self) -> dict[str, Any]:
        """Return the fixture gateway envelope."""
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
        self, device: Device, payload: dict[str, bool | list[str]]
    ) -> dict[str, Any]:
        """Return the selected fixture's raw feature envelope."""
        return self._load_feature_data()

    async def get_gateway_features(
        self, devices: list[Device], payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the selected fixture's raw gateway-scoped feature envelope."""
        return self._load_feature_data()

    def _load_feature_data(self) -> dict[str, Any]:
        """Load the selected fixture feature envelope once."""
        if self._feature_data is None:
            fixture_path = (
                Path(__file__).parent / "fixtures" / f"{self._device_name}.json"
            )
            with fixture_path.open(encoding="utf-8") as file:
                self._feature_data = cast(dict[str, Any], json.load(file))
        return self._feature_data


class _FixtureCommandAdapter:
    """Return deterministic command responses without modifying fixtures."""

    async def execute_command(
        self, control: FeatureControl, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a successful fixture command response."""
        _LOGGER.debug(
            "Executing fixture command %r for feature %r (param: %s) with params: %s",
            control.command_name,
            control.parent_feature_name,
            control.param_name,
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
                Must correspond to a file in the fixtures directory.
        """
        self.device_name = device_name
        self._discovery_adapter: _DiscoveryAdapter = _FixtureDiscoveryAdapter(
            device_name
        )
        self._command_adapter: _CommandAdapter = _FixtureCommandAdapter()

    @staticmethod
    def get_available_fixture_devices() -> list[str]:
        """Return fixture names accepted by the fixture-backed client.

        Returns:
            Sorted fixture names from the bundled metadata catalog.
        """
        discovery_data = _load_fixture_discovery_data()
        return sorted(metadata["fixtureName"] for metadata in discovery_data["devices"])
