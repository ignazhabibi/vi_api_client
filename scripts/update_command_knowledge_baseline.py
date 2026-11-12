"""Regenerate the command knowledge baseline from the bundled fixtures.

The baseline records, per bundled device fixture, how the library writes
every writable feature and which commands every API feature reports. A test
compares it with the current implementation, so an unintended parser change
shows up as a diff. Run this script after refreshing or adding a fixture, or
after an intended parser change, and review the diff it produces.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PROJECT_ROOT / "src" / "vi_api_client" / "fixtures"
BASELINE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "command_knowledge_baseline.json"

# Read the repository source the baseline describes, not an installed copy.
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from vi_api_client import FixtureViClient  # noqa: E402
from vi_api_client.models import (  # noqa: E402
    CommandPairWriteBinding,
    WriteBinding,
)
from vi_api_client.parsing import parse_api_features  # noqa: E402


def _binding_document(binding: WriteBinding) -> dict[str, Any]:
    """Return one write binding as a JSON document."""
    if isinstance(binding, CommandPairWriteBinding):
        return {
            "api_feature": binding.api_feature_name,
            "on_command": binding.on_command_name,
            "off_command": binding.off_command_name,
            "on_uri": binding.on_uri,
            "off_uri": binding.off_uri,
        }
    return {
        "api_feature": binding.api_feature_name,
        "command": binding.command_name,
        "parameter": binding.parameter_name,
        "required": list(binding.required_parameters),
        "uri": binding.uri,
    }


def build_fixture_baseline(fixture_device: str) -> dict[str, Any]:
    """Return the command knowledge of one bundled fixture.

    Unavailable API features are included, so the baseline covers everything
    the fixture reports.
    """
    response = json.loads(
        (FIXTURES_DIR / f"{fixture_device}.json").read_text(encoding="utf-8")
    )
    features, commands_by_api_feature = parse_api_features(
        response["data"], only_available=False
    )
    return {
        "api_feature_commands": {
            api_feature_name: sorted(command.name for command in commands)
            for api_feature_name, commands in sorted(commands_by_api_feature.items())
        },
        "write_bindings": {
            feature.name: _binding_document(feature.write_binding)
            for feature in sorted(features, key=lambda feature: feature.name)
            if feature.write_binding is not None
        },
    }


def build_baseline() -> dict[str, Any]:
    """Return the command knowledge of every bundled fixture."""
    return {
        fixture_device: build_fixture_baseline(fixture_device)
        for fixture_device in FixtureViClient.get_available_fixture_devices()
    }


def main() -> int:
    """Write the baseline file and report where it went."""
    BASELINE_PATH.write_text(
        json.dumps(build_baseline(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {BASELINE_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
