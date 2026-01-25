"""Unit tests for hysteresis feature parsing."""

from vi_api_client.parsing import parse_feature_flat


def test_hysteresis_parsing(load_fixture_json):
    # Arrange: Load fixture with hysteresis value and switch points.
    raw_feature = load_fixture_json("parsing/hysteresis_raw.json")

    # Act: Parse hysteresis feature - should create 3 separate features.
    features = parse_feature_flat(raw_feature)

    # Assert: Should create base hysteresis + switchOnValue + switchOffValue features.
    # We expect 3 features:
    # 1. heating.dhw.temperature.hysteresis (mapped from 'value', via setHysteresis)
    # 2. heating.dhw.temperature.hysteresis.switchOnValue (via setHysteresisSwitchOnValue)
    # 3. heating.dhw.temperature.hysteresis.switchOffValue (via setHysteresisSwitchOffValue)

    assert len(features) == 3

    feature_value = next(
        feature
        for feature in features
        if feature.name == "heating.dhw.temperature.hysteresis"
    )
    feature_on = next(
        feature for feature in features if feature.name.endswith(".switchOnValue")
    )
    feature_off = next(
        feature for feature in features if feature.name.endswith(".switchOffValue")
    )

    # Check Controls.
    assert feature_value.is_writable
    assert feature_value.control.command_name == "setHysteresis"

    # These currently FAIL because of the missing mapping logic.
    assert feature_on.is_writable
    assert feature_on.control.command_name == "setHysteresisSwitchOnValue"

    assert feature_off.is_writable
    assert feature_off.control.command_name == "setHysteresisSwitchOffValue"
