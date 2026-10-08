"""Tests for anonymized device fixture exports and identifier masking."""

import json
import re
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import ANY

import pytest
from builders import build_device, build_feature, load_fixture_device
from yarl import URL

from vi_api_client import FixtureViClient, mask_identifiers
from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES
from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import Device

FIXTURE_DEVICES = FixtureViClient.get_available_fixture_devices()

INSTALLATION_ID = "1234567"
GATEWAY_SERIAL = "7654321098765432"

# A masked identifier in a bundled fixture: a whole string or path segment.
_MASKED_SEGMENT_PATTERN = re.compile(r'(?<=["/])#+(?=["/])')


def _unmask(document: object) -> Any:
    """Replace each masked identifier with digits of the same length."""
    text = _MASKED_SEGMENT_PATTERN.sub(
        lambda match: "7" * len(match.group()), json.dumps(document)
    )
    return json.loads(text)


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


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param("7630175843100101", "################", id="serial"),
        pytest.param("123456", "######", id="six-digits"),
        pytest.param("12345", "12345", id="five-digits-kept"),
        pytest.param("0", "0", id="device-id-kept"),
        pytest.param(
            "https://api.example/installations/1234567/gateways/"
            "7630175843100101/devices/0/features",
            "https://api.example/installations/#######/gateways/"
            "################/devices/0/features",
            id="uri",
        ),
        pytest.param("/1234567/7654321/", "/#######/#######/", id="adjacent-segments"),
        pytest.param("abc1234567", "abc1234567", id="mixed-text-kept"),
        pytest.param("0030.0514.2221.0050", "0030.0514.2221.0050", id="version-kept"),
        pytest.param(1251105, 1251105, id="number-kept"),
        pytest.param(True, True, id="boolean-kept"),
        pytest.param(None, None, id="null-kept"),
    ],
)
def test_mask_identifiers_masks_long_digit_strings_and_path_segments(
    document, expected
):
    assert mask_identifiers(document) == expected


def test_mask_identifiers_zeroes_coordinates_and_keeps_other_values():
    # Arrange: A house location as the API reports it, with nested identifiers.
    document = {
        "feature": "heating.configuration.houseLocation",
        "gatewayId": "7630175843100101",
        "properties": {
            "altitude": {"type": "number", "unit": "meter", "value": 412},
            "latitude": {"type": "number", "unit": "degree", "value": 48.137},
            "longitude": {"type": "number", "unit": "degree", "value": 11.575},
        },
        "position": {"latitude": 52.52, "longitude": -13.4, "label": "home"},
        "1234567": ["7654321", 7654321],
    }
    original = deepcopy(document)

    # Act: Mask the document.
    masked = mask_identifiers(document)

    # Assert: Coordinates become 0, identifiers are masked, other values stay,
    # and the input is not modified.
    assert masked == {
        "feature": "heating.configuration.houseLocation",
        "gatewayId": "################",
        "properties": {
            "altitude": {"type": "number", "unit": "meter", "value": 412},
            "latitude": {"type": "number", "unit": "degree", "value": 0},
            "longitude": {"type": "number", "unit": "degree", "value": 0},
        },
        "position": {"latitude": 0, "longitude": 0, "label": "home"},
        "#######": ["#######", 7654321],
    }
    assert document == original


@pytest.mark.parametrize(
    "coordinate",
    [
        pytest.param({"type": "string", "value": "north"}, id="non-numeric-value"),
        pytest.param({"type": "number"}, id="no-value"),
        pytest.param(True, id="boolean"),
    ],
)
def test_mask_identifiers_keeps_coordinates_without_a_number(coordinate):
    assert mask_identifiers({"latitude": coordinate}) == {"latitude": coordinate}


@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES, ids=FIXTURE_DEVICES)
def test_mask_identifiers_restores_every_bundled_fixture_from_unmasked_data(
    fixture_device,
):
    # Arrange: Replace the fixture's masked identifiers with real-looking digits.
    fixture = load_fixture_device(fixture_device)
    unmasked = _unmask(fixture)
    assert unmasked != fixture

    # Act and assert: Masking yields the bundled fixture again.
    assert mask_identifiers(unmasked) == fixture


async def test_export_device_fixture_reads_raw_features_and_masks_them(
    vi_client, mock_responses
):
    # Arrange: Serve the unmasked Vitocal250A features with a real location
    # for a device that already carries a parsed feature snapshot.
    fixture = load_fixture_device("Vitocal250A")
    response = _unmask(fixture)
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
