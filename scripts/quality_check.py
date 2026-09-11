"""Run the repository's complete local quality gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

COMMANDS = (
    (sys.executable, "-m", "ruff", "check", "."),
    (sys.executable, "-m", "ruff", "format", "--check", "."),
    (sys.executable, "-m", "pyright", "--pythonpath", sys.executable),
    (sys.executable, "-m", "pytest", "-q"),
    (sys.executable, "-m", "build"),
)


def main() -> int:
    """Run every quality command and stop at the first failure."""
    for command in COMMANDS:
        result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
