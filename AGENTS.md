# Agentic Engineering Standards

This repository is evaluated as a prototype for an agentic software engineering assignment. The implementation should favor clarity, explicit control flow, and reviewability.

## Required engineering behaviors

- Interpret the requirement and capture ambiguities before implementation.
- Decompose the work into dependencies, sequencing, and validation stages.
- Keep execution state explicit and auditable.
- Enforce approval gates for high-impact actions.
- Support bounded retries, rollback, and safe-stop behavior.
- Maintain traceability across task states and decision lineage.
- Validate using real unit tests and API-level checks.
- Record known limitations and production gaps honestly.

## Review standard

Before completion, verify that:

1. The URL shortener works end-to-end.
2. The orchestration model handles dependencies and approval gates.
3. Tests cover both service-level and orchestration-level behavior.
4. Documentation explains architecture, setup, trade-offs, and assumptions.
5. Remaining gaps are documented rather than hidden.
