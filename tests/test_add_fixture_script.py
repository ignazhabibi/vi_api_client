"""Tests for the maintainer script that adds a device export as a fixture."""

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest
from builders import BUNDLED_FIXTURES_DIR, load_fixture_device

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "add_fixture.py"


def _load_script() -> ModuleType:
    """Import the script as a module so its main function runs in-process."""
    spec = importlib.util.spec_from_file_location("add_fixture", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


add_fixture = _load_script()


@pytest.fixture
def fixtures_dir(tmp_path: Path) -> Path:
    """Return a fixture directory holding a copy of the bundled catalog."""
    directory = tmp_path / "fixtures"
    directory.mkdir()
    shutil.copy(BUNDLED_FIXTURES_DIR / "discovery.json", directory)
    return directory


def _write_export(tmp_path: Path, document: object) -> Path:
    """Write an export document and return its path."""
    path = tmp_path / "export.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _export(data: list, captured_at: str | None = "2026-10-08") -> dict:
    """Return an export document as `dump-device` prints it."""
    device = {"modelId": "E3_Vitocal", "deviceType": "heating"}
    if captured_at is not None:
        device["capturedAt"] = captured_at
    return {"device": device, "data": data}


# One API feature that parses into a feature named "device.name".
_NAME_FEATURE = {
    "feature": "device.name",
    "properties": {"value": {"type": "string", "value": "Heating"}},
    "commands": {},
}


def _run(export_path: Path, name: str, fixtures_dir: Path) -> int:
    """Run the script's command line entry point."""
    return add_fixture.main(
        [str(export_path), "--name", name, "--fixtures-dir", str(fixtures_dir)]
    )


def test_adds_the_fixture_file_and_a_sorted_catalog_entry(
    tmp_path, fixtures_dir, capsys
):
    # Arrange: Export the bundled Vitocal250A features.
    fixture = load_fixture_device("Vitocal250A")
    export_path = _write_export(tmp_path, _export(fixture["data"]))

    # Act: Add the export under a new name.
    exit_status = _run(export_path, "Vitocal250A-2026", fixtures_dir)

    # Assert: The fixture file matches the bundled format, and the catalog
    # lists the new device in name order.
    assert exit_status == 0
    assert (fixtures_dir / "Vitocal250A-2026.json").read_text(encoding="utf-8") == (
        BUNDLED_FIXTURES_DIR / "Vitocal250A.json"
    ).read_text(encoding="utf-8")
    devices = json.loads((fixtures_dir / "discovery.json").read_text())["devices"]
    names = [device["fixtureName"] for device in devices]
    assert names == sorted(names)
    assert {
        "fixtureName": "Vitocal250A-2026",
        "modelId": "E3_Vitocal",
        "deviceType": "heating",
        "capturedAt": "2026-10-08",
    } in devices
    assert "Added fixture Vitocal250A-2026" in capsys.readouterr().out


def test_masks_identifiers_left_in_the_export(tmp_path, fixtures_dir, capsys):
    # Arrange: Export one feature whose gateway serial is not masked.
    api_feature = {
        "feature": "device.serial",
        "gatewayId": "7630175843100101",
        "isEnabled": True,
        "isReady": True,
        "properties": {"value": {"type": "string", "value": "7630175843100101"}},
        "commands": {},
    }
    export_path = _write_export(tmp_path, _export([api_feature], captured_at=None))

    # Act: Add the export.
    exit_status = _run(export_path, "Masked", fixtures_dir)

    # Assert: The fixture is masked, a warning is shown, and the catalog entry
    # has no capture date.
    assert exit_status == 0
    written = (fixtures_dir / "Masked.json").read_text(encoding="utf-8")
    assert "7630175843100101" not in written
    assert "################" in written
    assert "Warning: redacted sensitive data" in capsys.readouterr().err
    devices = json.loads((fixtures_dir / "discovery.json").read_text())["devices"]
    assert {
        "fixtureName": "Masked",
        "modelId": "E3_Vitocal",
        "deviceType": "heating",
    } in devices


@pytest.mark.parametrize(
    ("name", "document", "message"),
    [
        pytest.param(
            "Vitocal250A", _export([]), "already exists", id="name-in-catalog"
        ),
        pytest.param("discovery", _export([]), "already exists", id="existing-file"),
        pytest.param("bad name", _export([]), "Invalid fixture name", id="bad-name"),
        pytest.param("New", {"data": {}}, "'data' list", id="data-not-a-list"),
        pytest.param("New", {"data": []}, "'device' object", id="missing-device"),
        pytest.param("New", _export(["text"]), "must be an object", id="entry-text"),
        pytest.param(
            "New",
            _export([{"feature": ""}]),
            "cannot parse the export",
            id="malformed-feature",
        ),
        pytest.param(
            "New",
            _export([_NAME_FEATURE, _NAME_FEATURE]),
            "Duplicate feature name: device.name",
            id="duplicate-feature",
        ),
    ],
)
def test_rejects_an_unusable_export_without_writing(
    tmp_path, fixtures_dir, capsys, name, document, message
):
    # Arrange: Write the export and remember the catalog.
    export_path = _write_export(tmp_path, document)
    catalog_before = (fixtures_dir / "discovery.json").read_text(encoding="utf-8")

    # Act: Try to add the export.
    exit_status = _run(export_path, name, fixtures_dir)

    # Assert: The script fails with the reason and leaves the catalog unchanged.
    assert exit_status == 1
    assert message in capsys.readouterr().err
    assert (fixtures_dir / "discovery.json").read_text(
        encoding="utf-8"
    ) == catalog_before
    assert sorted(path.name for path in fixtures_dir.iterdir()) == ["discovery.json"]


def test_rejects_an_unreadable_export(tmp_path, fixtures_dir, capsys):
    # Act: Add an export file that does not exist.
    exit_status = _run(tmp_path / "missing.json", "New", fixtures_dir)

    # Assert: The script fails and names the file.
    assert exit_status == 1
    assert "Cannot read" in capsys.readouterr().err
