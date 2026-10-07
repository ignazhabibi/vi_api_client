"""Integrity guards for the bundled fixture device data.

These tests ensure that the bundled JSON fixture data shipped to consumers
stays parseable by the library and consistent with the fixture catalog.
"""

import re

import pytest
from builders import BUNDLED_FIXTURES_DIR, load_fixture_device, load_fixture_features

from vi_api_client import FixtureViClient

FIXTURE_DEVICES = FixtureViClient.get_available_fixture_devices()


# The discovery catalog and the event history responses share the fixture
# directory but are not device fixtures.
_NON_DEVICE_FIXTURES = {"discovery", "event_history", "event_history_final_page"}


def test_fixture_discovery_metadata_matches_bundled_device_fixtures():
    """Each bundled device fixture should have exactly one metadata definition."""
    # Arrange: Read the catalog and the device fixture files beside it.
    device_metadata = load_fixture_device("discovery")["devices"]
    device_files = sorted(
        path.stem
        for path in BUNDLED_FIXTURES_DIR.glob("*.json")
        if path.stem not in _NON_DEVICE_FIXTURES
    )

    # Act: Collect the fixture names the catalog declares.
    catalogued_fixture_names = [device["fixtureName"] for device in device_metadata]

    # Assert: Every file has exactly one entry with the discovery identity
    # fields, and the public catalog lists exactly the bundled devices.
    assert sorted(catalogued_fixture_names) == device_files
    assert FixtureViClient.get_available_fixture_devices() == device_files
    assert all(
        isinstance(device["modelId"], str) and isinstance(device["deviceType"], str)
        for device in device_metadata
    )


@pytest.mark.parametrize("device_name", FIXTURE_DEVICES, ids=FIXTURE_DEVICES)
def test_fixture_data_parses_and_keeps_constraint_quality(device_name):
    """Each fixture device parses into features with usable write constraints."""
    # Act: Parse all features of the device fixture.
    all_features = load_fixture_features(device_name)

    # Assert: The fixture yields features whose write constraints are usable.
    assert all_features
    controls = [
        (feature.name, feature.control)
        for feature in all_features
        if feature.control is not None
    ]
    for name, control in controls:
        # Curve settings without bounds would let consumers send invalid values.
        if "heating.curve" in name and control.parameter_name in ("slope", "shift"):
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
