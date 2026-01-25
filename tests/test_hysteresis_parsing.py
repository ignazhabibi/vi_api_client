from vi_api_client.parsing import parse_feature_flat


def test_hysteresis_parsing(load_fixture_json):
    # --- ARRANGE ---
    raw_feature = load_fixture_json("parsing/hysteresis_raw.json")

    # --- ACT ---
    features = parse_feature_flat(raw_feature)

    # --- ASSERT ---
    # We expect 3 features:
    # 1. heating.dhw.temperature.hysteresis (mapped from 'value', via setHysteresis)
    # 2. heating.dhw.temperature.hysteresis.switchOnValue (via setHysteresisSwitchOnValue)
    # 3. heating.dhw.temperature.hysteresis.switchOffValue (via setHysteresisSwitchOffValue)

    assert len(features) == 3

    f_val = next(f for f in features if f.name == "heating.dhw.temperature.hysteresis")
    f_on = next(f for f in features if f.name.endswith(".switchOnValue"))
    f_off = next(f for f in features if f.name.endswith(".switchOffValue"))

    # Check Controls
    assert f_val.is_writable
    assert f_val.control.command_name == "setHysteresis"

    # These currently FAIL because of the missing mapping logic
    assert f_on.is_writable
    assert f_on.control.command_name == "setHysteresisSwitchOnValue"

    assert f_off.is_writable
    assert f_off.control.command_name == "setHysteresisSwitchOffValue"
