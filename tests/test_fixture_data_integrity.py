"""Integrity guards for the bundled fixture device data.

These tests ensure that the bundled JSON fixture data shipped to consumers
stays parseable by the library and consistent with the fixture catalog.
"""

import re

import pytest

from vi_api_client import FixtureViClient
from vi_api_client.models import Feature
from vi_api_client.parsing import api_feature_to_flat_features

FIXTURE_DEVICES = FixtureViClient.get_available_fixture_devices()


def test_fixture_discovery_metadata_matches_bundled_device_fixtures(
    fixture_discovery_catalog, available_fixture_devices
):
    """Each bundled device fixture should have exactly one metadata definition."""
    # Arrange: Read the catalog entries that drive enumeration and discovery.
    device_metadata = fixture_discovery_catalog["devices"]

    # Act: Compare catalog fixture names with bundled feature-response filenames.
    catalogued_fixture_names = {device["fixtureName"] for device in device_metadata}

    # Assert: Metadata is complete, unique, and has the discovery identity
    # fields, and the public catalog lists exactly the bundled devices.
    assert catalogued_fixture_names == set(available_fixture_devices)
    assert FixtureViClient.get_available_fixture_devices() == available_fixture_devices
    assert len(device_metadata) == len(catalogued_fixture_names)
    assert all(
        isinstance(device["modelId"], str) and isinstance(device["deviceType"], str)
        for device in device_metadata
    )


@pytest.mark.parametrize("device_name", FIXTURE_DEVICES, ids=FIXTURE_DEVICES)
def test_fixture_data_parses_and_keeps_constraint_quality(
    load_fixture_device, device_name
):
    """Each fixture device parses into features with usable write constraints."""
    # Arrange: Every device fixture is a feature collection response.
    raw_features = load_fixture_device(device_name)["data"]

    # Act: Parse all raw features of the device.
    all_features: list[Feature] = []
    for raw_feature in raw_features:
        all_features.extend(api_feature_to_flat_features(raw_feature))

    # Assert: The fixture yields features whose write constraints are usable.
    assert all_features
    controls = [
        (feature.name, feature.control)
        for feature in all_features
        if feature.control is not None
    ]
    for name, control in controls:
        # Curve settings without bounds would let consumers send invalid values.
        if "heating.curve" in name and control.param_name in ("slope", "shift"):
            assert control.min is not None, f"{name}: missing min constraint"
            assert control.max is not None, f"{name}: missing max constraint"
        if control.pattern:
            assert control.pattern.startswith("^"), f"{name}: pattern is not anchored"
            try:
                re.compile(control.pattern)
            except re.error as error:
                pytest.fail(
                    f"{name}: pattern {control.pattern!r} is not a valid regex: {error}"
                )
