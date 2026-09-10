# Evidence and factual review

Build a claim inventory before writing substantial documentation. Keep it in a temporary file unless the user requests a permanent record.

Use this evidence order:

1. Current production code and public contracts
2. Tests that exercise the documented behavior
3. Current CLI help and validated configuration models
4. Repository issues and explicit user requirements
5. Primary external documentation
6. Dated live observations

Do not use an old design document as proof of current behavior. Compare it with the implementation first.

Record each claim with this shape:

| Claim | Evidence | Verification | Confidence |
|---|---|---|---|
| Exact statement for the reader | File, symbol, test, command, or authoritative URL | How the source proves the statement | Confirmed or qualified |

Inspect all code paths that affect a claim. For example, a deletion-order claim requires the workflow, port, adapter, and relevant tests. A configuration claim requires the Pydantic model, resolution code, and example generation path.

For commands and options, inspect `ocp-air --help` and the applicable subcommand help. Run read-only examples when possible. Do not run deployment, replacement, destruction, or another remote mutation without authorization from the current conversation.

For code examples:

- parse YAML examples with the repository's loader when practical;
- use synthetic credential references;
- verify Python examples against the installed project version;
- preserve exact enum values, units, defaults, and field names;
- distinguish a minimum supported value from a repository default or recommendation.

During final review, check both directions. Every documentation claim must have a source. Every material implemented user behavior in the requested scope must appear in the appropriate document or be intentionally omitted for a stated reason.
