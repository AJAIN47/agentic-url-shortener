# Architecture Overview

## 1. System Components

The prototype is intentionally compact but follows a production-oriented architecture pattern:

- HTTP API layer: accepts link creation and redirect requests.
- URLStore: persists links and click analytics in SQLite.
- Orchestrator: governs workflow execution, gating, retries, approval, rollback, and re-planning.
- Scenario runner: exercises greenfield, brownfield, and ambiguous requirements through the same lifecycle.

## 2. Control Flow

```text
Client
  -> HTTP Handler
      -> URLStore.validate_url()
      -> SQLite (links + clicks)
      -> 201 / 302 / 404 responses

Requirement
  -> Orchestrator.run()
      -> validate DAG
      -> dependency scheduling
      -> entry gates
      -> approval checks for high-impact tasks
      -> retry / fallback / rollback
      -> exit gates and audit persistence
      -> metrics + plan lineage
```

## 3. Governance Model

The orchestration layer enforces controlled autonomy rather than unrestricted agent execution:

- explicit dependency graph
- bounded retries
- human approval checkpoints on high-impact actions
- policy checks for production and compliance constraints
- safe-stop when dependencies or cycles block progress
- decision lineage for re-planning and auditability

## 4. Reliability and Security Choices

- SQLite is used for transparency and simplicity in a single-process prototype.
- Token validation prevents unsafe or duplicate identifiers.
- URL validation rejects unsafe schemes and local/private targets to reduce SSRF risk.
- Rate limiting protects API endpoints from abuse.
- Click analytics are recorded synchronously to preserve correctness for this prototype.

## 5. Production Limitations

This remains a prototype. A production deployment would add:

- multi-instance coordination
- external DB and cache layers
- authN/authZ
- observability/metrics pipelines
- secret management and config management
- a real release workflow and policy service
