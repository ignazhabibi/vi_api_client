"""Integrity guards for the bundled fixture device data.

These tests ensure that the bundled JSON fixture data shipped to consumers
stays parseable by the library and consistent with the fixture catalog.
"""

import json
import re

import pytest

from vi_api_client.parsing import parse_feature_flat

# The catalog file shares the fixture directory but is not a device fixture.
CATALOG_FILE_NAME = "discovery.json"


def test_fixture_discovery_metadata_matches_bundled_device_fixtures(
    device_responses_dir, available_fixture_devices
):
    """Each bundled device fixture should have exactly one metadata definition."""
    # Arrange: Read the catalog that drives fixture enumeration and discovery.
    discovery_data = json.loads(
        (device_responses_dir / CATALOG_FILE_NAME).read_text(encoding="utf-8")
    )
    device_metadata = discovery_data["devices"]

    # Act: Compare catalog fixture names with bundled feature-response filenames.
    catalogued_fixture_names = {device["fixtureName"] for device in device_metadata}
    bundled_fixture_names = set(available_fixture_devices)

    # Assert: Metadata is complete, unique, and has the discovery identity fields.
    assert catalogued_fixture_names == bundled_fixture_names
    assert len(device_metadata) == len(catalogued_fixture_names)
    assert all(
        isinstance(device["modelId"], str) and isinstance(device["deviceType"], str)
        for device in device_metadata
    )


def test_fixture_data_parses_and_keeps_constraint_quality(
    available_fixture_devices, load_fixture_device
):
    """Verify that each fixture device file parses with sane write constraints."""
    # Arrange: Enumerate the shipped device fixtures through the shared helper.
    device_fixture_names = sorted(available_fixture_devices)

    for device_name in device_fixture_names:
        data = load_fixture_device(device_name)
        # Some fixtures wrap the list in {"data": [...]}, others are just [...]
        if isinstance(data, dict) and "data" in data:
            raw_features = data["data"]
        elif isinstance(data, list):
            raw_features = data
        else:
            # Fallback for single object fixture
            raw_features = [data]

        # Act: Parse all raw features using the flat architecture parser.
        all_features: list = []
        for raw_feature in raw_features:
            all_features.extend(parse_feature_flat(raw_feature))

        # Assert: Every fixture yields features with usable write constraints.
        assert all_features, f"Fixture data {device_name} resulted in 0 features"

        writables = [feature for feature in all_features if feature.is_writable]

        for curve in [
            feature for feature in writables if "heating.curve" in feature.name
        ]:
            if curve.control.param_name in ("slope", "shift"):
                assert curve.control.min is not None, (
                    f"{device_name}/{curve.name}: missing min constraint"
                )
                assert curve.control.max is not None, (
                    f"{device_name}/{curve.name}: missing max constraint"
                )

        for feature in [item for item in writables if item.control.pattern]:
            assert feature.control.pattern.startswith("^"), (
                f"{device_name}/{feature.name}: pattern is not anchored"
            )
            try:
                re.compile(feature.control.pattern)
            except re.error as error:
                pytest.fail(
                    f"{device_name}/{feature.name}: pattern "
                    f"{feature.control.pattern!r} is not a valid regex: {error}"
                )
