"""Redact sensitive data in log text, raw API documents, and model objects.

One rule set decides what is sensitive, so logs, fixture exports, and
consumer diagnostics redact the same data:

- Secrets: values of keys ending in ``token``, ``secret``, or ``password``,
  of ``credential``/``credentials`` keys, and bearer tokens in text.
- Address data: values of ``address``, ``alias``, and ``description`` keys.
- Identifiers: runs of six or more digits that are not part of a longer word,
  number, or version, such as installation IDs and gateway or device serials,
  in values, URIs, and text.
- Location: numeric ``latitude`` and ``longitude`` values.

Free text such as user-chosen circuit names, URLs apart from their
identifiers, raw device messages, and error codes are kept. Replacements keep
a document's structure and the type of numbers, so a redacted feature document
still parses as a fixture.
"""

import re
from dataclasses import replace
from typing import overload

from ._types import JsonValue
from .models import Device, Feature

_DEFAULT_PLACEHOLDER = "<redacted>"

_BEARER_TOKEN_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9\-_.~+/]+=*", re.IGNORECASE)
# Installation IDs and gateway and device serials are numeric. Shorter numbers
# such as the device ID "0", and digits inside words, numbers, and versions
# such as "0030.0514.2221.0050" or "B_00049_VC252", stay readable.
_IDENTIFIER_PATTERN = re.compile(r"(?<![\w.])[0-9]{6,}(?![\w.])")
_SECRET_KEY_PATTERN = re.compile(
    r"(token|secret|password)$|^credentials?$", re.IGNORECASE
)
# Matched as whole keys: "busAddress" is a bus position, not an address.
_ADDRESS_KEYS = frozenset({"address", "alias", "description"})
_COORDINATE_KEYS = frozenset({"latitude", "longitude"})


@overload
def redact_sensitive(value: str, *, placeholder: str = ...) -> str: ...


@overload
def redact_sensitive(value: JsonValue, *, placeholder: str = ...) -> JsonValue: ...


def redact_sensitive(
    value: JsonValue, *, placeholder: str = _DEFAULT_PLACEHOLDER
) -> JsonValue:
    """Return a copy of log text or a JSON value with sensitive data redacted.

    Text keeps its wording: bearer tokens become ``placeholder`` and
    identifiers become ``#`` characters, so a request URL stays readable. In
    a JSON value, secrets and address data become ``placeholder``,
    identifiers in strings and keys become ``#`` characters, and coordinates
    become ``0``. See the module documentation for the complete rules.

    Args:
        value: Log text or a JSON-compatible value, such as an API response.
        placeholder: Replacement for secrets and address data.

    Returns:
        The redacted copy with the same structure.
    """
    if isinstance(value, str):
        return _redact_text(value, placeholder)
    if isinstance(value, list):
        return [redact_sensitive(item, placeholder=placeholder) for item in value]
    if isinstance(value, dict):
        return {
            _redact_text(key, placeholder): _redact_entry(key, item, placeholder)
            for key, item in value.items()
        }
    return value


def redact_feature(
    feature: Feature, *, placeholder: str = _DEFAULT_PLACEHOLDER
) -> Feature:
    """Return a copy of a feature with sensitive data redacted.

    The value follows `redact_sensitive`, with the last part of the feature
    name as its key: the value of ``....houseLocation.latitude`` becomes 0.
    The control's URI and options are redacted the same way.

    Args:
        feature: The feature to redact.
        placeholder: Replacement for secrets and address data.

    Returns:
        The redacted copy; the input is not changed.
    """
    value_key = feature.name.rsplit(".", 1)[-1]
    control = feature.control
    if control is not None:
        control = replace(
            control,
            uri=_redact_text(control.uri, placeholder),
            options=(
                None
                if control.options is None
                else [
                    redact_sensitive(option, placeholder=placeholder)
                    for option in control.options
                ]
            ),
        )
    return replace(
        feature,
        value=_redact_entry(value_key, feature.value, placeholder),
        control=control,
    )


def redact_device(device: Device, *, placeholder: str = _DEFAULT_PLACEHOLDER) -> Device:
    """Return a copy of a device with its identifiers and features redacted.

    The installation ID, gateway serial, and device ID follow the identifier
    rule, and every feature goes through `redact_feature`.

    Args:
        device: The device to redact.
        placeholder: Replacement for secrets and address data.

    Returns:
        The redacted copy; the input is not changed.
    """
    return replace(
        device,
        id=_redact_text(device.id, placeholder),
        gateway_serial=_redact_text(device.gateway_serial, placeholder),
        installation_id=_redact_text(device.installation_id, placeholder),
        features=[
            redact_feature(feature, placeholder=placeholder)
            for feature in device.features
        ],
    )


def _redact_text(text: str, placeholder: str) -> str:
    """Return text with bearer tokens and identifiers redacted."""
    text = _BEARER_TOKEN_PATTERN.sub(rf"\g<1>{placeholder}", text)
    return _IDENTIFIER_PATTERN.sub(lambda match: "#" * len(match.group()), text)


def _redact_entry(key: str, value: JsonValue, placeholder: str) -> JsonValue:
    """Return the redacted value of one keyed entry."""
    if value is None:
        return None
    if _SECRET_KEY_PATTERN.search(key) or key.casefold() in _ADDRESS_KEYS:
        return placeholder
    redacted = redact_sensitive(value, placeholder=placeholder)
    if key.casefold() in _COORDINATE_KEYS:
        return _zero_coordinate(redacted)
    return redacted


def _zero_coordinate(coordinate: JsonValue) -> JsonValue:
    """Return a coordinate, or its property object, with the number set to 0."""
    if _is_number(coordinate):
        return 0
    if isinstance(coordinate, dict) and _is_number(coordinate.get("value")):
        return {**coordinate, "value": 0}
    return coordinate


def _is_number(value: JsonValue) -> bool:
    """Return whether a JSON value is a number rather than a boolean."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)
