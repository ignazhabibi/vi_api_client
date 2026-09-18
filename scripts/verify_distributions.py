"""Verify the built distributions ship the typed package contract."""

from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "vi_api_client"
PYRIGHT_PIN = "pyright==1.1.411"


def _run(command: list[str], **kwargs: object) -> None:
    """Run one verification command and stop with its message on failure."""
    result = subprocess.run(command, check=False, **kwargs)
    if result.returncode:
        raise SystemExit(f"Command failed: {' '.join(command)}")


def _single_artifact(pattern: str) -> Path:
    """Return the one built artifact matching the pattern."""
    artifacts = sorted((PROJECT_ROOT / "dist").glob(pattern))
    if len(artifacts) != 1:
        raise SystemExit(
            f"Expected exactly one {pattern} artifact in dist/; "
            f"found {len(artifacts)}. Clean dist/ and rebuild."
        )
    return artifacts[0]


def _assert_typed_contents(names: set[str]) -> None:
    """Require the PEP 561 marker and bundled fixtures in archive contents."""
    if not any(name.endswith(f"{PACKAGE}/py.typed") for name in names):
        raise SystemExit("Artifact does not contain py.typed")
    if not any(f"{PACKAGE}/fixtures/" in name for name in names):
        raise SystemExit("Artifact does not contain the bundled device fixtures")


def _assert_wheel_contents(wheel: Path) -> None:
    """Require the typed package contents in the wheel."""
    with zipfile.ZipFile(wheel) as archive:
        _assert_typed_contents(set(archive.namelist()))


def _assert_sdist_contents(sdist: Path) -> None:
    """Require the typed package contents in the source distribution."""
    with tarfile.open(sdist) as archive:
        _assert_typed_contents(set(archive.getnames()))


def _verify_installed_artifact(artifact: Path) -> None:
    """Install one built artifact in an isolated environment and verify it.

    Installing the source distribution builds it from its own packaging
    files, so a broken build path in the shipped source cannot pass.
    """
    with tempfile.TemporaryDirectory(prefix="vi-verifytypes-") as temp_dir:
        venv_dir = Path(temp_dir) / "venv"
        venv.create(venv_dir, with_pip=True)
        python = str(venv_dir / "bin" / "python")
        _run([python, "-m", "pip", "install", "--quiet", str(artifact), PYRIGHT_PIN])

        # The consumer-level smoke check proves the bundled fixtures ship.
        _run(
            [
                python,
                "-c",
                "from vi_api_client import FixtureViClient; "
                "assert FixtureViClient.get_available_fixture_devices()",
            ]
        )

        # pyright resolves the environment from PATH, so the isolated
        # interpreter must be first while verifytypes inspects the artifact.
        environment = {
            **os.environ,
            "PATH": f"{venv_dir / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        result = subprocess.run(
            [python, "-m", "pyright", "--verifytypes", PACKAGE, "--ignoreexternal"],
            cwd=temp_dir,
            env=environment,
            check=False,
        )
        if result.returncode:
            raise SystemExit(
                "pyright --verifytypes reported an incomplete public type contract"
            )


def main() -> int:
    """Verify the wheel and source distribution as consumer artifacts."""
    wheel = _single_artifact("*.whl")
    sdist = _single_artifact("*.tar.gz")
    _assert_wheel_contents(wheel)
    _assert_sdist_contents(sdist)
    _verify_installed_artifact(wheel)
    _verify_installed_artifact(sdist)
    print("Typed distribution verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
