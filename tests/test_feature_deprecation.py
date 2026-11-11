"""Tests for the removal notice the API reports for an API feature."""

from dataclasses import replace
from datetime import date
from typing import Any

import pytest
from builders import build_device, load_fixture_device, load_fixture_features
from yarl import URL

from vi_api_client import FeatureDeprecation, redact_device, redact_feature
from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES
from vi_api_client.exceptions import ViResponseError
from vi_api_client.parsing import api_feature_to_flat_features

# Per bundled fixture: the API features that report a removal notice, and
# how many of them have no properties. The latter produce no flat feature,
# so their notice is not visible in the flat model.
DEPRECATED_API_FEATURES = {
    "Vitocal200S": (7, 5),
    "Vitocal222S": (17, 10),
    "Vitocal250A": (26, 6),
    "Vitocal333G-with-Vitovent300F": (7, 2),
    "Vitocharge03": (0, 0),
    "Vitodens200W": (8, 5),
    "Vitopure350": (2, 0),
}

_NOTICE = {"removalDate": "2024-09-15", "info": "replaced by heating.new.feature"}
_COMMAND_URL = (
    f"{API_BASE_URL}{ENDPOINT_FEATURES}/123/gateways/GW123/devices/0/"
    "features/heating.old.feature/commands/setValue"
)


def _api_feature(deprecated: object = None, **overrides: Any) -> dict[str, Any]:
    """Build one API feature, optionally with a `deprecated` entry."""
    api_feature: dict[str, Any] = {
        "feature": "heating.old.feature",
        "isEnabled": True,
        "isReady": True,
        "properties": {"value": {"type": "number", "value": 21}},
        "commands": {},
        **overrides,
    }
    if deprecated is not None:
        api_feature["deprecated"] = deprecated
    return api_feature


def test_deprecated_fixture_features_carry_the_notice_of_their_api_feature():
    # Act: Parse the Vitocal250A fixture.
    features = {
        feature.name: feature for feature in load_fixture_features("Vitocal250A")
    }

    # Assert: Every flat feature of a deprecated API feature carries its
    # notice, and the replacement carries none.
    replaced = FeatureDeprecation(
        removal_date=date(2024, 9, 15),
        info="replaced by heating.dhw.sensors.temperature.dhwCylinder",
    )
    for feature_name in (
        "heating.dhw.sensors.temperature.hotWaterStorage",
        "heating.dhw.sensors.temperature.hotWaterStorage.status",
    ):
        assert features[feature_name].deprecation == replaced
    assert features["heating.dhw.sensors.temperature.dhwCylinder"].deprecation is None
    assert features["heating.dhw.temperature.levels.min"].deprecation == (
        FeatureDeprecation(removal_date=date(2027, 3, 31), info="Not used anymore")
    )


@pytest.mark.parametrize(
    "fixture_device",
    sorted(DEPRECATED_API_FEATURES),
    ids=sorted(DEPRECATED_API_FEATURES),
)
def test_every_fixture_notice_reaches_all_flat_features_of_its_api_feature(
    fixture_device,
):
    # Arrange: Load the raw API features of one bundled fixture.
    api_features = load_fixture_device(fixture_device)["data"]

    # Act: Parse each API feature on its own to keep the link to its notice.
    deprecated_count = 0
    without_flat_features = 0
    for api_feature in api_features:
        features = api_feature_to_flat_features(api_feature)
        notice = api_feature.get("deprecated")
        if notice is None:
            # Assert: A regular API feature yields no notice.
            assert all(feature.deprecation is None for feature in features)
            continue
        deprecated_count += 1
        without_flat_features += not features
        # Assert: Every flat feature carries exactly the reported notice.
        expected = FeatureDeprecation(
            removal_date=date.fromisoformat(notice["removalDate"]),
            info=notice["info"],
        )
        assert all(feature.deprecation == expected for feature in features)

    # Assert: The fixture reports the expected number of notices.
    assert (deprecated_count, without_flat_features) == (
        DEPRECATED_API_FEATURES[fixture_device]
    )


@pytest.mark.parametrize(
    ("deprecated", "expected"),
    [
        pytest.param(
            _NOTICE,
            FeatureDeprecation(date(2024, 9, 15), "replaced by heating.new.feature"),
            id="date-and-info",
        ),
        pytest.param(
            {"info": "Not used anymore"},
            FeatureDeprecation(None, "Not used anymore"),
            id="missing-removal-date",
        ),
        pytest.param(
            {"removalDate": "2027-03-31"},
            FeatureDeprecation(date(2027, 3, 31), None),
            id="missing-info",
        ),
        pytest.param({}, FeatureDeprecation(None, None), id="empty-notice"),
        pytest.param(
            {**_NOTICE, "replacement": {"feature": "heating.new.feature"}},
            FeatureDeprecation(date(2024, 9, 15), "replaced by heating.new.feature"),
            id="unknown-extra-key",
        ),
    ],
)
def test_notice_fields_are_optional_and_unknown_keys_are_ignored(deprecated, expected):
    # Act: Parse an API feature with the given notice.
    (feature,) = api_feature_to_flat_features(_api_feature(deprecated))

    # Assert: Missing fields are None and the info text is passed on verbatim.
    assert feature.deprecation == expected


@pytest.mark.parametrize(
    ("deprecated", "message"),
    [
        pytest.param(True, "deprecated must be an object", id="boolean-notice"),
        pytest.param("2024-09-15", "deprecated must be an object", id="text-notice"),
        pytest.param([_NOTICE], "deprecated must be an object", id="list-notice"),
        pytest.param(
            {"removalDate": 20240915},
            "removalDate must be a string",
            id="numeric-removal-date",
        ),
        pytest.param({"info": 5}, "info must be a string", id="numeric-info"),
        pytest.param(
            {"removalDate": "15.09.2024"},
            "removalDate must be an ISO date",
            id="local-date-format",
        ),
        pytest.param(
            {"removalDate": "2024-02-30"},
            "removalDate must be an ISO date",
            id="impossible-date",
        ),
        pytest.param(
            {"removalDate": "2024-09-15T00:00:00Z"},
            "removalDate must be an ISO date",
            id="date-with-time",
        ),
        pytest.param(
            {"removalDate": "20240915"},
            "removalDate must be an ISO date",
            id="compact-date",
        ),
    ],
)
def test_malformed_notices_fail_loudly(deprecated, message):
    # Act and assert: A known field with the wrong shape is a contract error.
    with pytest.raises(ViResponseError, match=message):
        api_feature_to_flat_features(_api_feature(deprecated))


def test_schedule_and_its_other_properties_carry_the_notice():
    # Arrange: A deprecated schedule API feature with a second property.
    api_feature = _api_feature(
        _NOTICE,
        feature="heating.old.schedule",
        properties={
            "active": {"type": "boolean", "value": True},
            "entries": {"type": "Schedule", "value": {"mon": []}},
        },
    )

    # Act: Parse the API feature.
    features = api_feature_to_flat_features(api_feature)

    # Assert: The schedule feature and the flat property both carry it.
    assert [feature.name for feature in features] == [
        "heating.old.schedule",
        "heating.old.schedule.active",
    ]
    assert all(feature.deprecation is not None for feature in features)


def test_time_series_and_its_consumption_aliases_carry_the_notice():
    # Arrange: A deprecated consumption time series, the kind of API feature
    # whose day and year values produce the synthetic aliases.
    api_feature = _api_feature(
        _NOTICE,
        feature="heating.power.consumption.heating",
        properties={
            "day": {"type": "array", "value": [1.5, 2.5], "unit": "kilowattHour"},
            "year": {"type": "array", "value": [300.0], "unit": "kilowattHour"},
        },
    )

    # Act: Parse the API feature.
    features = api_feature_to_flat_features(api_feature)

    # Assert: The single complex feature and both aliases carry the notice.
    assert [feature.name for feature in features] == [
        "heating.power.consumption.heating",
        "heating.power.consumption.heating.currentDay",
        "heating.power.consumption.heating.currentYear",
    ]
    expected = FeatureDeprecation(date(2024, 9, 15), "replaced by heating.new.feature")
    assert all(feature.deprecation == expected for feature in features)


def test_notice_takes_part_in_equality_and_repr():
    # Arrange: The same feature with and without a notice.
    (deprecated,) = api_feature_to_flat_features(_api_feature(_NOTICE))
    (regular,) = api_feature_to_flat_features(_api_feature())

    # Assert: The notice distinguishes the two and is visible in the repr.
    assert deprecated != regular
    assert replace(regular, deprecation=deprecated.deprecation) == deprecated
    assert "replaced by heating.new.feature" in repr(deprecated)
    assert "deprecation=None" in repr(regular)


async def test_deprecated_feature_is_written_like_any_other_and_keeps_its_notice(
    vi_client, mock_responses
):
    # Arrange: A deprecated writable API feature whose removal date has
    # passed. No bundled fixture reports a deprecated writable feature.
    api_feature = _api_feature(
        _NOTICE,
        commands={
            "setValue": {
                "isExecutable": True,
                "name": "setValue",
                "params": {"value": {"type": "number", "required": True}},
                "uri": _COMMAND_URL,
            }
        },
    )
    device = build_device("0", "123", "GW123")
    mock_responses.post(
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/123/gateways/GW123/devices/0/"
        "features/filter",
        payload={"data": [api_feature]},
    )
    mock_responses.post(_COMMAND_URL, payload={"data": {"success": True}})
    device = await vi_client.refresh_device(device)
    feature = device.get_feature("heating.old.feature")
    assert feature is not None
    assert feature.is_settable

    # Act: Write the feature.
    updated_device = await vi_client.set_feature(device, feature, 22)

    # Assert: The command is sent, and the command-updated snapshot keeps
    # the notice next to the new value.
    (request,) = mock_responses.requests[("POST", URL(_COMMAND_URL))]
    assert await request.json() == {"value": 22}
    updated_feature = updated_device.get_feature("heating.old.feature")
    assert updated_feature is not None
    assert updated_feature.value == 22
    assert updated_feature.deprecation == feature.deprecation


def test_redaction_keeps_the_notice_unchanged():
    # Arrange: A deprecated fixture feature and its device.
    features = load_fixture_features("Vitocal250A")
    feature = next(
        feature
        for feature in features
        if feature.name == "heating.dhw.sensors.temperature.hotWaterStorage"
    )
    device = replace(build_device("7654321"), features=features)

    # Act: Redact the feature and the device.
    redacted_feature = redact_feature(feature)
    redacted_device = redact_device(device)

    # Assert: The notice carries no identifier and passes through unredacted.
    assert redacted_feature.deprecation == feature.deprecation
    assert [feature.deprecation for feature in redacted_device.features] == [
        feature.deprecation for feature in features
    ]
