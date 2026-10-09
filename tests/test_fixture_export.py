"""Tests for anonymized device fixture exports."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import ANY

import pytest
from builders import (
    build_device,
    build_feature,
    load_fixture_device,
    unmask_identifiers,
)
from yarl import URL

from vi_api_client import FixtureViClient
from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES
from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import Device

INSTALLATION_ID = "1234567"
GATEWAY_SERIAL = "7654321098765432"


def _device_features_url(device: Device) -> str:
    """Return the feature filter URL of one device."""
    return (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
        f"{device.gateway_serial}/devices/{device.id}/features/filter"
    )


def _set_house_latitude(document: dict, latitude: float) -> None:
    """Set the latitude of every house-location feature in a feature response."""
    for api_feature in document["data"]:
        if api_feature["feature"].endswith("houseLocation"):
            latitude_property = api_feature["properties"].get("latitude")
            if latitude_property is not None:
                latitude_property["value"] = latitude


async def test_export_device_fixture_reads_raw_features_and_redacts_them(
    vi_client, mock_responses
):
    # Arrange: Serve the unmasked Vitocal250A features with a real location
    # for a device that already carries a parsed feature snapshot.
    fixture = load_fixture_device("Vitocal250A")
    response = unmask_identifiers(fixture)
    _set_house_latitude(response, 48.137)
    assert "48.137" in json.dumps(response)
    device = replace(
        build_device(
            "0", installation_id=INSTALLATION_ID, gateway_serial=GATEWAY_SERIAL
        ),
        features=[build_feature()],
    )
    url = _device_features_url(device)
    mock_responses.post(url, payload=response)
    date_before = datetime.now(UTC).date().isoformat()

    # Act: Export the device.
    document = await vi_client.export_device_fixture(device)

    # Assert: The export requests every feature and holds the masked raw
    # features with the device identity, and no identifier or location.
    (request,) = mock_responses.requests[("POST", URL(url))]
    assert await request.json() == {"skipDisabled": False, "skipNotReady": False}
    assert document["data"] == fixture["data"]
    assert document["device"] in [
        {"modelId": "model-0", "deviceType": "heating", "capturedAt": captured_at}
        for captured_at in (date_before, datetime.now(UTC).date().isoformat())
    ]
    exported_text = json.dumps(document)
    for identifier in (INSTALLATION_ID, GATEWAY_SERIAL, "7777777", "48.137"):
        assert identifier not in exported_text


async def test_export_device_fixture_rejects_a_malformed_response(
    vi_client, mock_responses
):
    # Arrange: Return a feature response without a data list.
    device = build_device("0")
    mock_responses.post(_device_features_url(device), payload={"data": {}})

    # Act and assert: The malformed response raises the library error.
    with pytest.raises(ViResponseError, match="data must be a list"):
        await vi_client.export_device_fixture(device)


@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_client_exports_the_bundled_fixture():
    # Arrange: Discover the fixture device.
    client = FixtureViClient("Vitodens200W")
    installation = (await client.get_installations())[0]
    gateway = (await client.get_gateways())[0]
    (device,) = await client.get_devices(installation.id, gateway.serial)

    # Act: Export the fixture device.
    document = await client.export_device_fixture(device)

    # Assert: The export holds the bundled fixture and its catalog identity.
    assert document["data"] == load_fixture_device("Vitodens200W")["data"]
    assert document["device"] == {
        "modelId": "Vitodens200W",
        "deviceType": "heating",
        "capturedAt": ANY,
    }
