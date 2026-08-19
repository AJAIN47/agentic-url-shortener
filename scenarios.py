"""Demonstrations for greenfield, brownfield, and ambiguous requirements."""
from __future__ import annotations

from orchestrator import Orchestrator, PolicyViolation, Task


def build_scenario(kind: str, state_path: str = "orchestrator.db") -> Orchestrator:
    if kind not in {"greenfield", "brownfield", "ambiguous"}:
        raise ValueError("scenario must be greenfield, brownfield, or ambiguous")

    def requirements(context: dict) -> dict:
        details = {
            "greenfield": {
                "ambiguities": ["retention", "authentication"],
                "acceptance_criteria": ["create links", "redirect", "record clicks"],
            },
            "brownfield": {
                "ambiguities": ["existing token compatibility", "analytics retention"],
                "impacted_modules": ["URLStore.resolve", "links schema", "redirect contract"],
            },
            "ambiguous": {
                "ambiguities": ["safe means abuse screening or access control", "fast means latency target"],
                "clarification_questions": ["What users and threat model are in scope?", "What is the p95 latency target?"],
            },
        }[kind]
        return {"intent": context["requirement"], **details}

    def requirements_entry(context: dict) -> None:
        if not context.get("requirement", "").strip():
            raise PolicyViolation("requirements entry gate requires a non-empty requirement")

    def architecture(context: dict) -> dict:
        return {"components": ["HTTP API", "SQLite store", "analytics"], "decision_lineage": ["stdlib", "bounded TTL"]}

    def implement(context: dict) -> str:
        return "implementation plan accepted"

    def validate(context: dict) -> str:
        return "tests and contract checks passed"

    def validation_exit(context: dict) -> None:
        if context.get("security_scan") == "failed":
            raise PolicyViolation("security policy rejected failed validation")

    def release(context: dict) -> str:
        return "release candidate ready"

    tasks = [
        Task("requirements", requirements, entry_gate=requirements_entry),
        Task("architecture", architecture, {"requirements"}),
        Task("implementation", implement, {"architecture"}, high_impact=True, rollback=lambda c: c.update({"release": "reverted"})),
        Task("validation", validate, {"implementation"}, exit_gate=validation_exit),
        Task("release", release, {"validation"}, high_impact=True, policy_tags={"production"}),
    ]
    return Orchestrator(tasks, state_path=state_path)


def run_scenarios() -> None:
    requirements = {
        "greenfield": "Build a URL shortener with analytics.",
        "brownfield": "Add expiration and click analytics without breaking redirects.",
        "ambiguous": "Make links safe and fast for enterprise users.",
    }
    for kind, requirement in requirements.items():
        run = build_scenario(kind).run(
            {"requirement": requirement, "change_ticket": f"DEMO-{kind.upper()}"},
            {"implementation", "release"},
        )
        print(kind, run.status.value, Orchestrator.metrics(run))
        print("  audit events:", len(run.audit), "tasks:", run.task_states)


if __name__ == "__main__":
    run_scenarios()
