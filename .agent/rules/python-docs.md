---
trigger: always_on
---

# Documentation Standards

## 1. File Headers
- Every file must start with a docstring describing its purpose.
- Example: `"""Support for MQTT lights."""`

## 2. Function/Class Docstrings
- **Style:** Follow the **Google Style Guide**.
- **Public API:** Required for all public classes and methods.
- **Type Redundancy:** Do NOT repeat type information in the docstring if it is already present in the function signature.
    - ❌ Wrong: `param1 (str): The name.`
    - ✅ Right: `param1: The name.`

## 3. Content
- Describe the "Why" and "What", not just the "How".
- Mention raised exceptions in a `Raises:` section.
