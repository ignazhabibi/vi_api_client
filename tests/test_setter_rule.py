"""Tests for commands that are no setters: read-only features and run_command."""

import pytest
from builders import (
    build_device,
    load_fixture_device,
    load_fixture_json,
    unmask_identifiers,
)
from yarl import URL

from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES
from vi_api_client.models import Device

# The holiday fixture names its commands by these absolute URIs.
HOLIDAY_COMMAND_URL = (
    f"{API_BASE_URL}{ENDPOINT_FEATURES}/123/gateways/GW123/devices/0/"
    "features/heating.operating.programs.holiday/commands/{command_name}"
)


def _device_features_url(device: Device) -> str:
    """Return the feature filter URL of one device."""
    return (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
        f"{device.gateway_serial}/devices/{device.id}/features/filter"
    )


def _command_request_keys(mock_responses) -> list[tuple[str, URL]]:
    """Return the recorded requests that went to a command URI."""
    return [key for key in mock_responses.requests if "/commands/" in str(key[1])]


async def _refresh_holiday_device(vi_client, mock_responses) -> Device:
    """Return a refreshed device that reports the holiday API feature."""
    device = build_device("0")
    mock_responses.post(
        _device_features_url(device),
        payload={"data": load_fixture_json("holiday_program.json")},
    )
    return await vi_client.refresh_device(device, only_available=False)


async def test_run_command_schedules_a_holiday(vi_client, mock_responses):
    # Arrange: Refresh the holiday API feature and accept its schedule command.
    device = await _refresh_holiday_device(vi_client, mock_responses)
    schedule_url = HOLIDAY_COMMAND_URL.format(command_name="schedule")
    mock_responses.post(schedule_url, payload={"data": {"success": True}})

    # Act: Plan a holiday by naming the command and both dates.
    await vi_client.run_command(
        device,
        "heating.operating.programs.holiday",
        "schedule",
        {"start": "2026-12-24", "end": "2026-12-31"},
    )

    # Assert: The schedule command received exactly the supplied dates.
    (request,) = mock_responses.requests[("POST", URL(schedule_url))]
    assert await request.json() == {"start": "2026-12-24", "end": "2026-12-31"}


async def test_run_command_triggers_the_weekly_hygiene_run(vi_client, mock_responses):
    # Arrange: Refresh the Vitodens200W hygiene trigger, whose two commands
    # take the same start time and differ in the weekday.
    api_feature = next(
        api_feature
        for api_feature in unmask_identifiers(load_fixture_device("Vitodens200W"))[
            "data"
        ]
        if api_feature["feature"] == "heating.dhw.hygiene.trigger"
    )
    assert set(api_feature["commands"]) == {"triggerDaily", "triggerOncePerWeek"}
    device = build_device("0")
    mock_responses.post(_device_features_url(device), payload={"data": [api_feature]})
    weekly_url = api_feature["commands"]["triggerOncePerWeek"]["uri"]
    mock_responses.post(weekly_url, payload={"data": {"success": True}})
    device = await vi_client.refresh_device(device, only_available=False)

    # Act: Name the weekly command and supply all three parameters.
    await vi_client.run_command(
        device,
        "heating.dhw.hygiene.trigger",
        "triggerOncePerWeek",
        {"startHour": 2, "startMinute": 30, "weekday": "Mon"},
    )

    # Assert: Only the weekly command was sent, with the supplied parameters.
    assert _command_request_keys(mock_responses) == [("POST", URL(weekly_url))]
    (request,) = mock_responses.requests[("POST", URL(weekly_url))]
    assert await request.json() == {
        "startHour": 2,
        "startMinute": 30,
        "weekday": "Mon",
    }
    for feature_name in (
        "heating.dhw.hygiene.trigger.startHour",
        "heating.dhw.hygiene.trigger.startMinute",
    ):
        feature = device.get_feature(feature_name)
        assert feature is not None
        assert not feature.is_writable


async def test_run_command_requires_every_parameter_of_a_non_setter(
    vi_client, mock_responses
):
    # Arrange: Refresh the holiday API feature.
    device = await _refresh_holiday_device(vi_client, mock_responses)

    # Act and assert: Planning a holiday without its end date sends nothing.
    with pytest.raises(ValueError, match="is missing required parameter 'end'"):
        await vi_client.run_command(
            device,
            "heating.operating.programs.holiday",
            "schedule",
            {"start": "2026-12-24"},
        )
    assert _command_request_keys(mock_responses) == []


@pytest.mark.parametrize(
    "feature_name",
    [
        pytest.param("heating.operating.programs.holiday.start", id="start"),
        pytest.param("heating.operating.programs.holiday.end", id="end"),
    ],
)
async def test_set_feature_points_a_non_setter_feature_to_run_command(
    vi_client, mock_responses, feature_name
):
    # Arrange: Refresh the holiday API feature, whose dates only the
    # schedule and changeEndDate commands take.
    device = await _refresh_holiday_device(vi_client, mock_responses)
    feature = device.get_feature(feature_name)
    assert feature is not None
    assert not feature.is_writable
    assert feature.constraints is None

    # Act and assert: The write fails before a request and names the
    # alternative with the API feature's commands.
    with pytest.raises(ValueError) as raised_error:
        await vi_client.set_feature(device, feature_name, "2026-12-24")
    message = str(raised_error.value)
    assert f"Feature '{feature_name}' is read-only" in message
    assert "run_command" in message
    assert (
        "API feature 'heating.operating.programs.holiday' reports the commands "
        "changeEndDate, schedule, unschedule" in message
    )
    assert _command_request_keys(mock_responses) == []


async def test_set_feature_keeps_the_plain_error_without_reported_commands(
    vi_client, mock_responses
):
    # Arrange: Refresh a sensor whose API feature reports no command.
    device = build_device("0")
    mock_responses.post(
        _device_features_url(device),
        payload={
            "data": [
                {
                    "feature": "heating.sensors.temperature.outside",
                    "isEnabled": True,
                    "isReady": True,
                    "properties": {"value": {"type": "number", "value": 4.2}},
                    "commands": {},
                }
            ]
        },
    )
    device = await vi_client.refresh_device(device)

    # Act and assert: Nothing points to run_command.
    with pytest.raises(ValueError) as raised_error:
        await vi_client.set_feature(device, "heating.sensors.temperature.outside", 5)
    assert str(raised_error.value) == (
        "Feature 'heating.sensors.temperature.outside' is read-only."
    )
