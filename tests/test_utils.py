"""Unit tests for the CLI parameter parser and the display and PII helpers."""

import pytest

from vi_api_client.models import Feature
from vi_api_client.utils import format_feature, mask_pii, parse_cli_params


def _feature(value, unit=None) -> Feature:
    """Build one display-ready feature snapshot."""
    return Feature(
        name="heating.display",
        value=value,
        unit=unit,
        is_enabled=True,
        is_ready=True,
        control=None,
    )


def test_parse_key_value():
    """Test standard key=value parsing."""
    # Arrange: Create list of key=value strings with different types.
    inputs = ["slope=1.4", "shift=0", "mode=active", "enabled=true"]
    expected = {"slope": 1.4, "shift": 0, "mode": "active", "enabled": True}

    # Act: Parse the CLI parameters.
    params = parse_cli_params(inputs)

    # Assert: All supported value types are inferred from their string form.
    assert params == expected


def test_parse_json_string():
    """Test parsing a single JSON string."""
    # Arrange: Create single JSON object string input.
    # Note: in CLI, this comes as a list with one string
    inputs = ['{"slope": 1.4, "shift": 0}']
    expected = {"slope": 1.4, "shift": 0}

    # Act: Parse the JSON string argument.
    params = parse_cli_params(inputs)

    # Assert: The JSON object string is extracted as a parameter mapping.
    assert params == expected


def test_parse_mixed_types():
    """Test type inference."""
    # Arrange: Create key=value strings with int, float, bool, string values.
    inputs = ["int=42", "float=42.5", "bool_t=true", "bool_f=False", "str=hello"]

    # Act: Parse CLI params with automatic type inference.
    params = parse_cli_params(inputs)

    # Assert: Verify each value has correct type (int, float, bool, str).
    assert params["int"] == 42
    assert isinstance(params["int"], int)

    assert params["float"] == 42.5
    assert isinstance(params["float"], float)

    assert params["bool_t"] is True
    assert params["bool_f"] is False

    assert params["str"] == "hello"


def test_invalid_format():
    """Test invalid input format."""
    # Act and Assert: Invalid format should raise ValueError with specific message.
    with pytest.raises(ValueError, match="Expected key=value"):
        parse_cli_params(["invalid_arg"])


def test_nested_json_value():
    """Test parsing a JSON value within a key=value pair."""
    # Arrange: Create key=value with JSON dict as value.
    # e.g. schedule={"day": 1}
    inputs = ['schedule={"day": 1, "temp": 20}']

    # Act: Parse CLI params - value should be extracted as dict.
    params = parse_cli_params(inputs)

    # Assert: Nested JSON should be parsed as Python dict.
    assert isinstance(params["schedule"], dict)
    assert params["schedule"]["day"] == 1


@pytest.mark.parametrize("prefix", ["Bearer", "bearer", "BEARER"])
def test_mask_pii_redacts_bearer_tokens_case_insensitively(prefix):
    """Bearer token redaction should not depend on header capitalization."""
    # Arrange: Build an Authorization value with a secret token.
    token = "sensitive-token-value"

    # Act: Mask the Authorization value.
    masked_text = mask_pii(f"Authorization: {prefix} {token}")

    # Assert: The token should be absent regardless of the Bearer capitalization.
    assert token not in masked_text
    assert masked_text.endswith("Bearer ***")


def test_mask_pii_returns_empty_text_unchanged():
    """Masking empty input should stay a no-op."""
    # Act and assert: Empty text returns without any substitution.
    assert mask_pii("") == ""


def test_mask_pii_redacts_gateway_serials_in_urls():
    """Sixteen-digit gateway serials should be redacted in URLs."""
    # Arrange: Build a request log line containing a full gateway serial.
    text = "GET /iot/v2/features/installations/123/gateways/1234567890123456/devices/0"

    # Act: Mask the request log line.
    masked_text = mask_pii(text)

    # Assert: The serial is redacted while the URL shape remains readable.
    assert "1234567890123456" not in masked_text
    assert "gateways/****************" in masked_text


def test_mask_pii_redacts_installation_ids_in_contexts():
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
    # Act: Format the feature without a value.
    formatted = format_feature(_feature(None))

    # Assert: The placeholder keeps the display column occupied.
    assert formatted == "-"


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (5.5, "celsius", "5.5 celsius"),
        ("ready", None, "ready"),
        ([1.1, 2.2], "kilowattHour", "[1.1, 2.2] kilowattHour"),
        (list(range(12)), None, "List[12 items]"),
    ],
    ids=["value-with-unit", "value-without-unit", "short-list", "long-list"],
)
def test_format_feature_renders_values_and_lists(value, unit, expected):
    """Scalar and list values should render with their unit when present."""
    # Act: Format the feature value in its given shape.
    formatted = format_feature(_feature(value, unit))

    # Assert: The rendered text matches the documented display form.
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
    formatted = format_feature(_feature(schedule))

    # Assert: Active days render as compact slot ranges and junk is skipped.
    assert formatted == "Mo[06:00-22:00] Tu[06:00-22:00] Fr[?-22:00]"


def test_format_feature_renders_slotless_schedules_as_empty():
    """Schedules without usable slots should render the empty marker."""
    # Arrange: Build a schedule whose days carry no usable slots.
    schedule = {"mon": [], "tue": [], "wed": []}

    # Act: Format the slotless schedule feature.
    formatted = format_feature(_feature(schedule))

    # Assert: The empty marker communicates the absence of slots.
    assert formatted == "(empty)"


def test_parse_cli_params_returns_empty_mapping_without_arguments():
    """No parameter arguments should yield an empty mapping."""
    # Act and assert: An empty argument list parses to no parameters.
    assert parse_cli_params([]) == {}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["{not-json"], "could not be parsed"),
    ],
    ids=["malformed-json"],
)
def test_parse_cli_params_rejects_malformed_json_arguments(arguments, message):
    """JSON-looking single arguments that are unusable should reject clearly."""
    # Act and assert: The malformed JSON argument raises with its reason.
    with pytest.raises(ValueError, match=message):
        parse_cli_params(arguments)
