"""Model builders and fixture loaders shared by several test modules."""

import json
import re
from pathlib import Path
from typing import Any

from vi_api_client import FeatureValue
from vi_api_client.auth import AbstractAuth
from vi_api_client.models import (
    Device,
    Feature,
    FeatureConstraints,
    Gateway,
    Installation,
    ParameterWriteBinding,
)
from vi_api_client.parsing import api_feature_to_flat_features

TEST_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
# The bundled product fixtures double as the offline test catalog.
BUNDLED_FIXTURES_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "vi_api_client" / "fixtures"
)


# A masked identifier in a bundled fixture: a whole string or path segment.
_MASKED_IDENTIFIER_PATTERN = re.compile(r'(?<=["/])#+(?=["/])')


class StaticTokenAuth(AbstractAuth):
    """Provide a static token for live client request-flow tests."""

    async def async_get_access_token(self) -> str:
        """Return the access token used by mocked HTTP requests."""
        return "access-token"


def load_fixture_json(path: str) -> Any:
    """Load a JSON payload from ``tests/fixtures``."""
    return json.loads((TEST_FIXTURES_DIR / path).read_text(encoding="utf-8"))


def load_fixture_device(name: str) -> Any:
    """Load a bundled product fixture, such as a device response, by name."""
    return json.loads(
        (BUNDLED_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")
    )


def load_fixture_features(name: str) -> list[Feature]:
    """Parse every feature of a bundled fixture device, as the client does."""
    return [
        feature
        for api_feature in load_fixture_device(name)["data"]
        for feature in api_feature_to_flat_features(api_feature)
    ]


def build_installation(installation_id: str) -> Installation:
    """Build one installation as account discovery returns it."""
    return Installation(
        id=installation_id, description="Home", alias="home", address={}
    )


def build_gateway(serial: str, installation_id: str) -> Gateway:
    """Build one gateway as account discovery returns it."""
    return Gateway(
        serial=serial, version="1.0", status="ok", installation_id=installation_id
    )


def build_device(
    device_id: str,
    installation_id: str = "installation-1",
    gateway_serial: str = "gateway-1",
) -> Device:
    """Build one unhydrated device as gateway discovery returns it.

    The defaults place the device on the shared test installation and gateway.
    """
    return Device(
        id=device_id,
        gateway_serial=gateway_serial,
        installation_id=installation_id,
        model_id=f"model-{device_id}",
        device_type="heating",
        status="connected",
    )


def build_write_binding(
    *,
    api_feature_name: str = "heating.curve",
    command_name: str = "setValue",
    parameter_name: str = "value",
    required_parameters: tuple[str, ...] = ("value",),
    uri: str = "/features/heating.curve/commands/setValue",
) -> ParameterWriteBinding:
    """Build an internal write binding for a writable feature.

    The binding type is internal; this helper lets repository tests build
    writable features the way the parser does.
    """
    return ParameterWriteBinding(
        api_feature_name=api_feature_name,
        command_name=command_name,
        parameter_name=parameter_name,
        required_parameters=required_parameters,
        uri=uri,
    )


def build_feature(
    name: str = "heating.curve.slope",
    value: FeatureValue = 1.4,
    binding: ParameterWriteBinding | None = None,
    *,
    unit: str | None = None,
    is_enabled: bool = True,
    is_ready: bool = True,
    constraints: FeatureConstraints | None = None,
) -> Feature:
    """Build one feature snapshot, enabled and ready unless stated otherwise.

    A writable feature defaults to empty constraint rules, like the parser.
    """
    if constraints is None and binding is not None:
        constraints = FeatureConstraints()
    return Feature(
        name=name,
        value=value,
        unit=unit,
        is_enabled=is_enabled,
        is_ready=is_ready,
        constraints=constraints,
        write_binding=binding,
    )


def unmask_identifiers(document: object) -> Any:
    """Replace each masked identifier in a fixture with digits of the same length.

    The result looks like an unredacted API response, so redaction tests can
    expect the bundled fixture back.
    """
    text = _MASKED_IDENTIFIER_PATTERN.sub(
        lambda match: "7" * len(match.group()), json.dumps(document)
    )
    return json.loads(text)
