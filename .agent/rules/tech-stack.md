---
trigger: always_on
---

# Tech Stack & Patterns

## 1. Python Version
- Runtime baseline is Python 3.12+.
- CI currently validates against Python 3.12.
- Prefer matching the CI interpreter locally when possible.
- Keep tooling configuration aligned with the supported runtime baseline.

## 2. Typing (Strict)
- **Avoid `Any`:** Prefer concrete types, `Protocol`, or type aliases. Use
  `Any` only at genuinely dynamic boundaries such as raw JSON payloads or
  untyped third-party interfaces.
- **Generics:** Use built-in generics (e.g., `list[str]` instead of `List[str]`).
- **Modern unions:** Use `|` instead of `Optional` / `Union` when practical.
- **Self:** Do not annotate `self` in methods.

## 3. Error Handling & Logic
- **Specific Exceptions:** Never catch a bare `Exception`. Catch specific errors
  such as `ValueError`, `FileNotFoundError`, or `ViAuthError`.
- **EAFP:** Prefer "Easier to Ask for Forgiveness than Permission" over large
  pre-check ladders when the code stays readable.
- **Custom Exceptions:** Define custom exceptions in `exceptions.py`.
- **No Leaking:** Do not expose transport-library specific exceptions from the
  public client layer when a repo exception is more appropriate.

## 4. Async, Network, and Data Models
- **Async I/O:** Network and file-adjacent workflows that already operate in the
  async client stack should stay asynchronous.
- **HTTP Stack:** Use `aiohttp` for the live client flow unless the repository is
  intentionally being re-architected.
- **Core Models:** Prefer frozen dataclasses for public library models unless a
  different structure is clearly justified.

## 5. Filesystem and Serialization
- **Pathlib Preferred:** Prefer `pathlib.Path` for new or modified filesystem
  logic.
    - Wrong: `os.path.join(a, b)`
    - Right: `Path(a) / b`
- **JSON Files:** Read and write JSON fixtures/config files with explicit UTF-8
  handling when practical.
