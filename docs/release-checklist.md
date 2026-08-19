# Release Readiness Checklist

## Functional

- [x] URL validation rejects unsafe schemes and local/private targets
- [x] Short-link creation works
- [x] Redirect returns `302` to original URL
- [x] Click analytics are tracked
- [x] Expiry enforcement works
- [x] Duplicate tokens are rejected

## Orchestration

- [x] Tasks support dependencies
- [x] Approval gates block high-impact tasks
- [x] Retry logic handles transient failures
- [x] Runtime rollback is supported
- [x] Safe-stop is triggered on deadlock and unresolved dependencies
- [x] Replanning invalidates downstream tasks and preserves lineage

## Quality and risk

- [x] Automated tests cover API and orchestration behavior
- [x] Security guardrails exist for obvious SSRF patterns
- [x] Rate limiting reduces abuse risk
- [ ] AuthN/AuthZ integration for end users and operators
- [ ] Observability pipeline for logs, traces, and metrics
- [ ] Production-grade DB and cache scaling
- [ ] Operational runbooks and incident response process

## Deployment

- [x] Docker image definition exists
- [ ] Kubernetes / orchestrator deployment manifests
- [ ] TLS and reverse proxy configuration
- [ ] Secret and environment management
- [ ] CI/CD gate for tests and policy checks
