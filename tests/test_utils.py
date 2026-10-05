"""Unit tests for the CLI parameter parser and the display and PII helpers."""

import pytest
from builders import build_feature

from vi_api_client import FeatureValue
from vi_api_client.utils import format_feature, mask_pii, parse_cli_params


def test_parse_cli_params_accepts_one_json_object_argument():
    """A single JSON object argument becomes the parameter mapping."""
    params = parse_cli_params(['{"slope": 1.4, "shift": 0}'])

    assert params == {"slope": 1.4, "shift": 0}


def test_parse_cli_params_infers_value_types_from_key_value_pairs():
    """Key=value pairs carry numbers and booleans as typed values, not text."""
    # Arrange: Create key=value strings with int, float, bool, string values.
    inputs = ["int=42", "float=42.5", "bool_t=true", "bool_f=False", "str=hello"]

    # Act: Parse CLI params with automatic type inference.
    params = parse_cli_params(inputs)

    # Assert: Equality alone would accept 42.0 for 42 and 1 for True.
    assert params == {
        "int": 42,
        "float": 42.5,
        "bool_t": True,
        "bool_f": False,
        "str": "hello",
    }
    assert isinstance(params["int"], int)
    assert isinstance(params["float"], float)
    assert params["bool_t"] is True
    assert params["bool_f"] is False


def test_parse_cli_params_parses_json_values_inside_pairs():
    """A JSON value on the right of a key=value pair is decoded."""
    params = parse_cli_params(['schedule={"day": 1, "temp": 20}'])

    assert params == {"schedule": {"day": 1, "temp": 20}}


def test_parse_cli_params_returns_empty_mapping_without_arguments():
    """No parameter arguments should yield an empty mapping."""
    assert parse_cli_params([]) == {}


def test_parse_cli_params_rejects_arguments_without_equals_sign():
    """Arguments that are neither JSON nor key=value pairs are rejected."""
    with pytest.raises(ValueError, match="Expected key=value"):
        parse_cli_params(["invalid_arg"])


def test_parse_cli_params_rejects_malformed_json_arguments():
    """JSON-looking single arguments that are unusable should reject clearly."""
    with pytest.raises(ValueError, match="could not be parsed"):
        parse_cli_params(["{not-json"])


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("Bearer", id="title"),
        pytest.param("bearer", id="lower"),
        pytest.param("BEARER", id="upper"),
    ],
)
def test_mask_pii_redacts_bearer_tokens_case_insensitively(prefix):
    """Bearer token redaction should not depend on header capitalization."""
    token = "sensitive-token-value"

    masked_text = mask_pii(f"Authorization: {prefix} {token}")

    assert token not in masked_text
    assert masked_text.endswith("Bearer ***")


def test_mask_pii_returns_empty_text_unchanged():
    """Masking empty input should stay a no-op."""
    assert mask_pii("") == ""


def test_mask_pii_redacts_gateway_serials_in_urls():
    """Sixteen-digit gateway serials should be redacted in URLs."""
    text = "GET /iot/v2/features/installations/123/gateways/1234567890123456/devices/0"

    masked_text = mask_pii(text)

    assert "1234567890123456" not in masked_text
    assert "gateways/****************" in masked_text


def test_mask_pii_redacts_gateway_serials_in_compact_json():
    """Serials in JSON without a space after the colon should be redacted."""
    text = '{"serial":"1234567890123456"}'

    masked_text = mask_pii(text)

    assert "1234567890123456" not in masked_text
    assert 'serial":"****************' in masked_text


def test_mask_pii_redacts_installation_ids_in_paths_and_labels():
    """Installation IDs should be redacted in paths and labeled contexts."""
    # Arrange: Build log lines with installation IDs in two known contexts.
    path_text = "GET /iot/v2/features/installations/123456/gateways/GW1"
    label_text = "Using installation ID: 12345"

    # Act: Mask both log lines.
    masked_path = mask_pii(path_text)
    masked_label = mask_pii(label_text)

    # Assert: The IDs are redacted while their context words remain.
    assert "123456" not in masked_path
    assert "installations/****" in masked_path
    assert "12345" not in masked_label
    assert "ID: ****" in masked_label


def test_format_feature_renders_missing_values_as_dash():
    """Missing feature values should render as a visible dash."""
    formatted = format_feature(build_feature(value=None))

    assert formatted == "-"


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        pytest.param(5.5, "celsius", "5.5 celsius", id="value-with-unit"),
        pytest.param("ready", None, "ready", id="value-without-unit"),
        pytest.param(
            [1.1, 2.2], "kilowattHour", "[1.1, 2.2] kilowattHour", id="short-list"
        ),
        pytest.param(list(range(12)), None, "List[12 items]", id="long-list"),
    ],
)
def test_format_feature_renders_values_and_lists(value, unit, expected):
    """Scalar and list values should render with their unit when present."""
    formatted = format_feature(build_feature(value=value, unit=unit))

    assert formatted == expected


def test_format_feature_renders_daily_schedules_compactly():
    """Weekly schedules should render as one compact segment per active day."""
    # Arrange: Build a schedule with two active days plus unusable slot data.
    schedule = {
        "mon": [{"start": "06:00", "end": "22:00"}],
        "tue": [{"start": "06:00", "end": "22:00"}, "not-a-slot"],
        "wed": [],
        "thu": ["only-junk-slots"],
        "fri": [{"end": "22:00"}],
    }

    # Act: Format the schedule feature.
    formatted = format_feature(build_feature(value=schedule))

    # Assert: Active days render as compact slot ranges and junk is skipped.
    assert formatted == "Mo[06:00-22:00] Tu[06:00-22:00] Fr[?-22:00]"


def test_format_feature_renders_slotless_schedules_as_empty():
    """Schedules without usable slots should render the empty marker."""
    schedule: FeatureValue = {"mon": [], "tue": [], "wed": []}

    formatted = format_feature(build_feature(value=schedule))

    assert formatted == "(empty)"
