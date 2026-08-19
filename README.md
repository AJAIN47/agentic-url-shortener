# Agentic URL Shortener

A runnable, standard-library prototype of a URL shortener plus a governed agentic
software-engineering executor. The implementation is intentionally small enough to
review, but demonstrates the lifecycle controls required for production-oriented
autonomy: explicit dependencies, parallel execution, synchronization barriers,
approval gates, bounded retries, fallback, rollback, safe-stop, audit lineage, and
reliability metrics.

## Quick start

Requires Python 3.11 or newer. No runtime dependency is required.

Option A: run locally

```bash
python -m unittest -v
python shortener.py
```

Option B: run with Docker

```bash
docker compose up --build
```

The service listens on `http://127.0.0.1:8080` and stores data in `shortener.db`.

```bash
curl -X POST http://127.0.0.1:8080/api/links \
	-H 'Content-Type: application/json' \
	-d '{"url":"https://example.com/docs","ttl_seconds":3600}'
curl -i http://127.0.0.1:8080/<token>
curl http://127.0.0.1:8080/api/links/<token>/stats
python scenarios.py
```

## Setup instructions

1. Create and activate a virtual environment if desired.
2. Run `python -m unittest -v` from the repository root.
3. Launch the service with `python shortener.py`.
4. Exercise the API using the curl examples above.
5. Review the orchestration behavior using `python scenarios.py`.

## Requirement-to-deliverable traceability

| Requirement area | Evidence in this repo |
| --- | --- |
| Requirement understanding | [README.md](README.md), [scenarios.py](scenarios.py) |
| Task decomposition | [orchestrator.py](orchestrator.py) |
| Architecture and control flow | [docs/architecture.md](docs/architecture.md) |
| Implementation and API | [shortener.py](shortener.py) |
| Scenario coverage | [scenarios.py](scenarios.py) |
| Validation and risk control | [test_app.py](test_app.py), [test_orchestrator.py](test_orchestrator.py) |
| Final engineering summary | [docs/engineering_summary.md](docs/engineering_summary.md) |
| Deployment readiness | [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml), [docs/release-checklist.md](docs/release-checklist.md) |
| API contract | [docs/api-schema.md](docs/api-schema.md) |

## Architecture and decisions

```text
Client -> HTTP handler -> URLStore -> SQLite
                         |              |          |
                         |              +--------> clicks analytics
                         +--> redirect / JSON API

Requirement -> requirements -> architecture
                                          -> implementation [approval]
                                                      -> validation -> release [approval]
```

The standard library was chosen to make the prototype reproducible and transparent.
SQLite is appropriate for a single-process demonstration, not a multi-region service.
Tokens are short hashes by default; user-supplied tokens are validated.
Expiration is enforced at read time, and expired redirects do not increment clicks.

## Testing and validation

`test_app.py` covers URL safety, duplicate-token protection, expiry, redirect analytics,
API validation, and rate limiting. `test_orchestrator.py` covers parallel DAG execution,
dependency synchronization, approval blocking, retry behavior, and metrics. Run both with
`python -m unittest -v`.

## Production considerations and limitations

- Authentication and authorization are not yet implemented.
- Rate limits are in-process and local to a single node.
- Abuse and malware screening are left as future controls.
- High-scale deployments would require external persistence, caching, and observability.
- Real MTTR and incident telemetry are simulated rather than fed from production signal sources.

## Assumptions and trade-offs

- The prototype is a single service and trusts its deployment network for identity.
- Click analytics are synchronous for correctness and simplicity; this increases redirect latency under load.
- A process-local lock protects SQLite writes; horizontal scaling needs a shared DB.
- Orchestration checkpoints and audit are durable SQLite records for this prototype.
- Dynamic replanning versions plans and invalidates downstream tasks locally; a production implementation would add a planner service, conflict resolution, and signed approval records.

`GET /` returns a health and API-discovery document, which is also useful when
opening the service through a forwarded development-container port.

## Product API

`POST /api/links` creates a link. The JSON body requires an absolute `http` or
`https` URL and optionally accepts a URL-safe `token` and a `ttl_seconds` value
(maximum 365 days). The response is `201` with `token`, `short_url`, timestamps,
and the original URL. Invalid input returns `400`; duplicate tokens are rejected.

`GET /{token}` returns `302` and records a click with timestamp, user agent, and
client IP. Missing or expired links return `404`. `GET /api/links/{token}/stats`
returns click count and link metadata.

The SQLite schema has separate `links` and `clicks` tables, a primary-key token,
and an analytics index. Writes are protected for the threaded HTTP server. For a
larger deployment, the store should move to PostgreSQL/Redis and analytics should
be emitted asynchronously.

## Agentic orchestration

`orchestrator.py` is the control plane. A `Task` declares its dependencies,
impact level, retry budget, fallback, rollback action, entry gate, exit gate, and
policy tags. `Orchestrator` validates the graph before execution and repeatedly:

1. Selects tasks whose dependencies are complete.
2. Runs independent ready tasks concurrently, then waits at the dependency barrier.
3. Stops at a human approval checkpoint before every high-impact task.
4. Retries transient failures within the task budget, then invokes fallback or
	 fails the run. A rollback callback is attempted at the failure boundary.
5. Records an append-only audit trail with run ID, task, attempt, error, approval,
   rollback, policy, and completion events.

Run snapshots are persisted in `orchestrator.db`, including context, task states,
approvals, audit events, plan version, and decision lineage. A blocked run can be
resumed after human approval:

```python
run = orchestrator.run(requirement_context)
run = orchestrator.resume(run.id, {"implementation", "release"})
```

If an upstream output changes, `replan()` increments the plan version, records the
decision, invalidates the changed task and all descendants, and removes approvals
for impacted high-impact tasks so the new plan cannot bypass human review:

```python
run = orchestrator.replan(run.id, {"architecture_revision": "v2"}, {"architecture"})
```

A missing dependency or deadlock triggers safe-stop. `Orchestrator.metrics()`
reports success rate, retry count, rollback count, end-to-end latency, MTTR proxy,
plan version, and audit-event count.

This is controlled autonomy, not an unrestricted agent loop. Agent actions should
be represented by task functions and policy checks; high-impact changes require an
approval token supplied to `run(..., approvals={...})`. The built-in policy engine
rejects failed security scans, retention outside 1-365 days, and production tasks
without a change ticket. Scenario tasks also use entry and exit gates. In production,
identity/authorization, secrets handling, and approval identity must be integrated
with enterprise services.

## Three required scenarios

`scenarios.py` gives each scenario the same reviewable lifecycle while changing the
input requirement:

| Scenario | Requirement interpretation | Validation focus |
| --- | --- | --- |
| Greenfield | Build a URL shortener with analytics | API, persistence, and release gates |
| Brownfield | Add expiration and analytics without breaking redirects | Compatibility and regression validation |
| Ambiguous | Make links safe and fast for enterprise users | Captured ambiguities: retention and authentication |

Each run produces task states and audit events. The implementation and release
tasks are explicitly approved in the demo. Remove either approval to observe the
blocked/safe-stop behavior.

## Architecture and decisions

```text
Client -> HTTP handler -> URLStore -> SQLite
						 |              |          |
						 |              +--------> clicks analytics
						 +--> redirect / JSON API

Requirement -> requirements -> architecture
															-> implementation [approval]
																	 -> validation -> release [approval]
```

The standard library was chosen to make the prototype reproducible and transparent.
SQLite is appropriate for a single-process demonstration, not a multi-region
service. Tokens are short hashes by default; user-supplied tokens are validated.
Expiration is enforced at read time, and expired redirects do not increment clicks.

## Testing and validation

`test_app.py` covers URL safety, duplicate-token protection, expiry, redirect
analytics, and the data contract. `test_orchestrator.py` covers parallel DAG
execution, dependency synchronization, approval blocking, retry behavior, and
metrics. Run both with `python -m unittest -v`.

Important remaining production work: authentication and rate limiting, abuse and
malware screening, structured logs and distributed tracing, load testing, multi-
instance coordination, key management, data retention/deletion workflows, and a
real MTTR calculation from incident timestamps. These are explicit limitations
rather than hidden claims of production readiness.

## Assumptions and trade-offs

- The prototype is a single service and trusts its deployment network for identity.
- Click analytics are synchronous for correctness and simplicity; this increases
	redirect latency under load.
- A process-local lock protects SQLite writes; horizontal scaling needs a shared DB.
- Orchestration checkpoints and audit are durable SQLite records for this prototype;
	a production coordinator would use a transactional shared store and tamper-evident
	export for audit retention.
- Dynamic replanning versions plans and invalidates downstream tasks locally; a
	production implementation should add a planner service, conflict resolution, and
	signed approval records.
