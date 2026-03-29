---
description: Review and update agent guidance so rules, workflows, and bootstrap docs remain aligned with repository reality
---

# Rules and Workflow Compliance Check

**Goal:** Keep `AGENTS.md`, `.agent/rules/`, and `.agent/workflows/`
self-contained, current, and internally consistent with the real repository
behavior.

## Instructions

Act as a strict repository guidance reviewer. Audit the guidance files against
the actual codebase, docs, CI, release process, and repository boundaries, then
update them directly where needed.

### Phase 1: Repository Reality Check
1.  **Bootstrap Alignment:**
    - Verify `AGENTS.md` still points to the correct always-relevant files and
      workflows.
    - Ensure it reflects current CI, release, setup, and boundary assumptions.
2.  **Rules Alignment:**
    - Check `.agent/rules/` for stale assumptions about architecture, tech
      stack, testing, git workflow, and multi-repo boundaries.
3.  **Workflow Alignment:**
    - Check `.agent/workflows/` for stale steps around branches, PRs, local
      validation, release flow, and documentation duties.

### Phase 2: Self-Contained Documentation Check
1.  **Coverage:** Treat repository-operational documentation as one system:
    - `README.md`
    - `docs/`
    - `AGENTS.md`
    - `.agent/rules/`
    - `.agent/workflows/`
2.  **Durable Knowledge:** If a new insight changed how the repo is actually
    maintained, verify that the insight is captured in the right doc file and
    not left implicit in conversation only.
3.  **No Silent Drift:** If a guidance file is stale, update it as part of the
    task instead of merely noting the discrepancy.

### Phase 3: Required Triggers
Treat guidance updates as mandatory when the task changed any of the following:

- public API contract or model semantics
- CLI behavior or examples
- tech stack, Python baseline, or dependency strategy
- test strategy, fixture policy, or build validation expectations
- local setup commands
- release versioning or tagging flow
- Git workflow or repo-boundary policy

## Output
Apply the necessary guidance updates directly. Afterwards:

- summarize what guidance was updated
- state which files were reviewed for drift
- explicitly note if any checked files needed no change
