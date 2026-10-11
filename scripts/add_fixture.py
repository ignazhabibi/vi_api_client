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
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing fixture and its catalog entry",
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


def add_fixture(
    export_path: Path, name: str, fixtures_dir: Path, *, replace: bool = False
) -> str:
    """Add an export as a fixture file and a discovery catalog entry.

    With `replace`, the export replaces a catalogued fixture of that name and
    its catalog entry instead; the entry keeps its model ID.

    Returns:
        A summary of the added or replaced fixture.

    Raises:
        FixtureError: If the name is invalid, taken (or, with `replace`, not
            catalogued), or the export is unusable.
    """
    if not _FIXTURE_NAME_PATTERN.fullmatch(name):
        raise FixtureError(
            f"Invalid fixture name {name!r}: use letters, digits, and hyphens"
        )
    catalog_path = fixtures_dir / "discovery.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    fixture_path = fixtures_dir / f"{name}.json"
    catalogued = {entry["fixtureName"]: entry for entry in catalog["devices"]}
    if replace:
        # Only catalogued names, so a typo cannot add a fixture by accident.
        if name not in catalogued:
            raise FixtureError(f"Fixture {name!r} does not exist")
    elif fixture_path.exists() or name in catalogued:
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
    # A replaced fixture keeps the model ID that consumers already see.
    model_id = catalogued[name]["modelId"] if replace else device["modelId"]
    entry = {
        "fixtureName": name,
        "modelId": model_id,
        "deviceType": device["deviceType"],
    }
    if isinstance(device.get("capturedAt"), str):
        entry["capturedAt"] = device["capturedAt"]
    other_entries = [item for item in catalog["devices"] if item["fixtureName"] != name]
    catalog["devices"] = sorted(
        [*other_entries, entry], key=lambda item: item["fixtureName"]
    )

    _write_json(fixture_path, {"data": redacted_export["data"]})
    _write_json(catalog_path, catalog)
    action = "Replaced" if replace else "Added"
    return (
        f"{action} fixture {name} ({entry['modelId']}, {entry['deviceType']}) "
        f"with {feature_count} features"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Add the export and report the result."""
    args = build_parser().parse_args(argv)
    try:
        summary = add_fixture(
            args.export, args.name, args.fixtures_dir, replace=args.replace
        )
    except FixtureError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(summary)
    print("Review the fixture diff, then run the quality gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
