#!/bin/bash
# Prepare Claude Code cloud sessions with Python 3.14 and the dev dependencies
# so the local quality gate (ruff, pyright, pytest, build) runs without setup.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# Cloud images ship an older uv whose download list stops at 3.14 release
# candidates, so run a recent uv for the interpreter download.
if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --quiet --user uv
  export PATH="$HOME/.local/bin:$PATH"
fi
uv_latest() { uvx --quiet --from 'uv>=0.9' uv "$@"; }

uv_latest python install 3.14
python314="$(uv_latest python find '>=3.14.0')"

if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 14))'; then
  rm -rf .venv
  "$python314" -m venv .venv
fi

.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -c constraints-ci.txt '.[dev]'

# The pyright wrapper fetches its Node runtime on first use; warm it now so the
# download is part of the cached container state.
.venv/bin/pyright --version >/dev/null

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$PWD/.venv\""
    echo "export PATH=\"$PWD/.venv/bin:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi
