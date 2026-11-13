"""Fixture-backed client adapters and deterministic command simulation."""

import json
import logging
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from ._adapter import CommandAdapter, DiscoveryAdapter
from ._types import JsonValue
from .client import ViClient
from .models import Device

_LOGGER = logging.getLogger(__name__)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


class _FixtureDeviceIdentity(TypedDict):
    """The discovery identity of one device on a fixture's gateway."""

    id: str
    modelId: str
    deviceType: str
    roles: NotRequired[list[str]]


class _FixtureMetadata(TypedDict):
    """The discovery identity of one bundled feature fixture.

    The top-level identity describes device "0", whose features are in
    ``<fixtureName>.json``. Each further device on the same gateway has its
    own features in ``<fixtureName>.<id>.json``.
    """

    fixtureName: str
    modelId: str
    deviceType: str
    roles: NotRequired[list[str]]
    additionalDevices: NotRequired[list[_FixtureDeviceIdentity]]


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
        self._feature_files = {"0": f"{device_name}.json"} | {
            device["id"]: f"{device_name}.{device['id']}.json"
            for device in self._device_metadata.get("additionalDevices", [])
        }

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
        """Return the selected fixture's catalogued devices.

        Device "0" comes first, followed by any further catalogued devices.
        A device carries the catalogued roles when the fixture has them.
        """
        main_device: _FixtureDeviceIdentity = {
            "id": "0",
            "modelId": self._device_metadata["modelId"],
            "deviceType": self._device_metadata["deviceType"],
        }
        if "roles" in self._device_metadata:
            main_device["roles"] = self._device_metadata["roles"]
        identities = [main_device, *self._device_metadata.get("additionalDevices", [])]
        return {
            "data": [{**identity, "status": "connected"} for identity in identities]
        }

    async def get_features(
        self, device: Device, payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the raw feature response of one fixture device.

        An ID the catalog does not list reads device "0", so callers that only
        know a placeholder device ID still read the fixture.
        """
        return _read_fixture_file(
            self._feature_files.get(device.id, self._feature_files["0"])
        )

    async def get_gateway_features(
        self, installation_id: str, gateway_serial: str, payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the raw gateway-scoped features of every fixture device."""
        responses = [_read_fixture_file(name) for name in self._feature_files.values()]
        if len(responses) == 1:
            return responses[0]
        return {"data": [item for response in responses for item in response["data"]]}

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

    async def send_command(
        self, uri: str, parameters: dict[str, JsonValue]
    ) -> dict[str, Any]:
        """Return a successful fixture command response."""
        _LOGGER.debug("Executing fixture command at %s with values %s", uri, parameters)
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
