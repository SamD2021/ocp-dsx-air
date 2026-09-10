# Document types

Use one primary Diataxis type for each page. Split a page when sections serve different reader needs and the split improves navigation.

## Tutorial

Teach a new user through one tested path. Control the starting state and give the expected result after each important step. Keep alternatives and detailed design discussion in linked pages.

## How-to guide

Help a competent user complete one goal. State prerequisites, use an ordered procedure, explain meaningful branches, and include recovery steps for expected failures.

## Reference

Describe facts with consistent structure. Mirror the public product model. For the spec reference, document each field's type, unit, default, constraint, and interaction with other fields.

## Explanation

Explain why the system has its current structure. Connect design choices to constraints and consequences. Keep commands and exhaustive option lists in linked how-to or reference pages.

## Repository documents

- `README.md` orients the reader and provides the shortest verified path to first success.
- `docs/architecture.md` explains components, dependency direction, data flow, and boundary ownership.
- `docs/lifecycle.md` explains reconciliation, resource dependencies, polling, replacement, and destruction.
- `docs/spec-reference.md` describes the complete supported YAML, JSON, and TOML contract.
- `docs/operations.md` contains goal-based procedures and failure recovery.
- `docs/development.md` explains local setup, test layers, SDK boundary tests, and live probes.

Create only documents required by the current scope. Do not create empty framework pages.
