---
name: ocp-dsx-air-docs
description: Create or revise user, architecture, reference, and operations documentation for ocp-dsx-air from current code, tests, CLI behavior, and authoritative sources. Use for README work, design explanations, runbooks, configuration reference, and documentation reviews in this repository.
---

# OCP DSX Air documentation

Write documentation that a user can follow and a maintainer can verify.

## Workflow

1. Identify the intended reader and the task that the document must support.
2. Read [evidence.md](references/evidence.md). Build a claim inventory before drafting.
3. Classify each document as a tutorial, how-to guide, reference, or explanation. Read [documents.md](references/documents.md) for the selected type.
4. Inspect the current documentation layout. Update an existing page when it already serves the reader's task.
5. Draft from the claim inventory. Keep commands, field names, defaults, states, and resource order consistent with the implementation.
6. Read [style.md](references/style.md). Apply its Red Hat and plain-language rules to the draft.
7. Validate every local link with `scripts/check_markdown_links.py`. Run each safe command example or verify it through current tests and CLI help.
8. Review the documentation diff. Confirm that each claim has evidence and that each linked path exists.

Follow applicable workflow instructions from the repository and developer environment. This skill does not require a specific version-control client or command wrapper.

## Boundaries

- Treat current code, tests, and observed CLI help as the product contract.
- Use primary external documentation for facts about OpenShift, Assisted Installer, NVIDIA Air, NVIDIA hardware, and third-party tools.
- Label a live observation with its date and tested version when it is not an implemented contract.
- Preserve uncertainty. Do not convert an inference into a fact.
- Link only to files, commands, options, and pages that exist when the change is written.
- Do not inspect credential contents or include real credentials, signed URLs, response bodies, or private configuration values.
- Do not retain temporary evidence artifacts in the final change unless the user requests them.

## Completion

Documentation is complete when:

- each technical claim maps to current evidence;
- each procedure has a verified starting state, command sequence, expected result, and recovery path where applicable;
- local links resolve and external links target authoritative pages;
- examples use valid repository syntax and safe placeholder values;
- the text follows the selected document type and the project style;
- the final change contains only the intended documentation work.
