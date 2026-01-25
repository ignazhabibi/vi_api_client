---
trigger: always_on
---

# Tech Stack & Patterns

## 1. Python Version
- Target Python 3.12+ (Use features like `match/case` and `|` union operator).

## 2. Typing (Strict)
- **No Any:** Avoid `Any` at all costs. Use strict typing (`TypeVar`, `Protocol`).
- **Generics:** Use built-in generics (e.g., `list[str]` instead of `List[str]`).
- **Self:** Do not annotate `self` in methods.

## 3. Error Handling & Logic
- **Specific Exceptions:** NEVER catch a bare `Exception`. Catch specific errors (e.g., `ValueError`, `FileNotFoundError`).
- **EAFP:** Prefer "Easier to Ask for Forgiveness than Permission" (try/except) over extensive `if` checks where Pythonic.
- **Custom Exceptions:** Define custom exceptions in `exceptions.py`.
- **No Leaking:** Do not raise HTTP-specific exceptions (like `HTTPException`) in the service/library layer. Keep the core logic clean.

## 4. Filesystem
- **Pathlib:** Always use `pathlib.Path`.
    - ❌ Wrong: `os.path.join(a, b)`
    - ✅ Right: `pathlib.Path(a) / b`

## 6. Data Validation
- Use `pydantic` (v2) for internal data models.
