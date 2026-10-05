"""Model builders and fixture loaders shared by several test modules."""

import json
from pathlib import Path
from typing import Any

from vi_api_client.models import Device, Feature, Gateway, Installation
from vi_api_client.parsing import api_feature_to_flat_features

TEST_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
# The bundled product fixtures double as the offline test catalog.
BUNDLED_FIXTURES_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "vi_api_client" / "fixtures"
)


def load_fixture_json(path: str) -> Any:
    """Load a JSON payload from ``tests/fixtures``."""
    return json.loads((TEST_FIXTURES_DIR / path).read_text(encoding="utf-8"))


def load_fixture_device(name: str) -> Any:
    """Load a bundled product fixture, such as a device response, by name."""
    return json.loads(
        (BUNDLED_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")
    )


def load_fixture_features(name: str) -> list[Feature]:
    """Parse every feature of a bundled fixture device, as the client does."""
    return [
        feature
        for api_feature in load_fixture_device(name)["data"]
        for feature in api_feature_to_flat_features(api_feature)
    ]


def build_installation(installation_id: str) -> Installation:
    """Build one installation as account discovery returns it."""
    return Installation(
        id=installation_id, description="Home", alias="home", address={}
    )


def build_gateway(serial: str, installation_id: str) -> Gateway:
    """Build one gateway as account discovery returns it."""
    return Gateway(
        serial=serial, version="1.0", status="ok", installation_id=installation_id
    )


def build_device(
    device_id: str,
    installation_id: str = "installation-1",
    gateway_serial: str = "gateway-1",
) -> Device:
    """Build one unhydrated device as gateway discovery returns it.

    The defaults place the device on the shared test installation and gateway.
    """
    return Device(
        id=device_id,
        gateway_serial=gateway_serial,
        installation_id=installation_id,
        model_id=f"model-{device_id}",
        device_type="heating",
        status="connected",
    )
