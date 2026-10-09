"""Add a device export from `vi-client dump-device` as a bundled fixture."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PROJECT_ROOT / "src" / "vi_api_client" / "fixtures"

# Use the repository source the fixture is written into, not an installed copy.
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from vi_api_client import ViError, redact_sensitive  # noqa: E402
from vi_api_client.parsing import api_feature_to_flat_features  # noqa: E402

# Fixture names become file names and CLI arguments, such as "Vitocal250A".
_FIXTURE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


class FixtureError(Exception):
    """An export that cannot be added as a fixture."""


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "export", type=Path, help="JSON file written by `vi-client dump-device`"
    )
    parser.add_argument(
        "--name", required=True, help="Fixture name, for example Vitocal250A"
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=FIXTURES_DIR,
        help="Fixture directory with discovery.json (default: bundled fixtures)",
    )
    return parser


def _read_export(path: Path) -> dict[str, Any]:
    """Return the export document after checking its shape.

    Raises:
        FixtureError: If the file is not an export document.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureError(f"Cannot read {path}: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise FixtureError("The export must be an object with a 'data' list")
    device = document.get("device")
    if not isinstance(device, dict) or not all(
        isinstance(device.get(field), str) and device[field]
        for field in ("modelId", "deviceType")
    ):
        raise FixtureError(
            "The export must have a 'device' object with modelId and deviceType"
        )
    return document


def _check_parses(data: list[Any]) -> int:
    """Return the number of features the library parses from the export.

    Raises:
        FixtureError: If an API feature is malformed or two features share a name.
    """
    names: set[str] = set()
    for api_feature in data:
        if not isinstance(api_feature, dict):
            raise FixtureError("Every 'data' entry must be an object")
        try:
            features = api_feature_to_flat_features(api_feature)
        except ViError as error:
            raise FixtureError(
                f"The library cannot parse the export: {error}"
            ) from error
        for feature in features:
            if feature.name in names:
                raise FixtureError(f"Duplicate feature name: {feature.name}")
            names.add(feature.name)
    return len(names)


def _write_json(path: Path, document: object) -> None:
    """Write JSON in the format of the bundled fixtures."""
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def add_fixture(export_path: Path, name: str, fixtures_dir: Path) -> str:
    """Add an export as a fixture file and a discovery catalog entry.

    Returns:
        A summary of the added fixture.

    Raises:
        FixtureError: If the name is invalid or taken, or the export is unusable.
    """
    if not _FIXTURE_NAME_PATTERN.fullmatch(name):
        raise FixtureError(
            f"Invalid fixture name {name!r}: use letters, digits, and hyphens"
        )
    catalog_path = fixtures_dir / "discovery.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    fixture_path = fixtures_dir / f"{name}.json"
    catalogued_names = {entry["fixtureName"] for entry in catalog["devices"]}
    if fixture_path.exists() or name in catalogued_names:
        raise FixtureError(f"Fixture {name!r} already exists")

    export = _read_export(export_path)
    # Redaction keeps the document's shape, so the result is still an object.
    redacted_export = cast("dict[str, Any]", redact_sensitive(export))
    if redacted_export != export:
        print(
            "Warning: redacted sensitive data that the export still contained",
            file=sys.stderr,
        )
    feature_count = _check_parses(redacted_export["data"])

    device = redacted_export["device"]
    entry = {
        "fixtureName": name,
        "modelId": device["modelId"],
        "deviceType": device["deviceType"],
    }
    if isinstance(device.get("capturedAt"), str):
        entry["capturedAt"] = device["capturedAt"]
    catalog["devices"] = sorted(
        [*catalog["devices"], entry], key=lambda item: item["fixtureName"]
    )

    _write_json(fixture_path, {"data": redacted_export["data"]})
    _write_json(catalog_path, catalog)
    return (
        f"Added fixture {name} ({entry['modelId']}, {entry['deviceType']}) "
        f"with {feature_count} features"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Add the export and report the result."""
    args = build_parser().parse_args(argv)
    try:
        summary = add_fixture(args.export, args.name, args.fixtures_dir)
    except FixtureError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(summary)
    print("Review the fixture diff, then run the quality gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
