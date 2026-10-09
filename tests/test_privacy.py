"""Tests for redacting sensitive data in text, JSON, and model objects."""

from copy import deepcopy
from dataclasses import replace

import pytest
from builders import (
    build_device,
    build_feature,
    load_fixture_device,
    unmask_identifiers,
)

from vi_api_client import (
    FeatureControl,
    FixtureViClient,
    redact_device,
    redact_feature,
    redact_sensitive,
)

FIXTURE_DEVICES = FixtureViClient.get_available_fixture_devices()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "POST https://api.example/installations/1234567/gateways/"
            "7630175843100101/devices/0/features",
            "POST https://api.example/installations/#######/gateways/"
            "################/devices/0/features",
            id="url",
        ),
        pytest.param(
            "Using installation ID: 1234567",
            "Using installation ID: #######",
            id="label",
        ),
        pytest.param(
            '{"serial":"7630175843100101"}', '{"serial":"################"}', id="json"
        ),
        pytest.param("123456", "######", id="six-digits"),
        pytest.param("12345", "12345", id="five-digits-kept"),
        pytest.param("device 0", "device 0", id="device-id-kept"),
        pytest.param(
            "version 0030.0514.2221.0050",
            "version 0030.0514.2221.0050",
            id="version-kept",
        ),
        pytest.param("2026-10-08T16:05:06Z", "2026-10-08T16:05:06Z", id="time-kept"),
        pytest.param("B_00049_VC252", "B_00049_VC252", id="product-family-kept"),
        pytest.param("abc1234567", "abc1234567", id="inside-word-kept"),
        pytest.param("", "", id="empty"),
    ],
)
def test_redact_sensitive_masks_identifiers_in_text(text, expected):
    assert redact_sensitive(text) == expected


@pytest.mark.parametrize("prefix", ["Bearer", "bearer", "BEARER"])
def test_redact_sensitive_replaces_bearer_tokens_in_text(prefix):
    redacted = redact_sensitive(f"Authorization: {prefix} abc.DEF-123_x~/+=")

    assert redacted == f"Authorization: {prefix} <redacted>"


def test_redact_sensitive_redacts_secrets_and_address_data_in_documents():
    # Arrange: A document with secrets, address data, and look-alike keys.
    document = {
        "access_token": "secret-access",
        "refreshToken": "secret-refresh",
        "client_secret": "secret-client",
        "password": "secret-password",
        "credentials": {"user": "me"},
        "token_type": "Bearer",
        "address": {"city": "Mock City", "street": "Main Street 1"},
        "Alias": "Home",
        "description": "Holiday house",
        "busAddress": 71,
        "missing_token": None,
    }

    # Act: Redact the document.
    redacted = redact_sensitive(document, placeholder="**REDACTED**")

    # Assert: Matching keys are replaced; look-alike keys and empty values stay.
    assert redacted == {
        "access_token": "**REDACTED**",
        "refreshToken": "**REDACTED**",
        "client_secret": "**REDACTED**",
        "password": "**REDACTED**",
        "credentials": "**REDACTED**",
        "token_type": "Bearer",
        "address": "**REDACTED**",
        "Alias": "**REDACTED**",
        "description": "**REDACTED**",
        "busAddress": 71,
        "missing_token": None,
    }


def test_redact_sensitive_masks_identifiers_and_coordinates_in_documents():
    # Arrange: An API feature with identifiers, a location, and free text.
    document = {
        "feature": "heating.configuration.houseLocation",
        "gatewayId": "7630175843100101",
        "uri": "https://api.example/installations/1234567/features",
        "properties": {
            "altitude": {"type": "number", "unit": "meter", "value": 412},
            "latitude": {"type": "number", "unit": "degree", "value": 48.137},
            "longitude": {"type": "number", "unit": "degree", "value": 11.575},
            "name": {"type": "string", "value": "Radiateurs"},
        },
        "position": {"latitude": 52.52, "longitude": -13.4},
        "1234567": ["7654321", 7654321, True, None],
    }
    original = deepcopy(document)

    # Act: Redact the document.
    redacted = redact_sensitive(document)

    # Assert: Identifiers become '#', coordinates 0; numbers, free text, and
    # the input stay unchanged.
    assert redacted == {
        "feature": "heating.configuration.houseLocation",
        "gatewayId": "################",
        "uri": "https://api.example/installations/#######/features",
        "properties": {
            "altitude": {"type": "number", "unit": "meter", "value": 412},
            "latitude": {"type": "number", "unit": "degree", "value": 0},
            "longitude": {"type": "number", "unit": "degree", "value": 0},
            "name": {"type": "string", "value": "Radiateurs"},
        },
        "position": {"latitude": 0, "longitude": 0},
        "#######": ["#######", 7654321, True, None],
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
def test_redact_sensitive_keeps_coordinates_without_a_number(coordinate):
    assert redact_sensitive({"latitude": coordinate}) == {"latitude": coordinate}


@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES, ids=FIXTURE_DEVICES)
def test_redact_sensitive_restores_every_bundled_fixture_from_unmasked_data(
    fixture_device,
):
    # Arrange: Replace the fixture's masked identifiers with real-looking digits.
    fixture = load_fixture_device(fixture_device)
    unmasked = unmask_identifiers(fixture)
    assert unmasked != fixture

    # Act and assert: Redaction yields the bundled fixture again.
    assert redact_sensitive(unmasked) == fixture


def test_redact_feature_redacts_the_value_and_control():
    # Arrange: A writable feature whose value and control carry identifiers.
    control = FeatureControl(
        command_name="setSerial",
        parameter_name="serial",
        required_parameters=["serial"],
        parent_feature_name="device.serial",
        uri="https://api.example/installations/1234567/commands/setSerial",
        options=["7630175843100101", {"access_token": "secret"}, "short"],
    )
    feature = build_feature(
        name="device.serial", value="7630175843100101", control=control
    )

    # Act: Redact the feature.
    redacted = redact_feature(feature, placeholder="**REDACTED**")

    # Assert: The value, URI, and options are redacted; the input is unchanged.
    assert redacted.value == "################"
    assert redacted.control is not None
    assert redacted.control.uri == (
        "https://api.example/installations/#######/commands/setSerial"
    )
    assert redacted.control.options == (
        "################",
        {"access_token": "**REDACTED**"},
        "short",
    )
    assert redacted.control.command_name == "setSerial"
    assert feature.value == "7630175843100101"


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        pytest.param(
            "heating.configuration.houseLocation.latitude", 48.137, 0, id="latitude"
        ),
        pytest.param("device.address", "Main Street 1", "<redacted>", id="address"),
        pytest.param("heating.circuits.0.name", "Radiateurs", "Radiateurs", id="name"),
        pytest.param("heating.sensors.temperature", 21.5, 21.5, id="number"),
        pytest.param("device.unset", None, None, id="none"),
    ],
)
def test_redact_feature_applies_the_rules_to_the_feature_name(name, value, expected):
    redacted = redact_feature(build_feature(name=name, value=value))

    assert redacted.value == expected
    assert redacted.control is None


def test_redact_device_redacts_identifiers_and_features():
    # Arrange: A device with real-looking identifiers and one serial feature.
    device = build_device(
        "1234567", installation_id="7654321", gateway_serial="7630175843100101"
    )
    device = replace(
        device,
        features=[build_feature(name="device.serial", value="7630175843100101")],
    )

    # Act: Redact the device.
    redacted = redact_device(device)

    # Assert: Identifiers and the feature value are masked; the rest stays.
    assert (redacted.id, redacted.installation_id, redacted.gateway_serial) == (
        "#######",
        "#######",
        "################",
    )
    feature = redacted.get_feature("device.serial")
    assert feature is not None
    assert feature.value == "################"
    assert (redacted.model_id, redacted.device_type, redacted.status) == (
        device.model_id,
        device.device_type,
        device.status,
    )
    assert device.id == "1234567"
