# Release v0.3.0

## Highlights

This release focuses on **developer experience** and **test infrastructure**.

## New Features

### Integration Test Suite
- Added `tests/integration/` with mock workflow tests for Vitodens and Vitocal devices.
- New `@pytest.mark.integration` marker for targeted test execution.

### New Mock Device Fixture
- Added `Vitocal250A.json` (Heat Pump) to bundled fixtures.

### Agent Workflows & Rules
- Added `.agent/workflows/release.md` for standardized release process.
- Added `.agent/workflows/doc-update.md` for documentation sync.
- Added `.agent/rules/` with coding standards (Python style, testing, tech stack).

## Improvements

### Documentation
- Updated `docs/04_models_reference.md` with missing `FeatureControl` attributes.
- Updated `docs/05_client_reference.md` with correct method signatures.
- Added Python 3.12+ requirement to README.

### Test Refactoring
- Migrated tests to use `respx` instead of `aioresponses`.
- Extracted test fixtures to `tests/fixtures/`.
- Applied Arrange-Act-Assert (AAA) pattern consistently.

## Technical Details

- 58 files changed, +8293 / -917 lines
- Test coverage: 71%

---
*Full changelog: [v0.2.1...v0.3.0](https://github.com/ignazhabibi/vi_api_client/compare/v0.2.1...v0.3.0)*
