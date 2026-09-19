"""Verify the measured test coverage meets the repository thresholds."""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COVERAGE_FILE = PROJECT_ROOT / "coverage.xml"
MINIMUM_RATE = 0.95


def _module_rates(root: ElementTree.Element) -> list[tuple[str, float, float]]:
    """Return per-module line and branch rates from the coverage report."""
    return [
        (
            module.get("filename", "<unknown>"),
            float(module.get("line-rate", "0")),
            float(module.get("branch-rate", "0")),
        )
        for module in root.iter("class")
    ]


def main() -> int:
    """Fail when the measured line or branch coverage is below the threshold."""
    if not COVERAGE_FILE.exists():
        print(f"Coverage report not found: {COVERAGE_FILE}", file=sys.stderr)
        print("Run 'python -m pytest -q' before this check.", file=sys.stderr)
        return 1

    root = ElementTree.parse(COVERAGE_FILE).getroot()
    line_rate = float(root.get("line-rate", "0"))
    branch_rate = float(root.get("branch-rate", "0"))

    print(f"Line coverage: {line_rate:.1%} (required: >= {MINIMUM_RATE:.0%})")
    print(f"Branch coverage: {branch_rate:.1%} (required: >= {MINIMUM_RATE:.0%})")

    if line_rate >= MINIMUM_RATE and branch_rate >= MINIMUM_RATE:
        return 0

    print("\nModules below the threshold:", file=sys.stderr)
    for name, line, branch in _module_rates(root):
        if line < MINIMUM_RATE or branch < MINIMUM_RATE:
            print(f"  {name}: line {line:.1%}, branch {branch:.1%}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
