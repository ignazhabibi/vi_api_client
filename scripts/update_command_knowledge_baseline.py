"""Regenerate the command knowledge baseline from the bundled fixtures.

The baseline records, per bundled device feature file, how the library writes
every writable feature and which commands every API feature reports. A
fixture with further gateway devices has one file per device, such as
``Vitocal200A`` and ``Vitocal200A.1``. A test
compares it with the current implementation, so an unintended parser change
shows up as a diff. ``scripts/add_fixture.py`` runs this script after adding
or refreshing a fixture; run it yourself after an intended parser change, and
review the diff it produces.
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

from vi_api_client.models import (  # noqa: E402
    OnOffWriteCommand,
    WriteCommand,
    write_command_of,
)
from vi_api_client.parsing import parse_api_features  # noqa: E402


def _write_command_document(write_command: WriteCommand) -> dict[str, Any]:
    """Return one write command as a JSON document.

    The command URIs are left out: they repeat the fixture's own data, so
    they would only enlarge the diff after a fixture refresh.
    """
    if isinstance(write_command, OnOffWriteCommand):
        return {
            "api_feature": write_command.api_feature_name,
            "on_command": write_command.on_command_name,
            "off_command": write_command.off_command_name,
        }
    return {
        "api_feature": write_command.api_feature_name,
        "command": write_command.command_name,
        "parameter": write_command.parameter_name,
        "required": list(write_command.required_parameters),
    }


def feature_files() -> list[str]:
    """Return the sorted names of every catalogued device feature file.

    Device "0" of a fixture reads ``<fixtureName>.json``; every further device
    reads ``<fixtureName>.<id>.json``.
    """
    catalog = json.loads((FIXTURES_DIR / "discovery.json").read_text(encoding="utf-8"))
    return sorted(
        file_name
        for entry in catalog["devices"]
        for file_name in [
            entry["fixtureName"],
            *(
                f"{entry['fixtureName']}.{device['id']}"
                for device in entry.get("additionalDevices", [])
            ),
        ]
    )


def build_fixture_baseline(fixture_device: str) -> dict[str, Any]:
    """Return the command knowledge of one bundled device feature file.

    Unavailable API features are included, so the baseline covers everything
    the fixture reports.
    """
    response = json.loads(
        (FIXTURES_DIR / f"{fixture_device}.json").read_text(encoding="utf-8")
    )
    features, command_map = parse_api_features(response["data"], only_available=False)
    return {
        "api_feature_commands": {
            api_feature_name: sorted(command.name for command in commands)
            for api_feature_name, commands in sorted(command_map.items())
        },
        "write_commands": {
            feature.name: _write_command_document(command)
            for feature in sorted(features, key=lambda feature: feature.name)
            if (command := write_command_of(feature)) is not None
        },
    }


def build_baseline() -> dict[str, Any]:
    """Return the command knowledge of every bundled device feature file."""
    return {
        file_name: build_fixture_baseline(file_name) for file_name in feature_files()
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
